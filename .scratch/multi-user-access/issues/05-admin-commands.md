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

- [ ] `/requests` lists pending users with Telegram IDs and display names.
- [ ] `/approve <id>` activates a pending user; their next Mini App request
      succeeds with no restart.
- [ ] `/approve <id>` re-activates a blocked user, preserving all their
      historical data.
- [ ] `/block <id>` deactivates a user on both surfaces immediately.
- [ ] Blocking the last remaining admin is refused with a clear message.
- [ ] Non-admins invoking admin commands get no data and no state change
      (enforced at the command layer as well as the middleware).
- [ ] `/users` lists everyone with status and role.
- [ ] Every approve and block action appears in the audit table.
