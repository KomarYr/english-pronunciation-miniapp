# 03: Mini App access lifecycle, end-to-end

**What to build:** The complete stranger story inside the Mini App. A stranger
who opens the app with valid initData is auto-registered as pending and sees a
"waiting for approval" screen; the bot owner (admins) gets a Telegram
notification that someone requested access. A blocked user sees a distinct
"you were blocked" screen instead of waiting in vain. An active user gets the
full app exactly as today, checked on every request so a block takes effect
mid-session. Pending and blocked users keep their identity data; a blocked
user's attempts and exercises are untouched.

**Blocked by:** 02.

**Status:** ready-for-agent

- [x] A stranger's valid initData creates a pending user row plus pending
      access entry (idempotent: reopening does not duplicate) and the API
      returns 403 with the `pending` code.
- [x] A blocked user's requests return 403 with the `blocked` code.
- [x] The frontend renders distinct screens for `pending` and `blocked`,
      driven by the 403 code contract (manual verification, no new frontend
      test harness).
- [x] Creating a pending user sends a best-effort notification message to
      each admin via the Bot API (works in webhook and long-polling modes).
- [x] An active user's requests behave exactly as before the change.
- [x] Blocked users' stored attempts and custom exercises survive.

## Notes (2026-11-17)

- The Mini App gate moved out of `auth.py` into a new
  `interactions/miniapp/access.py`: `register_access` (user row +
  access entry, idempotent — reopening never duplicates; the first
  display name sticks) and `notify_admins_pending` (best-effort
  sendMessage straight to the Bot API, per-admin try/except, 5s
  per-send ceiling so the stranger's first request can't hang).
  `get_user` in api.py is now the database gate: register → notify on
  first sight → map `pending`/`blocked`/`active`.
- 403 contract: `pending` and `blocked` codes with generic bodies; the
  old `not_allowed` code is retired from the Mini App (all four
  frontend call sites updated to the shared `renderAccessGate` in the
  new `screens/access.ts`; pending and blocked get distinct screens).
- `auth.py` keeps `is_allowed` (still the Launcher's live gate until
  ticket 04) and the dev-mode guard; `resolve_user`/`NotAllowedError`
  are gone. Dev mode unchanged: the fixed dev user needs an active
  access entry, which the env bootstrap seeds.
- Notification mocking in tests sits at the `Bot.send_message`
  boundary; a raising send still answers the stranger 403 `pending`
  (best-effort proven). Tests: new `tests/test_miniapp_access.py`
  (11 tests) covering registration, idempotency, notification, active
  behavior, mid-session block, and data survival + re-approval.
- Frontend `dist` rebuilt for manual verification (pending/blocked
  screens) — needs a real Telegram/dev-mode look per the ticket.
- Ruff format was run but reverted for `storage.py`/`test_storage.py`
  (ticket-02 files — formatting churn would pollute this diff).

### Deferred from code review

- Two concurrent first requests from the same stranger can both see "no
  access entry yet" and both notify admins (double notification). Rare,
  best-effort scope; fixing needs `ensure_access` to report whether it
  inserted.
- No re-notify when admins are configured after a registration, or when
  every send failed — a user registered with an empty
  `ADMIN_TELEGRAM_IDS` stays pending silently. Candidate for ticket 05
  (`/requests` already covers discovery).
- Denied-access audit events (spec's "records denied access") are not
  written here; ticket-02's audit vocabulary has no `denied` event —
  fits ticket 06 (OWASP hardening).
- Glossary gap: `pending`/`active`/`blocked` access statuses are
  load-bearing across backend and frontend but missing from
  `GLOSSARY.md` — ride with ticket 07's docs pass.
