# 06: OWASP hardening on the auth surface

**What to build:** The auth boundary resists abuse and leaks nothing. initData
parsing rejects malformed input instead of silently repairing it: bad
percent-encoding, duplicate keys (including duplicate `hash`), oversized
payloads. Authentication error responses carry only stable codes — internal
reasons go to server logs, not clients. Repeated authentication failures trip
a sliding-window rate limiter keyed per identity/IP, answered with a generic
429 with no diagnostic detail. Authentication denials are recorded in the
audit table.

**Blocked by:** 01, 02.

**Status:** ready-for-agent

- [ ] Malformed initData (broken encoding, duplicate fields, oversized body)
      is rejected with a 401 generic body; the strict rules are pinned by
      tests at the pure-function seam.
- [ ] 401/403 response bodies contain stable codes and no internal error
      detail; the internal reason still reaches the server logs.
- [ ] Repeated auth failures from the same identity/IP are throttled: the
      limiter returns a generic 429 and resets over time (sliding window).
- [ ] The 429 body carries no diagnostic detail (no bucket names, no
      remaining-attempt counts).
- [ ] Auth denials land in the audit table with actor, subject, timestamp.
- [ ] Legitimate active users are unaffected by the limiter at normal
      practice rates.
