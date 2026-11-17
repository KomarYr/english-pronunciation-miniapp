# 04: Launcher gate on the database

**What to build:** The bot's update gate consults the access table instead of
the env allowlist. Pending strangers get a message that their request was
recorded and is awaiting approval; blocked users are refused; active users and
admins pass through. `/start` still bypasses the gate for onboarding, and
`/id` becomes a status checker reporting pending / active / blocked instead of
a number to copy.

**Blocked by:** 02.

**Status:** ready-for-agent

- [ ] The update middleware reads access state from the database for every
      non-bypass update, with the same `/start` and `/id` bypass as today.
- [ ] A pending stranger receives an "awaiting approval" message on any
      gated command.
- [ ] A blocked user is refused on any gated command or callback.
- [ ] Active users and admins pass through unchanged.
- [ ] `/id` reports the caller's current status (pending, active, blocked)
      in plain language.
- [ ] The env allowlist is no longer consulted by the launcher gate.
