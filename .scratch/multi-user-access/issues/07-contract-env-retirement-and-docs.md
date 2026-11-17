# 07: Contract — env retirement + docs

**What to build:** The env allowlist era ends cleanly and the decision is
recorded. The old env-based checks are deleted from the auth and launcher
gates; dev mode's fixed dev user must hold an active access entry like anyone
else (dev mode never weakens the gate); startup warns clearly if the legacy
allowlist env var is still set (it now does nothing). A new ADR records the
database-backed access decision: the OWASP cheat-sheet mapping, the stateless
per-request model, the 24h freshness trade-off, and the explicit note that the
Launcher's admin commands deliberately extend ADR-0001's onboarding remit
(superseding its "nothing else" clause on that one point). The glossary
retires "Allowlist" in favor of Access entry, Status and Admin, and docstrings
and docs that referenced the allowlist are renamed consistently.

**Blocked by:** 03, 04, 05, 06.

**Status:** ready-for-agent

- [x] No runtime code path consults the legacy allowlist env var; startup
      warns when it is set.
- [x] Dev mode requires the fixed dev user to have an active access entry;
      the dev-mode validation is rewritten against the access table.
- [x] The ADR is published with the OWASP mapping, the 24h trade-off, and
      the ADR-0001 relationship stated explicitly.
- [x] The glossary's People & access section uses Access entry / Status /
      Admin; "Allowlist" and its avoided-synonyms note are retired.
- [x] Docstrings, launcher docs and settings comments that referenced the
      allowlist use the new vocabulary.
- [x] The whole test suite passes with the legacy env var present (warning
      only) and absent.

## Comments (2026-11-17)

- Runtime gate consumers of `ALLOWED_TELEGRAM_IDS` were already gone
  (tickets 03/04); the last non-gate consumer — `validate_dev_mode` —
  now checks the access table instead. What remains is the
  spec-mandated bootstrap: `_seed_access` (one-time, first run), the
  wiped-data diagnostic, and the new startup warning, all inside
  `Storage.init_schema` so both processes (web + long polling) get it.
- The warning fires whenever the var is set, with wording that is
  true in both cases: it seeds once at first run, the live gate is
  the database, and the setting can be removed after the first start.
  Pinned by tests: fresh DB (seeds AND warns), migrated DB (warns,
  seeds nothing), absent (quiet).
- `validate_dev_mode(storage)` — dev mode is now arranged exactly the
  way a real person's access is: the dev user must HOLD an active entry
  (bootstrap seed via `ALLOWED_TELEGRAM_IDS`, or `/approve`). Pending,
  blocked or missing refuses to start — a blocked dev user cannot be
  bypassed by dev mode. `create_app` creates Storage first, then
  validates. The old test pinning "dev user registers as pending"
  became impossible by design and was rewritten to pin the startup
  refusal; the stranger-registers-pending flow stays pinned in
  `test_miniapp_access.py` with real initData.
- `ALLOWED_TELEGRAM_IDS` itself stays in the contract: it is the
  one-time bootstrap seed the spec keeps ("The .env file keeps only a
  one-time bootstrap seed"). Settings comments and `.env.example` now
  describe it as seed-only. `_parse_allowed_telegram_ids` and its
  parsing tests stay (renamed `TestBootstrapSeedParsing`).
- ADR-0005 (`docs/adr/0005-database-backed-access.md`): the decision,
  the four cheat-sheet mappings (Authentication, Authorization,
  Cryptographic/User Data Storage, REST statelessness), the 24h
  freshness trade-off, and the explicit ADR-0001 supersession ("the
  admin commands deliberately extend the onboarding remit; the
  'nothing else' clause is superseded on this one point"). ADR-0001
  itself is untouched — historical records record their time; the new
  ADR supersedes, it doesn't rewrite.
- Glossary: "Allowlist" retired; Access entry, Status and Admin added
  (each with _Avoid_ aliases — allowlist/whitelist now among them);
  the Launcher entry notes the extended remit with the ADR pointer.
- README: new "Access control lives in the database" section (the
  pending→approve flow, admin commands, seed-only env story); the
  dev-mode paragraph and the architecture tree updated.
- Suite verified with the legacy var present (`ALLOWED_TELEGRAM_IDS`
  exported) and absent — 243 passing both ways; fixtures isolate their
  own env so the var can only ever appear as the startup warning.

- Review findings addressed: `_seed_access`'s docstring no longer says
  "env allowlist" (glossary-avoided term); ADR-0005 gained its
  `## Considered options` section (house format — env-allowlist-as-gate,
  sessions/JWTs, tighter freshness all recorded as rejected); the two
  ticket-02 tests that a careless insertion had re-filed under
  `TestLegacyEnvWarning` moved back into `TestAccessEntries`;
  `TestDevModeGuard` deduplicated (fixture + parametrized
  pending/blocked exits).
- Review finding — the dev-mode seed advice was accurate only on a
  fresh database (the bootstrap seed never runs on an already-v2 DB,
  and no admin command can *create* an entry for the dev user). The
  exit message, README and `.env.example` now say it plainly: seed on
  a fresh DB before the first run, else start from a fresh DB file —
  dev data is disposable. The glossary's Admin entry dropped its
  unbacked "promoted afterwards only by another admin" claim (no
  promotion mechanism exists; `set_access_role` is a storage API
  without a command surface).
