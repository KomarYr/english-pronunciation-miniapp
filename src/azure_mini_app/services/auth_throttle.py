"""Sliding-window throttle on authentication failures (ticket 06).

The limiter is deliberately dumb: a fixed limit of failures inside a
sliding window, keyed by whatever string the caller chooses (client IP
in the API). No locks — the API is a single process (ADR-0003) and the
worse failure mode is a lenient count, not a crash. The clock is
injectable so tests can slide the window by hand.
"""

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class SlidingWindowLimiter:
    """Trip once `limit` failures land inside `window` seconds."""

    limit: int
    window: float
    _failures: dict[str, deque[float]] = field(default_factory=dict)

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        bucket = self._failures.setdefault(key, deque())
        bucket.append(moment)
        self._expire(bucket, moment)

    def clear(self, key: str) -> None:
        """A valid authentication proves the client is real: forget its
        failures, so a legitimate user sharing an IP with a prober (NAT)
        never inherits the bucket."""
        self._failures.pop(key, None)

    def is_limited(self, key: str, *, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        bucket = self._failures.get(key)
        if not bucket:
            return False
        self._expire(bucket, moment)
        if not bucket:  # prune: emptied buckets don't linger in memory
            del self._failures[key]
            return False
        return len(bucket) >= self.limit

    def _expire(self, bucket: deque[float], now: float) -> None:
        """A failure counts until it slides out of the window."""
        horizon = now - self.window
        while bucket and bucket[0] <= horizon:
            bucket.popleft()
