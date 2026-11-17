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

- [ ] No runtime code path consults the legacy allowlist env var; startup
      warns when it is set.
- [ ] Dev mode requires the fixed dev user to have an active access entry;
      the dev-mode validation is rewritten against the access table.
- [ ] The ADR is published with the OWASP mapping, the 24h trade-off, and
      the ADR-0001 relationship stated explicitly.
- [ ] The glossary's People & access section uses Access entry / Status /
      Admin; "Allowlist" and its avoided-synonyms note are retired.
- [ ] Docstrings, launcher docs and settings comments that referenced the
      allowlist use the new vocabulary.
- [ ] The whole test suite passes with the legacy env var present (warning
      only) and absent.
