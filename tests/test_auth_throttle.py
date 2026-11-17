"""Sliding-window throttle tests (ticket 06): the limiter as pure logic.

The clock is injectable, so window expiry is tested by moving time,
never by sleeping. HTTP-level throttling is pinned in
test_miniapp_access.py (Seam 1).
"""

from azure_mini_app.services.auth_throttle import SlidingWindowLimiter


class TestSlidingWindowLimiter:
    def test_under_limit_never_trips(self):
        limiter = SlidingWindowLimiter(limit=5, window=60.0)
        for second in range(4):
            limiter.record_failure("1.2.3.4", now=float(second))
            assert not limiter.is_limited("1.2.3.4", now=float(second))

    def test_limit_failures_trip(self):
        limiter = SlidingWindowLimiter(limit=3, window=60.0)
        for second in range(3):
            limiter.record_failure("ip", now=float(second))
        assert limiter.is_limited("ip", now=10.0)

    def test_window_slides_old_failures_expire(self):
        limiter = SlidingWindowLimiter(limit=2, window=60.0)
        limiter.record_failure("ip", now=0.0)
        limiter.record_failure("ip", now=1.0)
        assert limiter.is_limited("ip", now=2.0)
        # both failures have slid out of the window — the throttle reset
        assert not limiter.is_limited("ip", now=61.5)

    def test_one_expired_failure_unlimits_a_half_window(self):
        limiter = SlidingWindowLimiter(limit=2, window=60.0)
        limiter.record_failure("ip", now=0.0)
        limiter.record_failure("ip", now=30.0)
        assert limiter.is_limited("ip", now=31.0)
        assert not limiter.is_limited("ip", now=61.0)  # only the second counts

    def test_keys_are_independent(self):
        limiter = SlidingWindowLimiter(limit=1, window=60.0)
        limiter.record_failure("attacker", now=0.0)
        assert limiter.is_limited("attacker", now=0.0)
        assert not limiter.is_limited("someone-else", now=0.0)

    def test_default_clock_records_and_reports(self):
        limiter = SlidingWindowLimiter(limit=2, window=60.0)
        limiter.record_failure("ip")
        limiter.record_failure("ip")
        assert limiter.is_limited("ip")

    def test_clear_forgets_the_failures(self):
        limiter = SlidingWindowLimiter(limit=2, window=60.0)
        limiter.record_failure("ip", now=0.0)
        limiter.record_failure("ip", now=1.0)
        limiter.clear("ip")
        assert not limiter.is_limited("ip", now=1.5)
        # and the bucket starts over: two fresh failures are needed again
        limiter.record_failure("ip", now=2.0)
        assert not limiter.is_limited("ip", now=2.5)

    def test_expired_buckets_are_pruned_from_memory(self):
        # memory-growth pin: an emptied key must not linger forever
        # (a spoofed-IP sweep would otherwise grow the dict unbounded)
        limiter = SlidingWindowLimiter(limit=1, window=60.0)
        limiter.record_failure("spoofed", now=0.0)
        limiter.record_failure("another", now=0.0)
        assert limiter.is_limited("spoofed", now=100.0) is False
        assert limiter.is_limited("another", now=100.0) is False
        assert limiter._failures == {}
