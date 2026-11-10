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


def _require_azure_credentials() -> None:
    """Exit early with a clear message when Azure credentials are missing."""
    if not speech_key:
        sys.exit("Missing SUBSCRIPTION_KEY in .env")
    if not (speech_region or speech_endpoint):
        sys.exit("Set SERVICE_REGION or SERVICE_ENDPOINT in .env")
