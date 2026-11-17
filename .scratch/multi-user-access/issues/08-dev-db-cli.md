# 08: Developer DB CLI (seed / reset / list)

**What to build:** A developer-facing CLI, `azure-mini-app-dev`, for the
access database. `seed` idempotently ensures the fixed dev user
(`MINIAPP_DEV_USER_ID`) holds an active access entry — the missing
runtime path for existing databases, where the bootstrap seed never
runs and no admin command can *create* an entry for the dev user.
`reset` recreates the database from scratch behind a confirmation
prompt (`--yes` skips it for scripting). `list` prints everyone's
Telegram ID, display name, status and role. The docs flip to the
admin-first flow: `ADMIN_TELEGRAM_IDS` + `/approve` is the headline
bootstrap, `ALLOWED_TELEGRAM_IDS` is a legacy-migration note ("leave
empty for new setups"), and dev-mode instructions point at the CLI
instead of "start from a fresh DB file".

**Blocked by:** None (can start immediately; builds on tickets 02–07).

**Status:** ready-for-agent

- [x] `uv run azure-mini-app-dev seed` creates the dev user's active
      access entry when absent (audited as `access_created`), activates
      a pending/blocked one (audited as `status_changed`), and reports
      idempotently when already active (no write, no audit row).
- [x] `uv run azure-mini-app-dev reset` prints the DB path and asks for
      confirmation before deleting (`--yes` skips); the recreated
      database seeds `ADMIN_TELEGRAM_IDS` as active admins, seeds
      `ALLOWED_TELEGRAM_IDS` only when still set, and seeds the dev
      user active when `MINIAPP_DEV_MODE` is on.
- [x] `uv run azure-mini-app-dev list` prints every access entry with
      display name, status and role (read-only).
- [x] The CLI is a Python module (`src/azure_mini_app/devtools.py`)
      registered as a `[project.scripts]` entry in `pyproject.toml`,
      reusing the Storage API — no hand-written SQL.
- [x] The dev-mode startup guard's exit message points at
      `azure-mini-app-dev seed` (replacing the "fresh DB file" advice).
- [x] `.env.example` and README present `ADMIN_TELEGRAM_IDS` +
      `/approve` as the bootstrap flow; `ALLOWED_TELEGRAM_IDS` is a
      legacy-migration note; dev-mode docs point at the CLI.
- [x] Tests at the CLI's own seam (tmp DB, no sqlite hand-rolls) cover
      every checkbox above.

## Comments (2026-11-17)

- Origin: the owner hit the ticket-07 dev-mode refusal on a real
  database — a v1 database created 2026-11-14 migrated to v2, but the
  dev user (900000001) had a `users` row and no access entry, because
  the bootstrap seed only runs at migration/first-run and no admin
  command can create an entry for someone who never sent a request.
  The "start from a fresh DB file" advice works but discards practice
  history; a proper dev tool is the better answer.
- Owner decisions from the planning discussion: `reset` requires a
  confirmation prompt (with `--yes` for scripting); the CLI gets a
  read-only `list` subcommand; the documented bootstrap flow becomes
  admin-first — `.env` without `ALLOWED_TELEGRAM_IDS`: first start
  seeds `ADMIN_TELEGRAM_IDS` as active admins, everyone else enters via
  pending → admin `/approve`. That flow already works; the docs just
  need to lead with it.
- Design note: the CLI reuses the real Storage API (get_or_create_user,
  ensure_access, list_access_entries, bootstrap seeding) so every
  change lands in the audit trail exactly like a real approval, and the
  tool stays testable like the rest of the repo. A raw shell script
  hand-writing SQL was rejected — fragile (FK targets, timestamps) and
  it would bypass the audit trail.
- Honest caveat recorded in the plan: the dev user's seeded entry is a
  real active entry — an admin can later `/block` it and dev mode will
  correctly refuse to start. That is the gate working as designed.
- Verified end-to-end against the real database: `list` shows the
  owner's seeded admin entry and no dev entry — the exact gap this
  ticket closes; `uv run azure-mini-app-dev seed` is the fix for the
  dev-mode startup refusal, without touching practice history.
- `seed` idempotence pinned behaviorally: the already-active case
  asserts `updated_at` is untouched (nothing written), rather than
  peeking at audit rows — the storage seam already pins what
  `ensure_access`/`set_access_status` audit.
- `reset` deletes the WAL/SHM sidecars along with the main file, then
  reuses `Storage(db_path)` so the normal v2 bootstrap runs (admins
  from `ADMIN_TELEGRAM_IDS`, legacy IDs only if still set) — no
  duplicated seeding logic. With dev mode on it finishes with the
  same `seed` call.
- The audit actor for CLI-arranged entries is NULL (system), matching
  bootstrap semantics: the operator typed a shell command, there is
  no Telegram identity behind it.
- Guard message now: "Run `uv run azure-mini-app-dev seed` to create
  one — it works on fresh and existing databases." (pinned by the
  guard test). README and `.env.example` lead with the admin-first
  bootstrap: `ADMIN_TELEGRAM_IDS` seeds you as the first admin;
  everyone else enters via pending → `/approve`;
  `ALLOWED_TELEGRAM_IDS` is labeled legacy-migration-only.

- Review findings addressed: the ticket's two `## Comments` headings
  merged into one (issue-tracker convention); README's new bootstrap
  prose says "send the Launcher a message" instead of the avoided
  "bot"; `seed_dev_user` builds its `who` string once. The
  "allowlist" wording in the migration docs was kept deliberately —
  it names the pre-v2 concept those IDs came from, which genuinely
  wasn't Access entries. The production `assert user.id is not None`
  was kept: `interactions/miniapp/access.py` has carried the identical
  assert since ticket 03, so this matches prior art.
- Spec review verdict: complete and correct; the one test gap (reset's
  negatives held only implicitly via fixture defaults) got explicit
  pins — reset without dev mode seeds no dev entry, reset without
  legacy IDs seeds only the admin.
