import sys
from os import environ
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # src/ → project root
load_dotenv(PROJECT_ROOT / ".env")

speech_key: str | None = environ.get("SUBSCRIPTION_KEY")
speech_region: str | None = environ.get("SERVICE_REGION")
speech_endpoint: str | None = environ.get("SERVICE_ENDPOINT")

# Target language of the practice exercises.
language = "en-US"

# SQLite database location (gitignored).
db_path = PROJECT_ROOT / "data" / "app.db"

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
# user. Localhost convenience only — the dev id must still be allowlisted.
miniapp_dev_mode = environ.get("MINIAPP_DEV_MODE", "").lower() in ("1", "true", "yes")
miniapp_dev_user_id = _int_env("MINIAPP_DEV_USER_ID", 900_000_001)
miniapp_dev_user_name = environ.get("MINIAPP_DEV_USER_NAME", "Dev Browser User")


def _parse_allowed_telegram_ids() -> frozenset[int]:
    """Parse ALLOWED_TELEGRAM_IDS=123,456 from .env; empty → nobody allowed."""
    raw = environ.get("ALLOWED_TELEGRAM_IDS", "")
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    try:
        return frozenset(int(part) for part in parts)
    except ValueError:
        sys.exit(f"ALLOWED_TELEGRAM_IDS must be comma-separated integers, got {raw!r}")


# Closed by default: users must be added to .env to use the Telegram bot.
allowed_telegram_ids = _parse_allowed_telegram_ids()

# Custom-text guardrails: a user-submitted exercise must fit this window.
CUSTOM_TEXT_MIN_WORDS = 10
CUSTOM_TEXT_MAX_WORDS = 50
