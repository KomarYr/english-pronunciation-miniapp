# English Pronunciation Trainer

A personal English-practice tool built on **Azure Speech pronunciation
assessment**: read an exercise aloud, get a per-word report, and weak
exercises automatically land in your **mistake bank** so you know exactly
what to practice next.

Works from the terminal and from a **Telegram bot** that accepts voice notes.

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
uv run azure-mini-app
```

There is also `uv run azure-mini-app-demo` — the original minimal
microphone demo, kept for debugging audio setup.

## Telegram bot

The bot reuses the exact same use-cases as the CLI — same database, same
profiles, same quota. Telegram login matches on `telegram_id`.

### Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token
2. Add to `.env`:

   ```ini
   TELEGRAM_BOT_TOKEN=123456:ABC-your-token
   ALLOWED_TELEGRAM_IDS=            # comma-separated numeric IDs; empty = nobody
   ```

3. `uv sync` (needs [ffmpeg](https://ffmpeg.org) on the system for
   voice-note conversion — `sudo apt install ffmpeg` on Ubuntu)
4. Run it: `uv run azure-mini-app-telegram` (long polling, no public URL needed)

### Adding users (closed by default)

The allowlist is closed: nobody can use the bot until their ID is in
`ALLOWED_TELEGRAM_IDS`. To add someone:

1. They open a chat with the bot and send `/id`
2. The bot replies with their numeric Telegram ID
3. Append it to `ALLOWED_TELEGRAM_IDS` in `.env` (comma-separated) and
   restart the bot

Only `/start` and `/id` work for non-allowed users; everything else is
rejected with their ID as a hint.

### Commands

| Command | What it does |
|---|---|
| `/practice` | pick category → exercise (inline buttons), then send a voice note |
| `/text` | practice your own English text (10–50 words), then send a voice note |
| `/mistakes` | your weakest exercises with average scores |
| `/stats` | attempts, practice time, average score, audio budget |
| `/id` | show your Telegram ID (works without allowlisting) |
| `/cancel` | cancel the current action |

Voice notes are converted to 16 kHz mono WAV with ffmpeg before
assessment; recordings longer than 2 minutes are refused.

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

One core with thin interaction adapters — the CLI and the Telegram bot
only render; all rules live in `app.py`:

```
src/azure_mini_app/
├── settings.py               # .env credentials, language, guardrail limits, bot token
├── exercises.py              # built-in catalog (dataclass + seed data)
├── domain.py                 # User, Attempt, ScoreReport, WordScore — pure stdlib
├── app.py                    # use-cases: quota gate, guardrails, mistake bank
├── recognizer.py             # legacy mic demo (azure-mini-app-demo)
├── services/
│   ├── azure_assessment.py   # the ONLY module importing the Speech SDK
│   ├── storage.py            # SQLite: users/exercises/attempts + audio_usage ledger
│   ├── quota.py              # monthly 5 h budget policy, warn at 80%
│   └── voice.py              # ffmpeg: Telegram OGG/Opus → 16 kHz mono WAV
└── interactions/
    ├── cli.py                # terminal menu → microphone → report
    ├── telegram.py           # aiogram 3 bot: voice notes → same use-cases
    └── rendering.py          # report formatters: plain text (CLI), HTML (Telegram)
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
  operation — safe to call from worker threads (the Telegram bot wraps
  sync use-cases with `asyncio.to_thread`).

### Database

SQLite at `data/app.db` (gitignored), created and seeded automatically on
first run. Tables: `users`, `exercises` (builtin + custom), `attempts`
(with a `reference_text` snapshot so history stays truthful) and the
`audio_usage` quota ledger. WAL mode + foreign keys on.

### Tests and checks

```bash
uv run pytest -q        # 107 tests, no Azure account or bot token needed
uv run ruff check .
uv run ruff format --check .
uv run ty check .
```

The Azure call path itself is a thin shell around
`recognize_once_async().get()` — everything else (result → domain
mapping, quota policy, storage) is covered by unit tests with fakes.

## Telegram bot internals

The adapter lives in `interactions/telegram.py` (aiogram 3):

- **FSM states** — `waiting_for_text` and `waiting_for_voice`; which
  exercise/text is pending rides in the state data (in-memory storage —
  a bot restart just cancels an in-progress attempt)
- **Threading** — every storage/Azure call goes through
  `asyncio.to_thread`; storage opens a connection per operation, so
  parallel handlers are safe
- **Allowlist** — one middleware (`AllowlistMiddleware`) gates all
  updates; handlers never think about permissions
- **Reports** — the pretty HTML messages are built in
  `interactions/rendering.py` (`render_report_html`, `render_stats_html`,
  `render_mistakes_html`); all user-generated strings are HTML-escaped

## Roadmap

- [x] M1 — package layout, catalog, domain model
- [x] M2 — Telegram bot: voice notes → WAV → same use-cases, HTML reports
- [x] M3 — storage, quota ledger, Azure assessment, use-cases, CLI
- [ ] M5 — Telegram bot formatting and image cards
