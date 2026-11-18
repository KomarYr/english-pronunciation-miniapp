# English Pronunciation Trainer

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.14%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Node.js](https://img.shields.io/badge/Node.js-24%2B-5FA04E?logo=nodedotjs&logoColor=white)](https://nodejs.org/)
[![Azure Speech](https://img.shields.io/badge/Azure-Speech-0078D4?logo=microsoftazure&logoColor=white)](https://azure.microsoft.com/products/ai-services/speech-service)
[![Telegram Mini App](https://img.shields.io/badge/Telegram-Mini%20App-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots/webapps)
[![SQLite](https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white)](https://sqlite.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](#license)

</div>

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

### 1. Install the prerequisites

You need **Python 3.14+**, **[uv](https://docs.astral.sh/uv/)**, **Node.js
24+** and **[ffmpeg](https://ffmpeg.org)**. `uv` can install Python itself,
so the commands below use `uv python install` on every OS; native package
managers are listed as an alternative.

**Linux (Debian/Ubuntu)**

```bash
# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Python 3.14 (uv-managed)
uv python install 3.14

# Node.js 24+
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt install -y nodejs

# ffmpeg
sudo apt install ffmpeg
```

**macOS (Homebrew)**

```bash
brew install uv
uv python install 3.14      # or: brew install python@3.14
brew install node           # current Homebrew Node is 24+
brew install ffmpeg
```

**Windows (PowerShell)**

```powershell
winget install --id astral-sh.uv -e
uv python install 3.14      # or: winget install --id Python.Python.3.14 -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Gyan.FFmpeg -e
```

Verify the tools are on your `PATH`:

```bash
uv --version
node -v
ffmpeg -version
```

> Ubuntu's default repositories don't ship 3.14. To use a system interpreter
> instead of uv's: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install python3.14 python3.14-venv`.
> NodeSource pins Node 24; anything newer also works.

### 2. Create your Telegram bot

Bots are issued by [@BotFather](https://t.me/BotFather):

1. Open [@BotFather](https://t.me/BotFather), send `/newbot`, pick a display
   name and a username ending in `bot`.
2. Copy the **HTTP API token** into `TELEGRAM_BOT_TOKEN` in `.env`.
3. Once your HTTPS URL is live (step 5), attach the Mini App as the bot's menu
   button — `/setmenubutton` in BotFather, or Bot Settings → Menu Button.
4. Send `/id` to the bot to learn your numeric Telegram ID for
   `ADMIN_TELEGRAM_IDS`.

Further reading: [creating a bot](https://core.telegram.org/bots/features#creating-a-new-bot) ·
[Bot API](https://core.telegram.org/bots/api) ·
[Mini Apps](https://core.telegram.org/bots/webapps).

### 3. Install dependencies

```bash
uv sync
cd frontend && npm install && npm run build && cd ..
```

### 4. Configure `.env`

```bash
cp .env.example .env
# fill in SUBSCRIPTION_KEY, SERVICE_REGION, TELEGRAM_BOT_TOKEN,
# TELEGRAM_WEBHOOK_SECRET and ADMIN_TELEGRAM_IDS
```

### 5. Expose the web server

Any HTTPS tunnel works; Tailscale and cloudflared both ship Linux, macOS and
Windows installers.

```bash
sudo tailscale funnel --bg --https=443 http://localhost:8000
# drop `sudo` on macOS/Windows, then add the printed URL to .env:
echo "MINIAPP_PUBLIC_URL=https://<your-tunnel-host>" >> .env
```

### 6. Run it

```bash
uv run azure-mini-app-web
```

Open the bot in Telegram, press the **Menu button** (or send `/start` and use
the inline button) — the Mini App opens.

### Development without Telegram

Run the Mini App in a plain browser — no bot token and no tunnel:

```bash
echo "MINIAPP_DEV_MODE=1" >> .env
echo "MINIAPP_DEV_USER_ID=123456789" >> .env

uv run azure-mini-app-dev seed    # activate the dev user
uv run azure-mini-app-web
```

Then open <http://127.0.0.1:8000>. Requests run as `MINIAPP_DEV_USER_ID`,
which must hold an active access entry like anyone else.

## Access control lives in the database

The access table is the sole source of truth (schema v2, ADR-0005):
every Telegram ID has an Access entry with a Status (`pending`,
`active`, `blocked`) and a role (`admin`/`user`). Nobody gets in until
an admin approves them; blocking is immediate and reversible, and
every change lands in an audit trail.

**The bootstrap**: put your own Telegram ID in `ADMIN_TELEGRAM_IDS`
(find it via `/id`) — the first run seeds you as an active admin.
After that the database is the gate and everyone else gets in the
same way: open the Mini App or send the Launcher a message →
registered as `pending`, admins notified → an admin runs
`/approve <id>`. `ALLOWED_TELEGRAM_IDS` is only for migrating a
pre-v2 database that already had an allowlist; leave it empty for
new setups.

**Admin commands** (launcher, admins only — invisible to others):

- `/requests` — who is waiting for approval
- `/approve <id>` — activate (or re-activate) someone
- `/block <id>` — revoke access on both surfaces immediately
- `/users` — everyone with their status and role
- `/id` — your own access status

**Developer database tool**: `uv run azure-mini-app-dev` manages the
access database from the shell — `seed` (arrange the dev user's
active entry), `reset` (recreate the DB from the .env seeds, with
confirmation), `list` (print every access entry).

## The chat is only a launcher

The Mini App is the single surface (ADR-0001). The chat keeps `/start`
(the open-app button), the admin commands above, and `/id` as a status
checker. Anything else — voice notes included — is politely refused
with an open-app button; strangers who message the bot are registered
as `pending` and told their request is awaiting approval.

## Using the Mini App

Pick **Practice** → category → exercise, tap **🎤 Start recording**,
read the reference text aloud (up to 2 minutes), and tap **⏹ Stop**.
The report shows:

```
You said: She sells six silver shells.

      89
     Score            ← big number, colored by score

Accuracy      92
Fluency       88
Completeness 100
Prosody       75

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
├── devtools.py               # azure-mini-app-dev CLI: seed/reset/list the DB
├── services/
│   ├── azure_assessment.py   # the ONLY module importing the Speech SDK
│   ├── storage.py            # SQLite: users/exercises/attempts + access/audit (schema v2)
│   ├── quota.py              # monthly 5 h budget policy, warn at 80%
│   ├── access_admin.py       # admin policy behind /requests /approve /block /users
│   ├── auth_throttle.py      # sliding-window throttle on auth failures
│   └── voice.py              # ffmpeg: any container → 16 kHz mono WAV
└── interactions/
    ├── miniapp/              # FastAPI: static frontend + JSON API + webhook
    │   ├── web.py            # one process, three surfaces (ADR-0003)
    │   ├── auth.py           # initData HMAC validation, strict parsing, dev guard
    │   └── api.py            # one route per use-case, typed error codes
    └── telegram.py           # launcher: DB access gate, admin commands, /id status
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
- [ ] M5 — production deployment (VPS/Caddy or Azure Container Apps)

## Built with OpenCode

Created with [OpenCode](https://opencode.ai), the open-source coding agent.

## License

[MIT](LICENSE)
