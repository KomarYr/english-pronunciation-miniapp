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

# Telegram bot (M5). Leave TELEGRAM_BOT_TOKEN empty to run CLI-only.
telegram_bot_token: str | None = environ.get("TELEGRAM_BOT_TOKEN")


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


def _require_azure_credentials() -> None:
    """Exit early with a clear message when Azure credentials are missing."""
    if not speech_key:
        sys.exit("Missing SUBSCRIPTION_KEY in .env")
    if not (speech_region or speech_endpoint):
        sys.exit("Set SERVICE_REGION or SERVICE_ENDPOINT in .env")
