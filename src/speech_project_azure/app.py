"""Use-cases gluing the services together: the app's business rules.

Sync functions, no printing and no SDK types — interaction adapters (CLI,
Telegram) call these and render the results. Typed errors are part of the
contract: callers catch NoSpeechDetected / AssessmentCanceled /
QuotaExceededError / TextValidationError and turn them into messages.
"""

from datetime import UTC, datetime
from pathlib import Path

from langdetect import DetectorFactory, detect
from langdetect.lang_detect_exception import LangDetectException

from speech_project_azure import exercises as catalog
from speech_project_azure import settings
from speech_project_azure.domain import Attempt, TextValidationError, User
from speech_project_azure.services import azure_assessment, quota
from speech_project_azure.services.quota import QuotaStatus
from speech_project_azure.services.storage import ExerciseStat, Storage, UserStats

# Average pronunciation score below which an exercise lands in the mistake bank.
WEAK_SCORE_THRESHOLD = 70.0

# langdetect is probabilistic; fix the seed so results are reproducible.
DetectorFactory.seed = 0


def validate_custom_text(text: str) -> None:
    """Raise TextValidationError unless text is a usable English exercise."""
    normalized = " ".join(text.split())
    if not normalized:
        raise TextValidationError("The text is empty — write something first.")
    word_count = len(normalized.split(" "))
    if word_count < settings.CUSTOM_TEXT_MIN_WORDS:
        raise TextValidationError(
            f"Too short: {word_count} words. Use at least "
            f"{settings.CUSTOM_TEXT_MIN_WORDS} words."
        )
    if word_count > settings.CUSTOM_TEXT_MAX_WORDS:
        raise TextValidationError(
            f"Too long: {word_count} words. Use at most "
            f"{settings.CUSTOM_TEXT_MAX_WORDS} words."
        )
    try:
        detected = detect(normalized)
    except LangDetectException:
        raise TextValidationError(
            "Could not detect the language — English texts only."
        ) from None
    if detected != "en":
        raise TextValidationError(
            f"Detected language is '{detected}' — English texts only."
        )


def list_exercises(storage: Storage, user: User) -> list[catalog.Exercise]:
    """Builtin catalog plus the user's own custom exercises."""
    return storage.list_exercises_for_user(user.id or 0)


def mistake_bank(storage: Storage, user: User) -> list[ExerciseStat]:
    """Exercises with an average pronunciation score below the threshold.

    Weakness is derived from attempts, never stored: a low-score attempt
    enters the bank automatically (its exercise row exists and the average
    drops), and practicing it well sinks it back out.
    """
    return [
        stat
        for stat in storage.exercise_score_stats(user.id or 0)
        if stat.avg_pronunciation_score < WEAK_SCORE_THRESHOLD
    ]


def app_stats(storage: Storage, user: User) -> tuple[UserStats, QuotaStatus]:
    """Everything the /stats screen needs in one call."""
    return storage.user_stats(user.id or 0), quota.quota_status(storage)


def record_attempt(
    storage: Storage,
    user: User,
    exercise: catalog.Exercise,
    audio: Path | None = None,
) -> Attempt:
    """Quota gate → Azure assessment → stored attempt.

    Silent failures are logged to the quota ledger here (Azure bills them)
    and then re-raised so the caller can render a message.
    """
    quota.ensure_can_assess(storage)
    try:
        report, audio_seconds = azure_assessment.assess(audio, exercise.reference_text)
    except azure_assessment.NoSpeechDetected as error:
        storage.add_audio_usage(user.id or 0, error.audio_seconds, "no_speech")
        raise
    except azure_assessment.AssessmentCanceled as error:
        storage.add_audio_usage(user.id or 0, error.audio_seconds, "canceled")
        raise
    return storage.add_attempt(
        Attempt(
            user_id=user.id or 0,
            exercise_id=exercise.id,
            reference_text=exercise.reference_text,
            created_at=datetime.now(UTC),
            audio_seconds=audio_seconds,
            report=report,
        )
    )


def assess_custom_text(
    storage: Storage,
    user: User,
    text: str,
    audio: Path | None = None,
) -> Attempt:
    """Validate user text, persist it as a custom exercise, assess it."""
    validate_custom_text(text)
    exercise = storage.add_custom_exercise(user.id or 0, text)
    return record_attempt(storage, user, exercise, audio)
