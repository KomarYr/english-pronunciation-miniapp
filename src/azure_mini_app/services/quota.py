"""Monthly audio-budget policy over the storage quota ledger.

All the numbers come from storage.monthly_audio_seconds (the audio_usage
ledger); this module adds only the policy: the limit, the warn threshold,
and the go / no-go decision. It never prints or sends anything — rendering
the status is the adapters' job.

Subscription-wide by design: Azure F0 bills the 5-hour budget per
subscription across all users, so no user_id appears here.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from azure_mini_app.services.storage import Storage

# Azure F0 free tier: 5 audio-hours per subscription per month.
MONTHLY_LIMIT_SECONDS = 18_000.0
# Fraction of the budget at which callers should start warning the user.
WARN_FRACTION = 0.8


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    """Snapshot of the monthly audio budget."""

    used_seconds: float
    limit_seconds: float

    @property
    def remaining_seconds(self) -> float:
        return max(self.limit_seconds - self.used_seconds, 0.0)

    @property
    def fraction_used(self) -> float:
        return self.used_seconds / self.limit_seconds

    @property
    def exhausted(self) -> bool:
        return self.used_seconds >= self.limit_seconds

    @property
    def warn(self) -> bool:
        """True when close to the limit but attempts are still allowed."""
        return not self.exhausted and self.fraction_used >= WARN_FRACTION


class QuotaExceededError(Exception):
    """The monthly audio budget is gone; no more assessments this month."""

    def __init__(self, status: QuotaStatus):
        super().__init__(
            f"Monthly audio budget exhausted: "
            f"{status.used_seconds:.0f}s of {status.limit_seconds:.0f}s used."
        )
        self.status = status


def month_start(now: datetime | None = None) -> datetime:
    """Start of the current monthly quota window: the 1st, 00:00 UTC."""
    moment = now or datetime.now(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def quota_status(storage: Storage, *, now: datetime | None = None) -> QuotaStatus:
    used = storage.monthly_audio_seconds(month_start(now))
    return QuotaStatus(used_seconds=used, limit_seconds=MONTHLY_LIMIT_SECONDS)


def ensure_can_assess(storage: Storage, *, now: datetime | None = None) -> QuotaStatus:
    """Raise QuotaExceededError when the budget is gone, else return the status.

    Best-effort gate: call once before each assessment. Seconds spent by the
    attempt itself are only known afterwards, so the very last attempt of a
    month may push usage slightly past the limit — acceptable at this scale.
    """
    status = quota_status(storage, now=now)
    if status.exhausted:
        raise QuotaExceededError(status)
    return status
