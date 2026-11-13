"""Use-case tests: guardrails, quota gate, ledger logging, mistake bank.

azure_assessment.assess is monkeypatched — no Azure account, no microphone.
"""

from datetime import UTC, datetime

import pytest

from azure_mini_app import app
from azure_mini_app.domain import ScoreReport, TextValidationError
from azure_mini_app.services.azure_assessment import (
    AssessmentCanceled,
    NoSpeechDetected,
)
from azure_mini_app.services.quota import (
    MONTHLY_LIMIT_SECONDS,
    QuotaExceededError,
)
from azure_mini_app.services.storage import Storage

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


@pytest.fixture()
def storage(tmp_path):
    return Storage(tmp_path / "app-test.db")


@pytest.fixture()
def user(storage):
    return storage.get_or_create_user("tester")


@pytest.fixture()
def exercise():
    from azure_mini_app import exercises as catalog

    return catalog.get_exercise("s-vs-sh-sentence-1")


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


def patch_assess(monkeypatch, result):
    """result: ScoreReport or an exception instance."""
    calls = []

    def fake_assess(audio, reference_text, language="en-US"):
        calls.append({"audio": audio, "reference_text": reference_text})
        if isinstance(result, Exception):
            raise result
        return result, 4.2

    monkeypatch.setattr(app.azure_assessment, "assess", fake_assess)
    return calls


class TestValidateCustomText:
    def test_accepts_valid_english(self):
        text = "The quick brown fox jumps over the lazy dog near the river bank every morning."
        app.validate_custom_text(text)  # no raise

    def test_rejects_empty_and_whitespace(self):
        for text in ("", "   \n\t  "):
            with pytest.raises(TextValidationError):
                app.validate_custom_text(text)

    def test_rejects_too_short(self):
        with pytest.raises(TextValidationError, match="Too short"):
            app.validate_custom_text("Just three words here.")

    def test_rejects_too_long(self):
        text = " ".join(["word"] * 51)
        with pytest.raises(TextValidationError, match="Too long"):
            app.validate_custom_text(text)

    def test_rejects_non_english(self):
        text = "Этот текст содержит ровно десять слов для проверки минимума слов."
        with pytest.raises(TextValidationError, match="English"):
            app.validate_custom_text(text)


class TestRecordAttempt:
    def test_happy_path_persists_attempt_and_ledger(
        self, storage, user, exercise, monkeypatch
    ):
        patch_assess(monkeypatch, fake_report())
        attempt = app.record_attempt(storage, user, exercise)
        assert attempt.id is not None
        assert attempt.report.pronunciation_score == 89.0
        # graded ledger row written in the same transaction
        assert storage.monthly_audio_seconds(NOW.replace(day=1)) == pytest.approx(4.2)

    def test_no_speech_logged_to_ledger_and_reraised(
        self, storage, user, exercise, monkeypatch
    ):
        patch_assess(monkeypatch, NoSpeechDetected(2.5))
        with pytest.raises(NoSpeechDetected):
            app.record_attempt(storage, user, exercise)
        # silent audio still bills → ledger
        assert storage.monthly_audio_seconds(NOW.replace(day=1)) == pytest.approx(2.5)
        # but no attempt was stored
        assert storage.attempts_for_user(user.id or 0) == []

    def test_canceled_logged_to_ledger_and_reraised(
        self, storage, user, exercise, monkeypatch
    ):
        patch_assess(monkeypatch, AssessmentCanceled("Error", "bad key", 1.5))
        with pytest.raises(AssessmentCanceled):
            app.record_attempt(storage, user, exercise)
        assert storage.monthly_audio_seconds(NOW.replace(day=1)) == pytest.approx(1.5)

    def test_quota_gate_blocks_before_azure_call(
        self, storage, user, exercise, monkeypatch
    ):
        calls = patch_assess(monkeypatch, fake_report())
        storage.add_audio_usage(user.id or 0, MONTHLY_LIMIT_SECONDS, reason="no_speech")
        with pytest.raises(QuotaExceededError):
            app.record_attempt(storage, user, exercise)
        assert calls == []  # assess never invoked

    def test_sends_exercise_reference_text_to_azure(
        self, storage, user, exercise, monkeypatch
    ):
        calls = patch_assess(monkeypatch, fake_report())
        app.record_attempt(storage, user, exercise)
        assert calls[0]["reference_text"] == exercise.reference_text


class TestAssessCustomText:
    def test_creates_exercise_and_links_attempt(self, storage, user, monkeypatch):
        patch_assess(monkeypatch, fake_report())
        text = "The quick brown fox jumps over the lazy dog near the river bank every morning."
        attempt = app.assess_custom_text(storage, user, text)
        assert attempt.exercise_id is not None
        assert attempt.reference_text == " ".join(text.split())
        # custom exercise persisted → mistake bank can pick it up
        assert storage.get_exercise(attempt.exercise_id) is not None

    def test_same_text_reuses_exercise(self, storage, user, monkeypatch):
        patch_assess(monkeypatch, fake_report())
        text = "The quick brown fox jumps over the lazy dog near the river bank every morning."
        first = app.assess_custom_text(storage, user, text)
        second = app.assess_custom_text(storage, user, f"  {text}  ")
        assert first.exercise_id == second.exercise_id

    def test_invalid_text_never_calls_azure(self, storage, user, monkeypatch):
        calls = patch_assess(monkeypatch, fake_report())
        with pytest.raises(TextValidationError):
            app.assess_custom_text(storage, user, "too short")
        assert calls == []


class TestMistakeBank:
    def test_low_scores_enter_bank_automatically(
        self, storage, user, exercise, monkeypatch
    ):
        patch_assess(monkeypatch, fake_report(pronunciation=55.0))
        app.record_attempt(storage, user, exercise)
        bank = app.mistake_bank(storage, user)
        assert [s.exercise.id for s in bank] == [exercise.id]

    def test_good_scores_stay_out(self, storage, user, exercise, monkeypatch):
        patch_assess(monkeypatch, fake_report(pronunciation=85.0))
        app.record_attempt(storage, user, exercise)
        assert app.mistake_bank(storage, user) == []

    def test_practicing_well_removes_from_bank(
        self, storage, user, exercise, monkeypatch
    ):
        patch_assess(monkeypatch, fake_report(pronunciation=55.0))
        app.record_attempt(storage, user, exercise)
        patch_assess(monkeypatch, fake_report(pronunciation=90.0))
        app.record_attempt(storage, user, exercise)
        # average (55+90)/2 = 72.5 ≥ 70 → self-healed out of the bank
        assert app.mistake_bank(storage, user) == []


class TestAppStats:
    def test_combines_user_stats_and_quota(self, storage, user, exercise, monkeypatch):
        patch_assess(monkeypatch, fake_report())
        app.record_attempt(storage, user, exercise)
        user_stats, quota_status = app.app_stats(storage, user)
        assert user_stats.total_attempts == 1
        assert quota_status.used_seconds == pytest.approx(4.2)
