"""Quota policy tests: month window, status math, go/no-go decision."""

from datetime import UTC, datetime

import pytest

from speech_project_azure.domain import Attempt, ScoreReport
from speech_project_azure.services import quota
from speech_project_azure.services.quota import (
    MONTHLY_LIMIT_SECONDS,
    QuotaExceededError,
    ensure_can_assess,
    month_start,
    quota_status,
)
from speech_project_azure.services.storage import Storage

NOV_10 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


@pytest.fixture()
def storage(tmp_path):
    return Storage(tmp_path / "quota-test.db")


def make_attempt(user_id: int, audio_seconds: float, created_at: datetime) -> Attempt:
    report = ScoreReport(
        recognized_text="She sells six silver shells.",
        accuracy_score=92.0,
        fluency_score=88.0,
        completeness_score=100.0,
        prosody_score=75.0,
        pronunciation_score=89.0,
        words=(),
    )
    return Attempt(
        user_id=user_id,
        exercise_id="s-vs-sh-sentence-1",
        reference_text="She sells six silver shells by the sea shore.",
        created_at=created_at,
        audio_seconds=audio_seconds,
        report=report,
    )


class TestMonthStart:
    def test_mid_month_snaps_to_first(self):
        start = month_start(NOV_10)
        assert (start.year, start.month, start.day) == (2026, 11, 1)
        assert (start.hour, start.minute, start.second, start.microsecond) == (
            0,
            0,
            0,
            0,
        )
        assert start.tzinfo is UTC

    def test_first_of_month_is_idempotent(self):
        first = datetime(2026, 11, 1, 17, 30, tzinfo=UTC)
        assert month_start(first) == datetime(2026, 11, 1, tzinfo=UTC)


class TestQuotaStatus:
    def test_empty_ledger(self, storage):
        status = quota_status(storage, now=NOV_10)
        assert status.used_seconds == 0.0
        assert status.remaining_seconds == MONTHLY_LIMIT_SECONDS
        assert status.fraction_used == 0.0
        assert not status.exhausted
        assert not status.warn

    def test_counts_graded_and_no_speech(self, storage):
        uid = storage.get_or_create_user("u").id or 1
        storage.add_attempt(make_attempt(uid, 100.0, NOV_10))
        storage.add_audio_usage(uid, 50.0, reason="no_speech", created_at=NOV_10)
        status = quota_status(storage, now=NOV_10)
        assert status.used_seconds == pytest.approx(150.0)

    def test_ignores_previous_months(self, storage):
        uid = storage.get_or_create_user("u").id or 1
        storage.add_attempt(make_attempt(uid, 100.0, NOV_10))
        storage.add_audio_usage(
            uid, 500.0, reason="canceled", created_at=datetime(2026, 10, 2, tzinfo=UTC)
        )
        status = quota_status(storage, now=NOV_10)
        assert status.used_seconds == pytest.approx(100.0)

    def test_warn_threshold(self, storage):
        uid = storage.get_or_create_user("u").id or 1
        # just under 80% → no warning yet
        storage.add_audio_usage(
            uid, MONTHLY_LIMIT_SECONDS * 0.8 - 1.0, reason="no_speech"
        )
        assert not quota_status(storage, now=NOV_10).warn
        # exactly 80% → warn, not exhausted
        storage.add_audio_usage(uid, 1.0, reason="no_speech")
        status = quota_status(storage, now=NOV_10)
        assert status.warn
        assert not status.exhausted

    def test_over_limit_is_exhausted(self, storage):
        uid = storage.get_or_create_user("u").id or 1
        storage.add_audio_usage(uid, MONTHLY_LIMIT_SECONDS + 30.0, reason="no_speech")
        status = quota_status(storage, now=NOV_10)
        assert status.exhausted
        assert not status.warn
        assert status.fraction_used > 1.0
        assert status.remaining_seconds == 0.0


class TestEnsureCanAssess:
    def test_returns_status_when_budget_remains(self, storage):
        uid = storage.get_or_create_user("u").id or 1
        storage.add_audio_usage(uid, 100.0, reason="no_speech", created_at=NOV_10)
        status = ensure_can_assess(storage, now=NOV_10)
        assert status.used_seconds == pytest.approx(100.0)

    def test_raises_carrying_status_when_exhausted(self, storage):
        uid = storage.get_or_create_user("u").id or 1
        storage.add_audio_usage(
            uid, MONTHLY_LIMIT_SECONDS, reason="no_speech", created_at=NOV_10
        )
        with pytest.raises(QuotaExceededError) as exc_info:
            ensure_can_assess(storage, now=NOV_10)
        assert exc_info.value.status.exhausted
        assert exc_info.value.status.used_seconds == pytest.approx(
            MONTHLY_LIMIT_SECONDS
        )

    def test_exact_limit_counts_as_exhausted(self, storage):
        # used == limit → no remaining budget → block
        assert quota.QuotaStatus(
            used_seconds=MONTHLY_LIMIT_SECONDS, limit_seconds=MONTHLY_LIMIT_SECONDS
        ).exhausted
