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

- [x] Opening a version-1 database with the new code migrates it to version 2
      in place, driven by SQLite's `user_version` pragma.
- [x] Legacy env allowlist IDs appear in the access table as `active` after
      migration.
- [x] `ADMIN_TELEGRAM_IDS` IDs appear as `active` admins after migration; a
      missing/empty admin seed is reported clearly at startup.
- [x] Access entries support create, read (by telegram id), status change and
      role change; the audit table records each change.
- [x] User resolution matches on `telegram_id` only; the display-name
      OR-match is gone and its test proves two same-named users stay distinct.
- [x] Migration is idempotent: opening an already-migrated database changes
      nothing.

## Notes (2026-11-17)

- `Storage.init_schema` drives everything off `PRAGMA user_version`:
  version 0 with a `users` table = v1 database (migrate in place + seed),
  version 0 on a fresh file = create + seed once, version 2 = no-op
  (idempotency test reopens with *changed* env and asserts nothing moves).
- Seeding takes the union of `ALLOWED_TELEGRAM_IDS` and
  `ADMIN_TELEGRAM_IDS` (admin wins on overlap), reuses existing user
  rows and creates `tg-<id>` placeholders for allowlisted people who
  never opened the app (the access row's FK needs a target). Each
  seeded row writes an `access_seeded` audit event.
- Access API: `ensure_access` (idempotent create, defaults pending),
  `get_access`, `set_access_status`, `set_access_role`; every change
  writes an audit event (`access_created`, `status_changed`,
  `role_changed` with `old → new` detail).
- `get_or_create_user` matches telegram_id only; without a telegram id
  there is no identity to match, so the call always creates. A seeded
  `tg-<id>` placeholder gets its real display name the first time the
  actual person logs in (placeholder only — real names are never
  overwritten).
- Audit trail records *changes*: a re-`ensure_access` of an existing
  entry and a set-to-current-value write nothing at all (no row, no
  audit event). Review finding — "each change" means changes.
- `_seed_access` is row-level idempotent (skips existing telegram_ids),
  so an interrupted migration re-opens without crashing.
- Diagnostics: empty `ADMIN_TELEGRAM_IDS` at seed time; empty access
  table on an already-migrated database while bootstrap IDs exist in
  .env (data likely wiped — warning, not auto-repair).
- Deferred to ticket 07: the "warn at startup if
  `ALLOWED_TELEGRAM_IDS` is still set" story — that env var is still
  the live gate until tickets 03/04 flip access checks to the database;
  warning about it now would be wrong.
- Test fixture note: `tests/test_storage.py`'s `storage` fixture now
  empties both env sets, so the real `.env` can't seed into test
  databases.
