# English Pronunciation Trainer

A personal English-practice tool built on **Azure Speech pronunciation
assessment**: read an exercise aloud, get a per-word report, and weak
exercises automatically land in your **mistake bank** so you know exactly
what to practice next.

Works from the terminal (this repo) and — planned — from a Telegram bot
that accepts voice notes.

## Features

- **Graded reading practice** — pronunciation, accuracy, fluency,
  completeness and prosody scores, plus word-level feedback
  (mispronounced / skipped / extra words)
- **Built-in exercise catalog** — themed categories with reference texts
  (s/sh minimal pairs, tongue twisters, paragraphs…)
- **Your own texts** — paste any English text (10–50 words) and practice it;
  it is saved automatically and can be retried later
- **Mistake bank** — any exercise averaging below 70/100 shows up as weak;
  practice it well and it leaves the bank on its own (nothing to manage)
- **Monthly audio budget** — Azure's free tier allows 5 audio-hours per
  subscription per month; every second of audio (including silent
  attempts) is tracked in a local ledger, with a warning at 80% and a
  hard stop when the budget is gone

## Quick start

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Install dependencies
uv sync

# 2. Add your Azure Speech credentials to .env
cat > .env <<'EOF'
SUBSCRIPTION_KEY=your-azure-speech-key
SERVICE_REGION=your-region            # e.g. westeurope
# SERVICE_ENDPOINT=...                # optional, overrides SERVICE_REGION
EOF

# 3. Run the trainer
uv run speech-project-azure
```

There is also `uv run speech-project-azure-demo` — the original minimal
microphone demo, kept for debugging audio setup.

### Microphone note (WSL2)

The mic works through WSLg's PulseAudio. If the tool hears nothing, check
`~/.asoundrc` routes ALSA to Pulse (this project was set up on WSL2 and
verified end-to-end there). Plain Linux and Windows should work out of
the box.

## Using the CLI

Pick **Practice an exercise**, choose a category and an exercise, wait for
the 3-2-1 countdown, and read the reference text aloud. The report shows:

```
You said: She sells six silver shells.
Pronunciation: 89/100
  accuracy      92.0
  fluency       88.0
  completeness 100.0
  prosody       75.0

Words to work on:
  shells          ✗ said wrong    60
```

- **Practice my own text** — type an English text of 10–50 words; it is
  validated, saved as your personal exercise, and assessed immediately.
  The same text is never saved twice.
- **Mistake bank** — your weakest exercises, lowest average score first.
- **Stats** — attempts, practice time, average score and the monthly
  audio budget. You get a ⚠ warning at 80% of the budget; at 100%
  practice is blocked until the 1st of the next month.

## For developers

### Architecture

One core with thin interaction adapters — the CLI and the (future)
Telegram bot only render; all rules live in `app.py`:

```
src/speech_project_azure/
├── settings.py               # .env credentials, language, guardrail limits
├── exercises.py              # built-in catalog (dataclass + seed data)
├── domain.py                 # User, Attempt, ScoreReport, WordScore — pure stdlib
├── app.py                    # use-cases: quota gate, guardrails, mistake bank
├── recognizer.py             # legacy mic demo (speech-project-azure-demo)
├── services/
│   ├── azure_assessment.py   # the ONLY module importing the Speech SDK
│   ├── storage.py            # SQLite: users/exercises/attempts + audio_usage ledger
│   └── quota.py              # monthly 5 h budget policy, warn at 80%
└── interactions/
    └── cli.py                # terminal menu → microphone → report
```

Key design decisions:

- **Typed errors as the API contract.** `assess()` raises
  `NoSpeechDetected` (carrying `audio_seconds`), `AssessmentCanceled`
  (carrying reason + details + seconds) and `TextValidationError`;
  `ensure_can_assess()` raises `QuotaExceededError` (carrying the status).
  Adapters catch and render — no `None`-squinting.
- **The quota ledger is the source of truth.** Azure bills silent
  attempts, so the `audio_usage` table records every transaction —
  `graded`, `no_speech` and `canceled` alike. `Attempt.audio_seconds` is
  only a display stat.
- **Mistake bank is derived, not stored.** Weakness is
  `AVG(pronunciation_score) < 70` over attempts, so it self-heals.
- **Custom texts dedupe by hash.** The exercise id is
  `custom-<sha1(normalized text)[:10]>` — the same text is saved once.
- **One recognizer per attempt**, connections opened per storage
  operation — safe to call from worker threads (the Telegram bot will
  wrap sync use-cases with `asyncio.to_thread`).

### Database

SQLite at `data/app.db` (gitignored), created and seeded automatically on
first run. Tables: `users`, `exercises` (builtin + custom), `attempts`
(with a `reference_text` snapshot so history stays truthful) and the
`audio_usage` quota ledger. WAL mode + foreign keys on.

### Tests and checks

```bash
uv run pytest -q        # 77 tests, no Azure account needed (fakes + tmp DBs)
uv run ruff check .
uv run ruff format --check .
uv run ty check .
```

The Azure call path itself is a thin shell around
`recognize_once_async().get()` — everything else (result → domain
mapping, quota policy, storage) is covered by unit tests with fakes.

## Roadmap

- [x] M1 — package layout, catalog, domain model
- [x] M3 — storage, quota ledger, Azure assessment, use-cases, CLI
- [ ] M5 — Telegram bot: voice notes → WAV → same use-cases
