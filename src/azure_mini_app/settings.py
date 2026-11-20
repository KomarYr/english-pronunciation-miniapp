import sys
from os import environ
from pathlib import Path

from dotenv import load_dotenv

# Where read-only project files live (frontend dist, .env). Overridable so a
# packaged install does not depend on this file's location.
PROJECT_ROOT = Path(environ.get("APP_ROOT") or Path(__file__).resolve().parents[2])
load_dotenv(PROJECT_ROOT / ".env")

speech_key: str | None = environ.get("SUBSCRIPTION_KEY")
speech_region: str | None = environ.get("SERVICE_REGION")
speech_endpoint: str | None = environ.get("SERVICE_ENDPOINT")

# Target language of the practice exercises.
language = "en-US"

# Writable state. Separate from PROJECT_ROOT on purpose: the project tree can
# be read-only inside a container, while this directory is a volume.
# `or` matters — a set-but-empty DATA_DIR must fall back, not mean cwd.
data_dir = Path(environ.get("DATA_DIR") or PROJECT_ROOT / "data")

# SQLite database location (gitignored).
db_path = data_dir / "app.db"

# Telegram bot. Leave TELEGRAM_BOT_TOKEN empty to run the web server
# without Telegram wiring (dev mode only).
telegram_bot_token: str | None = environ.get("TELEGRAM_BOT_TOKEN")

# Telegram sends updates with this secret so we can reject impostors.
telegram_webhook_secret: str | None = environ.get("TELEGRAM_WEBHOOK_SECRET")


def _int_env(name: str, default: int) -> int:
    """Parse an integer env var, exiting with a clear message on garbage."""
    raw = environ.get(name, str(default))
    try:
        return int(raw)
    except ValueError:
        sys.exit(f"{name} must be an integer, got {raw!r}")


# Mini App web server (uvicorn). MINIAPP_PUBLIC_URL (e.g. a tailscale
# tunnel) triggers set_webhook on startup — the bot's polling replacement.
miniapp_host = environ.get("MINIAPP_HOST", "127.0.0.1")
miniapp_port = _int_env("MINIAPP_PORT", 8000)
miniapp_public_url: str | None = environ.get("MINIAPP_PUBLIC_URL")

# Dev mode: a plain browser (no Telegram) talks to the API as this fixed
# user. Localhost convenience only — the dev user must still hold an
# active access entry like anyone else.
miniapp_dev_mode = environ.get("MINIAPP_DEV_MODE", "").lower() in ("1", "true", "yes")
miniapp_dev_user_id = _int_env("MINIAPP_DEV_USER_ID", 900_000_001)
miniapp_dev_user_name = environ.get("MINIAPP_DEV_USER_NAME", "Dev Browser User")


def _parse_telegram_ids(env_name: str) -> frozenset[int]:
    """Parse e.g. ADMIN_TELEGRAM_IDS=123,456; empty → nobody."""
    raw = environ.get(env_name, "")
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    try:
        return frozenset(int(part) for part in parts)
    except ValueError:
        sys.exit(f"{env_name} must be comma-separated integers, got {raw!r}")


def _parse_allowed_telegram_ids() -> frozenset[int]:
    return _parse_telegram_ids("ALLOWED_TELEGRAM_IDS")


# Legacy bootstrap seed (ticket 07): seeds `active` access entries ONCE
# at first run; the database is the live gate afterwards. Closed by
# default — nobody gets in without an Access entry.
allowed_telegram_ids = _parse_allowed_telegram_ids()

# Admins manage access (approve/block) in ticket 05's Launcher commands.
# Empty is allowed but reported at startup — see storage._seed_access.
admin_telegram_ids = _parse_telegram_ids("ADMIN_TELEGRAM_IDS")

# Custom-text guardrails: a user-submitted exercise must fit this window.
CUSTOM_TEXT_MIN_WORDS = 10
CUSTOM_TEXT_MAX_WORDS = 50
