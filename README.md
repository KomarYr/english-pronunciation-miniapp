# English Pronunciation Trainer

A personal English-practice tool built on **Azure Speech pronunciation
assessment**: read an exercise aloud, get a per-word report, and weak
exercises automatically land in your **mistake bank** so you know exactly
what to practice next.

Everything happens in the **Telegram Mini App** (ADR-0001): pick an
exercise, read it aloud into the phone's microphone, and get a per-word
report. The chat is just the launcher.

## Features

- **Graded reading practice** — pronunciation, accuracy, fluency,
  completeness and prosody scores, plus word-level feedback
  (mispronounced / skipped / extra words)
- **Built-in exercise catalog** — themed categories with reference texts
  (s/sh minimal pairs, tongue twisters, paragraphs…)
- **Your own texts** — paste any English text (10–50 words) and practice
  it; it is saved automatically and can be retried later
- **Mistake bank** — any exercise averaging below 70/100 shows up as weak;
  practice it well and it leaves the bank on its own (nothing to manage)
- **Monthly audio budget** — Azure's free tier allows 5 audio-hours per
  subscription per month; every second of audio (including silent
  attempts) is tracked in a local ledger, with a warning at 80% and a
  hard stop when the budget is gone

## Quick start

Requires Python 3.14+, [uv](https://docs.astral.sh/uv/), Node 24+ and
[ffmpeg](https://ffmpeg.org) (`sudo apt install ffmpeg` on Ubuntu).

```bash
# 1. Install dependencies (backend + frontend)
uv sync
cd frontend && npm install && npm run build && cd ..

# 2. Add your Azure Speech credentials and the bot token to .env
cat > .env <<'EOF'
SUBSCRIPTION_KEY=your-azure-speech-key
SERVICE_REGION=your-region            # e.g. westeurope
# SERVICE_ENDPOINT=...                # optional, overrides SERVICE_REGION
TELEGRAM_BOT_TOKEN=123456:ABC-your-token
TELEGRAM_WEBHOOK_SECRET=some-random-string
ALLOWED_TELEGRAM_IDS=                 # comma-separated numeric IDs; empty = nobody
EOF

# 3. Expose the web server (local dev): any HTTPS tunnel works,
#    e.g. tailscale funnel or cloudflared
tailscale funnel --bg --https=443 http://localhost:8000

# 4. Put that URL into .env so the bot registers its webhook there
echo "MINIAPP_PUBLIC_URL=https://<your-tunnel-host>" >> .env

# 5. Run the server — Mini App + API + Telegram webhook in one process
uv run azure-mini-app-web
```

Open the bot in Telegram, press the **Menu button** (or send `/start`
and use the inline button) — the Mini App opens.

**Development without Telegram**: set `MINIAPP_DEV_MODE=1` and open
`http://127.0.0.1:8000` in a plain browser; requests run as
`MINIAPP_DEV_USER_ID` (which must be in `ALLOWED_TELEGRAM_IDS` — the
allowlist stays closed even in dev mode).

## The chat is only a launcher

The Mini App is the single surface (ADR-0001). The chat keeps exactly:

- `/start` — greeting + the open-app button
- `/id` — shows your numeric Telegram ID (works without allowlisting)

New users: send `/id` to the bot, then the owner appends the ID to
`ALLOWED_TELEGRAM_IDS` in `.env` and restarts the server. Nobody else
gets anything — voice notes included; they are politely refused with an
open-app button.

## Using the Mini App

Pick **Practice** → category → exercise, wait for the 3-2-1 countdown,
and read the reference text aloud. The report shows:

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

- **Practice my own text** — paste an English text of 10–50 words; it is
  validated, saved as your personal exercise, and assessed immediately.
  The same text is never saved twice.
- **Mistake bank** — your weakest exercises, lowest average score first.
- **Stats** — attempts, practice time, average score and the monthly
  audio budget with a ⚠ warning at 80%; at 100% practice is blocked
  until the 1st of the next month.

## For developers

Domain language lives in `GLOSSARY.md`; architectural decisions in
`docs/adr/`.

### Architecture

One core with thin interaction adapters — the Mini App and the chat
launcher only render; all rules live in `app.py`:

```
src/azure_mini_app/
├── settings.py               # .env credentials, language, guardrail limits
├── exercises.py              # built-in catalog (dataclass + seed data)
├── domain.py                 # User, Attempt, ScoreReport, WordScore — pure stdlib
├── app.py                    # use-cases: quota gate, guardrails, mistake bank
├── services/
│   ├── azure_assessment.py   # the ONLY module importing the Speech SDK
│   ├── storage.py            # SQLite: users/exercises/attempts + audio_usage ledger
│   ├── quota.py              # monthly 5 h budget policy, warn at 80%
│   └── voice.py              # ffmpeg: any container → 16 kHz mono WAV
└── interactions/
    ├── miniapp/              # FastAPI: static frontend + JSON API + webhook
    │   ├── web.py            # one process, three surfaces (ADR-0003)
    │   ├── auth.py           # initData HMAC validation + allowlist
    │   └── api.py            # one route per use-case, typed error codes
    └── telegram.py           # launcher: /start, /id, voice-note refusal
frontend/                     # vanilla TS + Vite Mini App (ADR-0004)
├── src/api.ts                # typed fetch wrapper (tma initData header)
├── src/recorder.ts           # MediaRecorder: WebM/Opus, 120 s cap
├── src/screens/              # practice, report, mistakes, stats
└── src/styles.css            # Telegram theme variables + dark fallbacks
```

Key design decisions:

- **The Mini App records, the backend assesses** (ADR-0002): audio is
  captured in the browser and POSTed as raw bytes; the Speech SDK stays
  server-side and the typed error contract is unchanged.
- **Typed errors as the API contract.** `assess()` raises
  `NoSpeechDetected` (carrying `audio_seconds`), `AssessmentCanceled`
  (carrying reason + details + seconds) and `TextValidationError`;
  `ensure_can_assess()` raises `QuotaExceededError` (carrying the status).
  Adapters catch and render — no `None`-squinting. The JSON API maps them
  to stable codes (`no_speech`, `quota_exceeded`, …).
- **The quota ledger is the source of truth.** Azure bills silent
  attempts, so the `audio_usage` table records every transaction —
  `graded`, `no_speech` and `canceled` alike. `Attempt.audio_seconds` is
  only a display stat.
- **Mistake bank is derived, not stored.** Weakness is
  `AVG(pronunciation_score) < 70` over attempts, so it self-heals.
- **Custom texts dedupe by hash.** The exercise id is
  `custom-<sha1(normalized text)[:10]>` — the same text is saved once.
- **One process** (ADR-0003): web + API + webhook share a single SQLite
  writer; the adapters are wired independently so splitting into two
  processes later is an ops change, not a code rewrite.

### Database

SQLite at `data/app.db` (gitignored), created and seeded automatically on
first run. Tables: `users`, `exercises` (builtin + custom), `attempts`
(with a `reference_text` snapshot so history stays truthful) and the
`audio_usage` quota ledger. WAL mode + foreign keys on.

### Tests and checks

```bash
uv run pytest -q         # no Azure account or bot token needed
uv run ruff check .
uv run ruff format --check .
uv run ty check .
cd frontend && npm run build   # tsc --noEmit + vite build
```

The Azure call path itself is a thin shell around
`recognize_once_async().get()` — everything else (result → domain
mapping, quota policy, storage, auth, API) is covered by unit tests with
fakes.

## Roadmap

- [x] M1 — package layout, catalog, domain model
- [x] M2 — Telegram: voice notes → WAV → same use-cases, HTML reports
- [x] M3 — storage, quota ledger, Azure assessment, use-cases
- [x] M4 — Telegram Mini App replaces chat interactions (ADR-0001)
- [ ] M6 — production deployment (VPS/Caddy or Azure Container Apps)
