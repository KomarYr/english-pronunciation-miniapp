"""Mini App API tests: auth wiring, use-case mapping, typed error codes.

azure_assessment.assess is monkeypatched — no Azure, no Telegram, no
bot token; requests authenticate through MINIAPP_DEV_MODE.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from azure_mini_app import settings
from azure_mini_app.domain import ScoreReport
from azure_mini_app.services import azure_assessment, quota
from azure_mini_app.services.azure_assessment import (
    AssessmentCanceled,
    NoSpeechDetected,
)
from azure_mini_app.services.quota import QuotaExceededError, QuotaStatus
from azure_mini_app.services.voice import VoiceConversionError

DEV_ID = 900000001
ENGLISH_TEXT = "She sells six silver shells by the shiny seashore every sunny morning."

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def fake_report(pronunciation: float = 89.0) -> ScoreReport:
    return ScoreReport(
        recognized_text="She sells six silver shells.",
        accuracy_score=92.0,
        fluency_score=88.0,
        completeness_score=100.0,
        prosody_score=75.0,
        pronunciation_score=pronunciation,
        words=(),
    )


@pytest.fixture()
def api_client(monkeypatch, tmp_path):
    """App without Telegram wiring: .env holds a real token + public URL,
    which would make the lifespan call the real set_webhook."""
    make_app(monkeypatch, tmp_path)
    from azure_mini_app.interactions.miniapp.web import create_app

    with TestClient(create_app()) as client:
        yield client


def make_app(
    monkeypatch,
    tmp_path,
    *,
    dev_mode: bool = True,
    allowed: frozenset[int] = frozenset({DEV_ID}),
) -> None:
    monkeypatch.setattr(settings, "db_path", tmp_path / "api-test.db")
    monkeypatch.setattr(settings, "miniapp_dev_mode", dev_mode)
    monkeypatch.setattr(settings, "allowed_telegram_ids", allowed)
    monkeypatch.setattr(settings, "telegram_bot_token", None)
    monkeypatch.setattr(settings, "miniapp_public_url", None)


def patch_assess(monkeypatch, result):
    calls = []

    def fake_assess(audio, reference_text, language="en-US"):
        calls.append(reference_text)
        if isinstance(result, Exception):
            raise result
        return result, 5.0

    monkeypatch.setattr(azure_assessment, "assess", fake_assess)
    return calls


@pytest.fixture()
def fake_wav(monkeypatch, tmp_path):
    """Skip the real ffmpeg: error-path tests send unparseable bytes."""
    wav = tmp_path / "fake.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setattr(
        "azure_mini_app.interactions.miniapp.api.to_wav",
        lambda source, target_dir: wav,
    )
    return wav


class TestAuthWiring:
    def test_dev_mode_serves_requests(self, api_client):
        response = api_client.get("/api/me")
        assert response.status_code == 200
        body = response.json()
        assert body["user"]["telegram_id"] == DEV_ID
        assert body["dev_mode"] is True

    def test_without_init_data_or_dev_mode_is_401(self, monkeypatch, tmp_path):
        make_app(monkeypatch, tmp_path, dev_mode=False)
        from azure_mini_app.interactions.miniapp.web import create_app

        with TestClient(create_app()) as client:
            response = client.get("/api/me")
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "auth_required"

    def test_stranger_auto_registers_pending(self, monkeypatch, tmp_path):
        # Nothing seeded: the dev user is a stranger to the database, so
        # the closed-by-default lifecycle registers them as pending.
        make_app(monkeypatch, tmp_path, allowed=frozenset())
        from azure_mini_app.interactions.miniapp.web import create_app

        with TestClient(create_app()) as client:
            response = client.get("/api/me")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "pending"


class TestCatalog:
    def test_lists_builtin_exercises(self, api_client):
        body = api_client.get("/api/exercises").json()
        exercises = body["exercises"]
        assert len(exercises) > 0
        first = exercises[0]
        assert set(first) == {
            "id",
            "title",
            "reference_text",
            "category",
            "level",
            "phoneme_focus",
        }

    def test_custom_text_validated_and_deduped(self, api_client):
        first = api_client.post("/api/texts", json={"text": ENGLISH_TEXT})
        assert first.status_code == 200
        second = api_client.post(
            "/api/texts", json={"text": "  ".join(ENGLISH_TEXT.split())}
        )
        assert second.json()["exercise"]["id"] == first.json()["exercise"]["id"]

    def test_invalid_text_is_typed_400(self, api_client):
        response = api_client.post("/api/texts", json={"text": "too short"})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "text_invalid"


class TestAssess:
    def test_happy_path_returns_attempt_and_ledgers_audio(
        self, api_client, monkeypatch, fake_wav
    ):
        calls = patch_assess(monkeypatch, fake_report())
        exercises = api_client.get("/api/exercises").json()["exercises"]
        exercise_id = next(e["id"] for e in exercises if e["category"] != "custom")
        response = api_client.post(
            f"/api/assess?exercise_id={exercise_id}",
            content=b"fake-wav-bytes",
            headers={"X-Duration-Seconds": "5"},
        )
        assert response.status_code == 200
        attempt = response.json()["attempt"]
        assert attempt["report"]["pronunciation_score"] == 89.0
        assert attempt["audio_seconds"] == 5.0
        assert calls == [
            next(e["reference_text"] for e in exercises if e["id"] == exercise_id)
        ]

        stats = api_client.get("/api/stats").json()
        assert stats["quota"]["used_seconds"] == 5.0

    def test_assess_custom_text(self, api_client, monkeypatch, fake_wav):
        calls = patch_assess(monkeypatch, fake_report())
        response = api_client.post(
            "/api/assess",
            params={"text": ENGLISH_TEXT},
            content=b"fake-wav-bytes",
        )
        assert response.status_code == 200
        attempt = response.json()["attempt"]
        assert attempt["exercise_id"].startswith("custom-")
        assert calls == [ENGLISH_TEXT]

    def test_empty_body_is_400(self, api_client):
        response = api_client.post("/api/assess", content=b"")
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "audio_missing"

    def test_oversized_recording_is_413(self, api_client):
        response = api_client.post(
            "/api/assess",
            content=b"x",
            headers={"X-Duration-Seconds": "999"},
        )
        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "record_too_long"

    def test_unknown_exercise_is_404(self, api_client, fake_wav):
        response = api_client.post(
            "/api/assess?exercise_id=ghost",
            content=b"fake-wav-bytes",
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "exercise_not_found"

    def test_no_target_is_400(self, api_client, fake_wav):
        response = api_client.post("/api/assess", content=b"fake-wav-bytes")
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "no_target"

    def test_no_speech_maps_to_422(self, api_client, monkeypatch, fake_wav):
        patch_assess(monkeypatch, NoSpeechDetected(audio_seconds=3.0))
        response = api_client.post(
            "/api/assess?exercise_id=s-vs-sh-sentence-1", content=b"x"
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "no_speech"
        assert detail["audio_seconds"] == 3.0

    def test_canceled_maps_to_502(self, api_client, monkeypatch, fake_wav):
        patch_assess(
            monkeypatch,
            AssessmentCanceled(
                reason="bad key", error_details="401", audio_seconds=2.0
            ),
        )
        response = api_client.post(
            "/api/assess?exercise_id=s-vs-sh-sentence-1", content=b"x"
        )
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "assessment_canceled"

    def test_quota_exceeded_maps_to_403(self, api_client, monkeypatch, fake_wav):
        status = QuotaStatus(used_seconds=18000.0, limit_seconds=18000.0)

        def raise_quota(storage, **kwargs):
            raise QuotaExceededError(status)

        monkeypatch.setattr(quota, "ensure_can_assess", raise_quota)
        response = api_client.post(
            "/api/assess?exercise_id=s-vs-sh-sentence-1", content=b"x"
        )
        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail["code"] == "quota_exceeded"
        assert detail["quota"]["exhausted"] is True

    def test_conversion_failure_maps_to_400(self, api_client, monkeypatch):
        def broken_wav(source, target_dir):
            raise VoiceConversionError("ffmpeg not found")

        monkeypatch.setattr(
            "azure_mini_app.interactions.miniapp.api.to_wav", broken_wav
        )
        response = api_client.post(
            "/api/assess?exercise_id=s-vs-sh-sentence-1", content=b"x"
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "voice_error"


class TestReports:
    def test_mistake_bank_lists_weak_exercise(self, api_client, monkeypatch, fake_wav):
        patch_assess(monkeypatch, fake_report(pronunciation=40.0))
        exercise_id = "s-vs-sh-sentence-1"
        graded = api_client.post("/api/assess?exercise_id=" + exercise_id, content=b"x")
        assert graded.status_code == 200

        mistakes = api_client.get("/api/stats").json()  # sanity: endpoint exists
        assert mistakes["quota"]["used_seconds"] == 5.0

        body = api_client.get("/api/mistakes").json()["mistakes"]
        assert len(body) == 1
        assert body[0]["exercise"]["id"] == exercise_id
        assert body[0]["avg_pronunciation_score"] == 40.0

    def test_empty_mistake_bank(self, api_client):
        assert api_client.get("/api/mistakes").json() == {"mistakes": []}

    def test_stats_shape(self, api_client):
        body = api_client.get("/api/stats").json()
        assert body["stats"]["total_attempts"] == 0
        assert body["stats"]["avg_pronunciation_score"] is None
        assert body["quota"]["limit_seconds"] == quota.MONTHLY_LIMIT_SECONDS
