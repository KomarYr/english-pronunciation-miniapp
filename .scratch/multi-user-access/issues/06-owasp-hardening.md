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

- [x] Malformed initData (broken encoding, duplicate fields, oversized body)
      is rejected with a 401 generic body; the strict rules are pinned by
      tests at the pure-function seam.
- [x] 401/403 response bodies contain stable codes and no internal error
      detail; the internal reason still reaches the server logs.
- [x] Repeated auth failures from the same identity/IP are throttled: the
      limiter returns a generic 429 and resets over time (sliding window).
- [x] The 429 body carries no diagnostic detail (no bucket names, no
      remaining-attempt counts).
- [x] Auth denials land in the audit table with actor, subject, timestamp.
- [x] Legitimate active users are unaffected by the limiter at normal
      practice rates.

## Comments (2026-11-17)

- Strict parsing (`_strict_pairs` in auth.py) runs *before* the HMAC:
  oversized payloads (> 4 KiB), malformed percent-encoding (any `%` not
  followed by two hex digits — `parse_qsl` silently passes these), and
  duplicate fields (a duplicated `hash` included) are rejected outright.
  Uniqueness also retired every `next(...)` first-match lookup — the
  fields dict cannot lie. OWASP framing: parser confusion never reaches
  the cryptographic check.
- The limiter is `services/auth_throttle.py`: a plain sliding window
  (deque per key, failures expire at `now - window`), keyed by client
  IP, with an injectable clock. Policy: **5 failures / 60 s** (a legit
  user fails once on stale initData and reopens; a prober fails
  continuously). Lives on `app.state`, created in `create_app`, so each
  app instance (and each test) gets a fresh one. Single-process by
  design (ADR-0003), no locks — a lenient count under races is
  acceptable, a crash is not.
- Rule chosen: **every 401 counts** — presented-but-invalid initData
  (`auth_failed`) and missing credentials (`auth_required`) both trip
  the throttle; a scanner without any header cannot hammer freely.
  429s (`throttled`) are answered before validation and are audited
  too, so probing stays visible (spec story 18) at the cost of
  unbounded rows under sustained attack — accepted for a personal tool.
- Bodies: 401 `auth_failed` / 429 `throttled` carry fixed messages only;
  the internal reason (`str(error)`) goes to the server log
  (`logger.warning` with the client IP) and the audit detail.
- Audit: new `auth_denied` event (the `AuditEvent` literal grew by
  one). `record_auth_denial(ip, reason, claimed_telegram_id)` writes
  actor NULL (no verified identity — the system records), subject =
  the *claimed* telegram id resolved to a user row when it exists
  (best-effort `_claimed_telegram_id` parses the failed payload —
  fabrication-friendly, never used in an access decision), detail =
  `ip: reason`.
- 403 `pending`/`blocked` bodies were already stable codes from
  ticket 03; untouched. `PENDING_MESSAGE` now says "an admin" instead
  of "the bot owner" (glossary: Launcher avoids "bot"; admins approve
  since ticket 05).
- Review findings addressed: **validation now comes before the
  throttle** — valid credentials are never answered with a 429 and
  *clear* the client's failure bucket, so a legitimate user sharing an
  IP with a prober (NAT) never inherits the lockout. A failing attempt
  while the window is tripped gets the generic 429 (audited as
  "throttled"); the real reason is audited for the pre-trip 401s.
  `claimed_telegram_id` moved to auth.py and reuses `_strict_pairs`
  (no lenient re-implementation; malformed payloads yield None).
  Storage grew `list_auth_denials()` (read model for story 18's
  probing visibility) so the Seam-1 audit test asserts through Storage
  public methods, per the file's own docstring; the limiter prunes
  emptied keys (memory pin) and gained `clear()`.
- Documented deviation from "keyed per identity/IP": keying is **IP
  only**. A claimed identity inside a *failed* payload is unverified —
  keying on it would let an attacker fill a victim's bucket by forging
  their user field (cross-user lockout). IP-only plus clear-on-success
  covers the legitimate-shared-IP case the spec worried about.
- Test pins added after review: throttle recovery over real elapsed
  time at the HTTP boundary (tiny 0.3 s window + sleep — the unit
  tests move the clock by hand, this proves the wiring), the claimed
  identity landing as the audit subject, the timestamp on denial rows,
  and valid credentials passing while the bucket is tripped.
