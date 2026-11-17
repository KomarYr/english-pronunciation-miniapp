# Access control lives in the database, not .env

Who may use the trainer is data, not configuration: schema v2 adds an
**Access entry** per Telegram ID — a Status (`pending`, `active`,
`blocked`) and a role (`admin`, `user`) — plus an append-only audit
table. Closed by default: a stranger with valid Telegram `initData` is
auto-registered as `pending`, admins are notified, and nobody gets in
until an admin approves them from the chat (`/requests`, `/approve`,
`/block`, `/users`). `ALLOWED_TELEGRAM_IDS` is demoted to a one-time
bootstrap seed (its presence now warns at startup); after the first
run the database is the sole source of truth. Both surfaces re-read the
access table on every update/request, so a block takes effect
immediately, mid-session.

## Considered options

- **Keep the env allowlist as the live gate (rejected)**: the list of
  people would live in a config file instead of alongside the data it
  governs — no way to see, revoke or audit access without an edit and
  a restart, and no notion of who administers it.
- **Server-issued sessions or JWTs (rejected)**: they would trade the
  stateless verify-per-request model for token storage, expiry logic
  and a second validation path — the signed `initData` already carries
  a verified identity on every request.
- **Tighter `initData` freshness (rejected)**: a window much shorter
  than 24 h would log people out mid-practice-session, because
  Telegram regenerates the payload only on app reopen; a day bounds a
  stolen payload's replay while keeping sessions usable.

## OWASP cheat-sheet mapping

The hardening follows four cheat sheets, applied as constraints rather
than imported wholesale:

- **Authentication**: every request re-verifies the signed `initData`
  (HMAC-SHA256 keyed with the bot token, constant-time comparison).
  Parsing is strict — oversized, malformed or duplicate-field payloads
  are rejected before the HMAC runs; error bodies are generic stable
  codes (internal reasons go to logs and the audit table); repeated
  failures hit a per-IP sliding-window throttle answered with a
  detail-free 429.
- **Authorization**: deny by default, decided server-side on every
  request from the database — never from a header, a cached session, or
  the client.
- **Cryptographic / User Data Storage**: no secrets are stored; the
  database holds identities and statuses only.
- **REST Security (statelessness)**: no sessions, cookies or JWTs.
  Each request carries its own verified identity; the server keeps no
  per-client auth state beyond the failure throttle.

## The 24-hour freshness trade-off

`initData` older than 24 h is rejected. Telegram regenerates the
payload only when the Mini App is reopened, so a tighter window would
log people out mid-practice-session for a personal tool; 24 h bounds a
stolen payload's replay to the same day it was captured. That is a
deliberate usability-over-strictness trade, recorded here.

## Relationship to ADR-0001

ADR-0001 demoted the Launcher to "opens the Mini App and onboards
strangers, nothing else". The admin commands deliberately extend that
onboarding remit — approving and blocking people *is* onboarding — so
ADR-0001's "nothing else" clause is superseded on this one point and
unchanged everywhere else.

## Consequences

- Adding a person is one chat command instead of editing `.env` and
  restarting; revocation is immediate and reversible, with an audit
  trail of who changed what, when.
- The access/audit tables require a versioned migration (v1 → v2,
  driven by SQLite's `user_version`) and one-time env seeding so
  existing users and the first admin survive the upgrade.
- Dev mode no longer weakens the gate: the fixed dev user must hold an
  active Access entry like anyone else (arranged via the bootstrap
  seed), checked at startup.
- `initData` validation failure costs one HMAC and one audit row per
  request — cheap for a personal tool, and the throttle keeps it that
  way under probing.
