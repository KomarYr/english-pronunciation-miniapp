# Spec: Multi-user access control (database-backed, OWASP-aligned)

Status: ready-for-agent

## Problem Statement

Adding a new user to the trainer is a manual, multi-step dance: the
stranger runs `/id` in the Launcher, copies their Telegram ID, sends it
to the bot owner, who edits `ALLOWED_TELEGRAM_IDS` in `.env` and
restarts the process. The list of people allowed to use the tool lives
in a config file instead of alongside the data it governs, so there is
no way to see who has access, no way to revoke it, no notion of who
administers it, and no record of when access was granted or denied.

## Solution

Access control moves into the SQLite database. Every person who
authenticates with valid Telegram `initData` gets an **Access entry**
with a **Status** (`pending`, `active`, `blocked`) and a **Role**
(`admin`, `user`). Closed-by-default semantics are preserved: nobody
gets in until an Admin moves them to `active`. Admins manage access
with Launcher commands (`/requests`, `/approve`, `/block`, `/users`)
and get notified when a stranger requests access. The `.env` file keeps
only a one-time bootstrap seed; after the first run the database is the
sole source of truth. The Mini App API request authentication is
hardened along OWASP Cheat Sheet lines: strict `initData` parsing,
generic auth error bodies, rate limiting on repeated auth failures,
and a security audit log.

## User Stories

1. As a stranger, I want to open the Mini App and be registered as
   pending automatically, so that I don't have to copy a numeric ID to
   anyone to be considered for access.
2. As a stranger, I want a clear "waiting for approval" message, so
   that I know my request was recorded and access is not instant.
3. As a stranger, I want a distinct "you were blocked" message, so that
   I don't wait for an approval that will never come.
4. As an Admin, I want a notification when someone requests access, so
   that I can approve them without polling the bot.
5. As an Admin, I want `/requests` to list all pending users with their
   Telegram IDs and display names, so that I can decide whom to let in.
6. As an Admin, I want `/approve <id>` to activate a pending or blocked
   user, so that granting access is one command instead of editing
   `.env` and restarting.
7. As an Admin, I want `/block <id>` to revoke access, so that a
   former user loses both Mini App and Launcher entry immediately.
8. As an Admin, I want `/users` to list everyone with their status and
   role, so that I can see the whole access picture in one place.
9. As an Admin, I want admin commands to do nothing for non-admin
   users, so that only authorized people manage access.
10. As an Admin, I want to be prevented from blocking the last admin,
    so that the tool can never lock itself out of its own admin
    surface.
11. As an Admin, I want approve/block actions recorded with timestamps
    in an audit log, so that I can reconstruct who changed access and
    when.
12. As an active User, I want my attempts, custom exercises and stats
    to survive being blocked and later re-approved, so that revocation
    is reversible without data loss.
13. As an active User, I want access checked on every API request, so
    that a block takes effect immediately even mid-session.
14. As the bot owner, I want existing `ALLOWED_TELEGRAM_IDS` users to
    migrate into the database as active, so that nobody loses access
    when the new system ships.
15. As the bot owner, I want `ADMIN_TELEGRAM_IDS` to seed admin rows
    once at startup, so that the chicken-and-egg problem of "the first
    admin can't approve themselves" is solved deterministically.
16. As the bot owner, I want the database to be the sole source of
    truth after the first run, so that `.env` no longer carries any
    live access control.
17. As the bot owner, I want a warning at startup if
    `ALLOWED_TELEGRAM_IDS` is still set, so that I know the legacy
    setting is no longer doing anything.
18. As the bot owner, I want failed and denied authentication attempts
    logged, so that I can notice probing or abuse.
19. As a developer, I want `initData` parsing to reject malformed
    input (bad encoding, duplicate keys, oversized payloads) instead of
    silently taking the first match, so that parser confusion can't
    bypass validation.
20. As a developer, I want authentication error responses to be
    generic, so that internal reasons are not leaked to clients.
21. As a developer, I want repeated auth failures to trigger a generic
    429, so that the auth surface can't be hammered.
22. As a developer, I want `initData` freshness checked (24h), so that
    a stolen payload has a bounded replay window.
23. As a developer, I want login to match users on `telegram_id` only,
    so that two people with the same display name can never collide
    into one account.
24. As a developer, I want the schema change to be a versioned
    migration, so that existing databases upgrade in place.
25. As a User in dev mode, I want the fixed dev user to still require
    an active access entry, so that dev mode never weakens the gate.

## Implementation Decisions

- **Access table** (new, schema version 2): one row per Telegram ID,
  with `status` (`pending` / `active` / `blocked`), `role`
  (`admin` / `user`), created/updated timestamps, and a foreign key to
  the user row. The old env allowlist is retired; "allowlist" checks
  become status checks.
- **Migration machinery**: the storage layer gains versioned schema
  migration driven by SQLite's `user_version` pragma. Version 1 → 2
  creates the access table and seeds it: every Telegram ID in the
  legacy env allowlist becomes `active`; every ID in
  `ADMIN_TELEGRAM_IDS` becomes `active` with role `admin`. Seeding is
  one-time at startup; afterwards the database is the source of truth
  and the legacy env var triggers only a startup warning.
- **Stateless authentication kept**: every API request re-verifies the
  signed `initData` (HMAC-SHA256 with the bot token, constant-time
  comparison). No sessions, JWTs or cookies are introduced. Freshness
  window stays at 24 hours — Telegram regenerates `initData` only on
  app reopen, and a personal practice tool prefers session continuity
  over a tighter replay window (recorded in the ADR).
- **Strict `initData` parsing**: reject malformed percent-encoding,
  duplicate keys (including duplicate `hash`), and oversized payloads;
  never silently repair or take the first match.
- **Generic auth errors**: 401/403 response bodies carry stable codes
  (`auth_failed`, `auth_required`, `pending`, `blocked`,
  `not_admin`) without internal detail; internal reasons go to server
  logs.
- **Rate limiting**: a sliding-window limiter keyed per identity/IP on
  authentication failures returns a generic 429 without diagnostic
  detail.
- **Security audit log** (new table): records denied access, pending
  registrations, and admin approve/block actions, each with actor,
  subject and timestamp.
- **Login fix**: user resolution matches on `telegram_id` only; the
  `OR display_name` match that would merge same-named users is
  removed.
- **Lifecycle**: strangers with valid `initData` are auto-created as
  `pending` (user row + pending access entry) and the bot notifies
  admins; `pending` and `blocked` both get 403 with distinct codes;
  blocked users keep their attempts and exercises; `/approve` of a
  blocked user re-activates them; blocking the last remaining admin is
  rejected.
- **Launcher**: the update middleware consults the database access
  table (`/start` and `/id` still bypass; `/id` becomes a status
  checker). Admin commands `/requests`, `/approve <id>`, `/block
  <id>`, `/users` are enforced admin-only; handlers stay thin over a
  small access-administration layer. This extends the Launcher's
  "onboarding strangers" remit from ADR-0001 — the ADR for this
  feature notes the relationship explicitly.
- **Admin notification**: when a pending user is created, admins get a
  Telegram message (sent directly through the Bot API; works in both
  webhook and long-polling modes).
- **Dev mode semantics unchanged**: the fixed dev user must hold an
  active access entry like anyone else.
- **Docs ride along**: a new ADR records the database-backed access
  decision, its OWASP mapping (Authentication, Authorization,
  Cryptographic/User Data Storage cheat sheets, REST Security
  statelessness) and the 24h trade-off; `GLOSSARY.md` retires
  "Allowlist" in favor of Access entry, Status and Admin.

## Testing Decisions

- A good test asserts external behavior at a seam: HTTP status and
  response body codes at the API boundary, stored rows at the storage
  boundary, bot reply text at the handler boundary — never internal
  call graphs.
- **Seam 1 — HTTP API** (prior art: the existing Mini App API tests
  driving `create_app()` with FastAPI's TestClient): valid /
  missing / garbage / stale `initData`, pending and blocked 403s with
  distinct codes, auto-creation of pending users, admin notification
  (Bot mocked), rate-limit 429, generic error bodies.
- **Seam 2 — Storage** (prior art: existing storage tests with a
  temp SQLite file): v1→v2 migration and seeding, access CRUD,
  last-admin protection, audit-log writes, the telegram_id-only login
  fix.
- **Seam 3 — Launcher handlers/middleware** (prior art: existing
  launcher tests with mocked aiogram objects): DB-backed gate, admin
  command gating against real storage with a mocked `Message`.
- **Seam 4 — `initData` validation as a pure function** (prior art:
  existing auth tests): strict-parsing rejections, 24h staleness,
  and a pinned real-world payload containing a `signature` field as a
  test vector.
- **First task before anything else**: capture a real `initData`
  payload and settle whether modern Telegram clients' `signature`
  field belongs inside the bot-token HMAC data-check string. Sources
  conflict; the current validator is written the pre-8.0 way. The
  vector pins the correct behavior and may force a one-line fix to the
  data-check-string construction.

## Out of Scope

- Server-issued sessions, JWTs, refresh tokens or cookies.
- Non-Telegram identities (passwords, email, OAuth).
- An admin management screen in the Mini App frontend.
- A test harness for the vanilla-TS frontend (the pending/blocked
  screens are verified manually against the 403 code contract).
- Database encryption at rest; no secrets or credentials are stored in
  the database.
- Ed25519 third-party `initData` validation (first-party HMAC only).
- Notification retry/queueing beyond a single best-effort send.
- Bulk import or CSV-style access management.

## Further Notes

- The OWASP framing is a constraint on review, not a mandate to
  import: the stateless per-request model already matches REST
  Security guidance ("validate on every request"), and the additions
  above close the deltas (generic errors, strict parsing, throttling,
  audit trail).
- ADR conflict to note in the new ADR: ADR-0001 demoted the Launcher
  to "opens the Mini App and onboards strangers, nothing else";
  admin access commands are being added there deliberately, framed as
  the onboarding remit, so ADR-0001's "nothing else" clause is
  superseded on that one point.
- The feature touches the People & access section of the glossary;
  "Allowlist" is referenced in the Launcher docs and the
  `interactions` docstrings and should be renamed consistently as
  part of the build.
