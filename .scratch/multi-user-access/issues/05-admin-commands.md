# 05: Admin commands

**What to build:** Access management from the phone. Admins use `/requests`,
`/approve <id>`, `/block <id>` and `/users` in the Launcher; every command is
enforced admin-only and invisible/no-op for everyone else. Approving a pending
user activates them; approving a blocked user re-activates them (block is
reversible); blocking is refused if it would strip the last remaining admin.
Every approve/block action is written to the audit table with actor, subject
and timestamp. Handlers stay thin over a small access-administration layer so
the logic is testable with real storage and a mocked message.

**Blocked by:** 02, 04.

**Status:** ready-for-agent

- [x] `/requests` lists pending users with Telegram IDs and display names.
- [x] `/approve <id>` activates a pending user; their next Mini App request
      succeeds with no restart.
- [x] `/approve <id>` re-activates a blocked user, preserving all their
      historical data.
- [x] `/block <id>` deactivates a user on both surfaces immediately.
- [x] Blocking the last remaining admin is refused with a clear message.
- [x] Non-admins invoking admin commands get no data and no state change
      (enforced at the command layer as well as the middleware).
- [x] `/users` lists everyone with status and role.
- [x] Every approve and block action appears in the audit table.

## Comments (2026-11-17)

- New `services/access_admin.py` owns the policy (the "small
  access-administration layer"): `list_pending`, `list_all`, `approve`,
  `block`, with `NotAdminError` guarding every entry point. Handlers in
  `interactions/telegram.py` stay thin: parse the id, call the layer,
  render the result — never any access logic inline.
- Enforcement is deliberately doubled. `AdminCommandMiddleware` (message
  outer middleware, runs after the access gate) makes the four commands
  *invisible*: a non-admin gets the same FALLBACK_TEXT as any unknown
  command — no data, no hint the commands exist. The layer then refuses
  again (`NotAdminError`), so admin logic is unreachable even if the
  middleware is bypassed or reordered. Handlers catch the error and
  reply with the same fallback.
- Storage gained `list_access(status=None)` (creation order) and
  `count_active_admins()` — the last-admin guard's input. The guard:
  an *active* admin cannot be blocked while no other active admin
  would remain (an admin who is already pending/blocked may be blocked
  freely — they couldn't act anyway).
- Audit actor resolution: `audit_log.actor_id` is a users.id, so the
  layer resolves the acting admin's entry and passes its `user_id`;
  handlers never touch audit directly. Tests assert the actor, subject
  and `old → new` detail rows straight from the database.
- "Next Mini App request succeeds with no restart" and "deactivates on
  both surfaces immediately" ride on tickets 03/04: both gates re-read
  the access table per request/update, and `/approve`//`/block` just
  move the status column — pinned in test_miniapp_access.py's
  `test_pending_becomes_active_after_approval` and
  `test_block_takes_effect_mid_session`.
- `/approve` of an already-active user is a distinct "already active"
  reply (no audit row — nothing changed); `/approve`/`/block` without a
  numeric argument get a usage line.

- Review findings addressed: invisibility now comes *before* argument
  parsing in the shared `/approve` + `/block` shape — a non-admin with
  a malformed argument gets the fallback, not a usage line that would
  reveal the command (test pins it). `NOT_FOUND_TEXT` no longer says
  "bot" (glossary: Launcher). Dedup: one `_reply_fallback` serves the
  middleware, the command layer and the unknown-text handler;
  `is_admin` delegates to the same `_active_admin_entry` the layer
  uses; `Person` grew `telegram_id`/`status`/`role` properties so
  handlers stop digging through `.entry`; storage's listing is
  `list_access_entries` (avoids the glossary's "access list" alias).
- Review finding accepted as documented behavior: a refused last-admin
  block and an "already active" approve write no audit row — the audit
  trail records *changes* (the ticket-02 review's decision), the fixed
  `AuditEvent` vocabulary has no refusal event, and expanding it just
  for refusals was judged not worth it. The refusal is visible to the
  admin in the reply and in the server log.
