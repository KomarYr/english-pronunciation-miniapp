# 04: Launcher gate on the database

**What to build:** The bot's update gate consults the access table instead of
the env allowlist. Pending strangers get a message that their request was
recorded and is awaiting approval; blocked users are refused; active users and
admins pass through. `/start` still bypasses the gate for onboarding, and
`/id` becomes a status checker reporting pending / active / blocked instead of
a number to copy.

**Blocked by:** 02.

**Status:** ready-for-agent

- [x] The update middleware reads access state from the database for every
      non-bypass update, with the same `/start` and `/id` bypass as today.
- [x] A pending stranger receives an "awaiting approval" message on any
      gated command.
- [x] A blocked user is refused on any gated command or callback.
- [x] Active users and admins pass through unchanged.
- [x] `/id` reports the caller's current status (pending, active, blocked)
      in plain language.
- [x] The env allowlist is no longer consulted by the launcher gate.

## Comments (2026-11-17)

- `AllowlistMiddleware` → `AccessMiddleware(storage)`: same `/start` +
  `/id` text-prefix bypass, then the access table decides. `active`
  passes (admins are active by seeding, the role itself doesn't gate —
  ticket 05 enforces admin-only commands); `pending` gets the
  awaiting-approval message; `blocked` is refused. Callbacks get the
  same texts as an alert.
- A stranger with no access entry is auto-registered as `pending` —
  the same `register_access` the Mini App uses (ticket 03), so both
  surfaces share one registration path and one audit trail. Admins are
  notified once per person via the existing `notify_admins_pending`;
  "send the bot any message" replaces the old copy-your-ID dance.
  Decision confirmed with the owner (auto-register, not read-only).
- Display-name derivation is extracted to
  `auth.display_name_for` — initData parsing and the launcher gate now
  build identities by one rule (full name, else username, else
  `tg-<id>`).
- `/id` is a pure status checker: reports pending / active (+admin
  role) / blocked / "no access request on file yet", and never
  registers the caller (bypass commands stay side-effect free).
- `create_dispatcher(storage)` — the web server passes its existing
  `Storage`, the long-polling `main()` builds its own; the middleware
  injects the instance into handler kwargs so `/id` can read it.
- `is_allowed` is deleted: the launcher gate was its last consumer.
  `ALLOWED_TELEGRAM_IDS` still exists for the v1→v2 seed and the dev
  user must hold an active entry like anyone else.
- Update with no Telegram user (channel posts): logged and dropped
  without a reply — there is no identity to register or refuse.

- Review findings addressed: ticket notes moved under `## Comments`
  (issue-tracker convention); the three test helpers collapsed into one
  `grant(status=...)`; bypass test now pins each command separately;
  the pending-callback alert path gained its test. The `data["storage"]`
  handler-injection key was checked against aiogram — the framework
  injects FSM storage as `fsm_storage`, so no collision.
