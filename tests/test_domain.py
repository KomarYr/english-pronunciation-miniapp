from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from speech_project_azure.domain import (
    Attempt,
    ScoreReport,
    TextValidationError,
    User,
    WordScore,
)


def make_report(
    *,
    recognized_text: str = "She sells six silver shells.",
    accuracy_score: float = 92.0,
    fluency_score: float = 88.0,
    completeness_score: float = 100.0,
    prosody_score: float = 75.0,
    pronunciation_score: float = 89.0,
    words: tuple[WordScore, ...] = (
        WordScore("She", 95.0, "None"),
        WordScore("sells", 90.0, "None"),
        WordScore("six", 85.0, "None"),
    ),
) -> ScoreReport:
    return ScoreReport(
        recognized_text=recognized_text,
        accuracy_score=accuracy_score,
        fluency_score=fluency_score,
        completeness_score=completeness_score,
        prosody_score=prosody_score,
        pronunciation_score=pronunciation_score,
        words=words,
    )


def make_attempt(
    *,
    user_id: int = 1,
    exercise_id: str | None = "s-vs-sh-sentence-1",
    reference_text: str = "She sells six silver shells by the sea shore.",
    created_at: datetime = datetime(2026, 11, 10, 12, 0, tzinfo=UTC),
    audio_seconds: float = 4.2,
    report: ScoreReport | None = None,
    id: int | None = None,
) -> Attempt:
    return Attempt(
        user_id=user_id,
        exercise_id=exercise_id,
        reference_text=reference_text,
        created_at=created_at,
        audio_seconds=audio_seconds,
        report=report if report is not None else make_report(),
        id=id,
    )


class TestScoreReport:
    def test_is_immutable(self):
        report = make_report()
        with pytest.raises(FrozenInstanceError):
            report.pronunciation_score = 50.0  # ty: ignore[invalid-assignment]

    def test_words_default_empty(self):
        report = ScoreReport(
            recognized_text="",
            accuracy_score=0,
            fluency_score=0,
            completeness_score=0,
            prosody_score=0,
            pronunciation_score=0,
        )
        assert report.words == ()

    def test_words_are_immutable(self):
        report = make_report()
        with pytest.raises(FrozenInstanceError):
            report.words[0].word = "x"  # ty: ignore[invalid-assignment]

    def test_is_hashable(self):
        assert {make_report(), make_report()}  # set construction works


class TestUser:
    def test_cli_user_has_no_telegram_id(self):
        user = User(display_name="yurii")
        assert user.telegram_id is None
        assert user.id is None
        assert user.created_at is None

    def test_telegram_user_fields(self):
        user = User(
            display_name="yurii",
            id=7,
            telegram_id=123456,
            created_at=datetime(2026, 11, 10, tzinfo=UTC),
        )
        assert user.telegram_id == 123456

    def test_is_immutable(self):
        with pytest.raises(FrozenInstanceError):
            User(display_name="a").display_name = "b"  # ty: ignore[invalid-assignment]


class TestAttempt:
    def test_exercise_attempt(self):
        attempt = make_attempt()
        assert attempt.exercise_id == "s-vs-sh-sentence-1"
        assert attempt.id is None  # assigned by storage on insert

    def test_custom_text_attempt_has_no_exercise_id(self):
        attempt = make_attempt(exercise_id=None, reference_text="My own sentence.")
        assert attempt.exercise_id is None
        assert attempt.reference_text == "My own sentence."

    def test_is_immutable(self):
        with pytest.raises(FrozenInstanceError):
            make_attempt().audio_seconds = 0.0  # ty: ignore[invalid-assignment]

    def test_created_at_is_tz_aware(self):
        assert make_attempt().created_at.tzinfo is not None


class TestTextValidationError:
    def test_is_value_error(self):
        # interactions catch ValueError to render friendly messages
        assert issubclass(TextValidationError, ValueError)
