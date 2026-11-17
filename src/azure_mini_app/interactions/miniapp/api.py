"""JSON API for the Mini App: one route per use-case, errors as typed codes.

Routes are thin: they authenticate (auth.py), dispatch sync use-cases via
asyncio.to_thread, serialize domain dataclasses to plain dicts, and map
typed errors to HTTP status codes with a stable `code` field the frontend
switches on. No rendering or business rules here.
"""

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from azure_mini_app import app, settings
from azure_mini_app.domain import TextValidationError, User
from azure_mini_app.interactions.miniapp.access import (
    notify_admins_pending,
    register_access,
)
from azure_mini_app.interactions.miniapp.auth import (
    MiniAppAuthError,
    MiniAppUser,
    claimed_telegram_id,
    validate_init_data,
)
from azure_mini_app.services.azure_assessment import (
    AssessmentCanceled,
    AssessmentError,
    NoSpeechDetected,
)
from azure_mini_app.services.quota import QuotaExceededError
from azure_mini_app.services.storage import ExerciseStat, Storage, UserStats
from azure_mini_app.services.voice import VoiceConversionError, to_wav

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

MAX_RECORD_SECONDS = 120  # same guardrail as the chat voice-note flow
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # ~10 MB WAV-equivalent is plenty

# Auth-failure throttle policy (ticket 06): five failures inside a
# sliding minute per client IP. A legitimate user fails once (stale
# initData) and reopens the app; a prober fails continuously.
AUTH_FAILURE_LIMIT = 5
AUTH_FAILURE_WINDOW_SECONDS = 60.0

AUTH_FAILED_MESSAGE = "Authentication failed — reopen the Mini App and try again."
THROTTLED_MESSAGE = "Too many attempts — wait a minute and try again."


# --- request dependencies -----------------------------------------------------


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _deny(
    request: Request,
    *,
    code: str,
    message: str,
    reason: str,
    claimed: int | None,
) -> HTTPException:
    """Count the denial (audit + throttle), then answer it.

    Once the window is tripped the answer is the generic 429 — the
    caller learns nothing about whether the signature was even close.
    Valid credentials never reach here; they clear the bucket instead.
    """
    ip = _client_ip(request)
    if request.app.state.auth_throttle.is_limited(ip):
        request.app.state.storage.record_auth_denial(
            ip=ip, reason="throttled", claimed_telegram_id=claimed
        )
        return _fail(429, "throttled", THROTTLED_MESSAGE)
    request.app.state.storage.record_auth_denial(
        ip=ip, reason=reason, claimed_telegram_id=claimed
    )
    request.app.state.auth_throttle.record_failure(ip)
    return _fail(401, code, message)


def get_mini_app_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> MiniAppUser:
    """initData ("Authorization: tma <raw>") or, in dev mode, the fixed dev user.

    Hardened boundary (ticket 06): validation comes first, so valid
    credentials are never throttled — and clear the client's failure
    bucket (a NAT-sharing user must not inherit a prober's lockout).
    Every failure counts toward a per-client-IP sliding window and is
    audited; bodies carry stable codes only, internal reasons go to
    the server log and the audit table.
    """
    ip = _client_ip(request)
    if authorization and authorization.startswith("tma "):
        raw = authorization.removeprefix("tma ")
        try:
            identity = validate_init_data(raw)
        except MiniAppAuthError as error:
            logger.warning("initData rejected from %s: %s", ip, error)
            raise _deny(
                request,
                code="auth_failed",
                message=AUTH_FAILED_MESSAGE,
                reason=str(error),
                claimed=claimed_telegram_id(raw),
            ) from None
        request.app.state.auth_throttle.clear(ip)
        return identity
    if settings.miniapp_dev_mode:
        return MiniAppUser(
            telegram_id=settings.miniapp_dev_user_id,
            display_name=settings.miniapp_dev_user_name,
        )
    raise _deny(
        request,
        code="auth_required",
        message="Missing Telegram initData — open the Mini App from Telegram.",
        reason="no credentials",
        claimed=None,
    )


PENDING_MESSAGE = (
    "Your access request has been recorded — reopen the app once an admin approves you."
)
BLOCKED_MESSAGE = "Your access to this trainer was blocked."


async def get_user(
    storage: Annotated[Storage, Depends(get_storage)],
    identity: Annotated[MiniAppUser, Depends(get_mini_app_user)],
) -> User:
    """Resolve the identity against the access database; the gate lives here.

    Strangers self-register as pending (admins get notified once),
    pending and blocked callers get typed 403s, active callers pass —
    checked on every request, so a block takes effect mid-session.
    """
    registration = await asyncio.to_thread(register_access, storage, identity)
    if registration.entry.status == "pending":
        if registration.created:
            # first sight: tell the admins before answering the stranger
            await notify_admins_pending(registration.user)
        raise _fail(403, "pending", PENDING_MESSAGE)
    if registration.entry.status == "blocked":
        raise _fail(403, "blocked", BLOCKED_MESSAGE)
    return registration.user


def _fail(status: int, code: str, message: str, **extra) -> HTTPException:
    """Typed error → HTTP error with a stable `code` the frontend maps."""
    return HTTPException(
        status_code=status, detail={"code": code, "message": message, **extra}
    )


def _exercise_json(exercise) -> dict:
    return {
        "id": exercise.id,
        "title": exercise.title,
        "reference_text": exercise.reference_text,
        "category": exercise.category,
        "level": exercise.level,
        "phoneme_focus": sorted(exercise.phoneme_focus),
    }


def _stat_json(stat: ExerciseStat) -> dict:
    return {
        "exercise": _exercise_json(stat.exercise),
        "avg_pronunciation_score": stat.avg_pronunciation_score,
        "attempt_count": stat.attempt_count,
        "last_attempted": stat.last_attempted.isoformat(),
    }


def _user_stats_json(stats: UserStats) -> dict:
    return {
        "user_id": stats.user_id,
        "total_attempts": stats.total_attempts,
        "total_audio_seconds": stats.total_audio_seconds,
        "avg_pronunciation_score": stats.avg_pronunciation_score,
        "avg_accuracy_score": stats.avg_accuracy_score,
    }


def _attempt_json(attempt) -> dict:
    report = attempt.report
    return {
        "id": attempt.id,
        "exercise_id": attempt.exercise_id,
        "reference_text": attempt.reference_text,
        "created_at": attempt.created_at.isoformat(),
        "audio_seconds": attempt.audio_seconds,
        "report": {
            "recognized_text": report.recognized_text,
            "accuracy_score": report.accuracy_score,
            "fluency_score": report.fluency_score,
            "completeness_score": report.completeness_score,
            "prosody_score": report.prosody_score,
            "pronunciation_score": report.pronunciation_score,
            "words": [
                {
                    "word": word.word,
                    "accuracy_score": word.accuracy_score,
                    "error_type": word.error_type,
                    "phonemes": [[phoneme, score] for phoneme, score in word.phonemes],
                }
                for word in report.words
            ],
        },
    }


def _quota_json(status) -> dict:
    return {
        "used_seconds": status.used_seconds,
        "limit_seconds": status.limit_seconds,
        "remaining_seconds": status.remaining_seconds,
        "fraction_used": status.fraction_used,
        "warn": status.warn,
        "exhausted": status.exhausted,
    }


# --- identity & catalog -------------------------------------------------------


@router.get("/me")
async def me(
    user: Annotated[User, Depends(get_user)],
    identity: Annotated[MiniAppUser, Depends(get_mini_app_user)],
) -> dict:
    return {
        "user": {
            "id": user.id,
            "display_name": user.display_name,
            "telegram_id": user.telegram_id,
        },
        "dev_mode": settings.miniapp_dev_mode,
    }


@router.get("/exercises")
async def list_exercises(
    storage: Annotated[Storage, Depends(get_storage)],
    user: Annotated[User, Depends(get_user)],
) -> dict:
    exercises = await asyncio.to_thread(app.list_exercises, storage, user)
    return {"exercises": [_exercise_json(exercise) for exercise in exercises]}


@router.post("/texts")
async def add_custom_text(
    body: dict,
    storage: Annotated[Storage, Depends(get_storage)],
    user: Annotated[User, Depends(get_user)],
) -> dict:
    text = " ".join(str(body.get("text", "")).split())
    try:
        app.validate_custom_text(text)
    except TextValidationError as error:
        raise _fail(400, "text_invalid", str(error)) from None
    exercise = await asyncio.to_thread(storage.add_custom_exercise, user.id or 0, text)
    return {"exercise": _exercise_json(exercise)}


# --- practice -----------------------------------------------------------------


@router.post("/assess")
async def assess(
    request: Request,
    storage: Annotated[Storage, Depends(get_storage)],
    user: Annotated[User, Depends(get_user)],
    exercise_id: str | None = None,
    text: str | None = None,
) -> dict:
    """Raw audio bytes in (application/octet-stream), attempt JSON out.

    Metadata rides in query params (`exercise_id` for catalog exercises,
    `text` for custom texts) and the `X-Duration-Seconds` header.
    """
    audio = await request.body()
    if not audio:
        raise _fail(400, "audio_missing", "Send the recording as the request body.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise _fail(
            413,
            "audio_too_large",
            f"Recording exceeds {MAX_AUDIO_BYTES // 1024 // 1024} MB.",
        )
    try:
        duration = float(request.headers.get("X-Duration-Seconds", "0"))
    except ValueError:
        duration = 0.0
    if duration > MAX_RECORD_SECONDS:
        raise _fail(
            413,
            "record_too_long",
            f"That recording is {duration:.0f}s — keep it under {MAX_RECORD_SECONDS}s.",
        )

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "recording.webm"
        source.write_bytes(audio)
        try:
            wav_path = await asyncio.to_thread(to_wav, source, Path(tmp))
            if text is not None:
                attempt = await asyncio.to_thread(
                    app.assess_custom_text,
                    storage,
                    user,
                    " ".join(text.split()),
                    wav_path,
                )
            else:
                if exercise_id is None:
                    raise _fail(
                        400, "no_target", "Pass exercise_id or text for assessment."
                    )
                exercise = await asyncio.to_thread(storage.get_exercise, exercise_id)
                if exercise is None:
                    raise _fail(
                        404, "exercise_not_found", "That exercise no longer exists."
                    )
                attempt = await asyncio.to_thread(
                    app.record_attempt, storage, user, exercise, wav_path
                )
        except TextValidationError as error:
            raise _fail(400, "text_invalid", str(error)) from None
        except NoSpeechDetected as error:
            raise _fail(
                422,
                "no_speech",
                "I didn't hear anything — hold the mic button and read aloud.",
                audio_seconds=error.audio_seconds,
            ) from None
        except AssessmentCanceled as error:
            raise _fail(
                502,
                "assessment_canceled",
                f"The service canceled the assessment: {error}",
                audio_seconds=error.audio_seconds,
            ) from None
        except QuotaExceededError as error:
            raise _fail(
                403,
                "quota_exceeded",
                "Monthly audio budget is exhausted — practice resets on the 1st.",
                quota=_quota_json(error.status),
            ) from None
        except VoiceConversionError as error:
            raise _fail(400, "voice_error", str(error)) from None
        except AssessmentError as error:
            raise _fail(
                502, "assessment_error", f"Assessment problem: {error}"
            ) from None

    return {"attempt": _attempt_json(attempt)}


# --- reports ------------------------------------------------------------------


@router.get("/mistakes")
async def mistakes(
    storage: Annotated[Storage, Depends(get_storage)],
    user: Annotated[User, Depends(get_user)],
) -> dict:
    stats = await asyncio.to_thread(app.mistake_bank, storage, user)
    return {"mistakes": [_stat_json(stat) for stat in stats]}


@router.get("/stats")
async def stats(
    storage: Annotated[Storage, Depends(get_storage)],
    user: Annotated[User, Depends(get_user)],
) -> dict:
    user_stats, quota_status = await asyncio.to_thread(app.app_stats, storage, user)
    return {"stats": _user_stats_json(user_stats), "quota": _quota_json(quota_status)}
