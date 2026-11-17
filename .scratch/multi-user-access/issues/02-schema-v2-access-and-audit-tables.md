# 02: Schema v2 — access table, audit table, migration & seeding

**What to build:** The database becomes the home of access control. Existing
databases upgrade in place via versioned migration: a new access table (one
row per Telegram ID: status pending/active/blocked, role admin/user, foreign
key to the user row, timestamps) and a new audit table (actor, subject, event,
timestamp). On first run after the upgrade, every Telegram ID from the legacy
env allowlist is seeded as `active`, and every ID in `ADMIN_TELEGRAM_IDS` is
seeded as `active` with role `admin` — one-time, so the first admin can exist
before any approval flow can run. Login stops matching users by display name,
so same-named people can never collide into one account.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] Opening a version-1 database with the new code migrates it to version 2
      in place, driven by SQLite's `user_version` pragma.
- [ ] Legacy env allowlist IDs appear in the access table as `active` after
      migration.
- [ ] `ADMIN_TELEGRAM_IDS` IDs appear as `active` admins after migration; a
      missing/empty admin seed is reported clearly at startup.
- [ ] Access entries support create, read (by telegram id), status change and
      role change; the audit table records each change.
- [ ] User resolution matches on `telegram_id` only; the display-name
      OR-match is gone and its test proves two same-named users stay distinct.
- [ ] Migration is idempotent: opening an already-migrated database changes
      nothing.
