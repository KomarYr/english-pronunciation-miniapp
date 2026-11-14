"""Mini App request authentication.

Two paths reach the API:

- **production** — Telegram signs `initData` (HMAC-SHA256 with the bot
  token). We verify the signature, reject stale data, resolve the
  Telegram user, and enforce the closed allowlist;
- **development** — `MINIAPP_DEV_MODE` lets a plain browser talk to the
  API as a fixed dev user (which must still be allowlisted), so you can
  iterate on the UI without opening the app inside Telegram.
"""

import hashlib
import hmac
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl

from azure_mini_app import settings
from azure_mini_app.domain import User
from azure_mini_app.services.storage import Storage

# initData older than a day is not trusted: Telegram regenerates it every
# time the Mini App is opened, so a long-lived one only signals tampering.
MAX_AUTH_AGE_SECONDS = 86_400.0


class MiniAppAuthError(Exception):
    """The request carries no valid Telegram identity."""


class NotAllowedError(Exception):
    """Valid identity, but the user is not on the closed allowlist."""


@dataclass(frozen=True, slots=True)
class MiniAppUser:
    """Who the API call claims to be, after signature verification."""

    telegram_id: int
    display_name: str


def validate_init_data(init_data: str, *, now: datetime | None = None) -> MiniAppUser:
    """Verify Telegram-signed initData and extract the user identity.

    Follows the Telegram Mini Apps spec: the data-check string is every
    received field except `hash`, sorted alphabetically and joined with
    newlines; the HMAC key is `HMAC-SHA256(bot_token, "WebAppData")`.
    """
    if not settings.telegram_bot_token:
        raise MiniAppAuthError("TELEGRAM_BOT_TOKEN is not configured")
    pairs = parse_qsl(init_data, keep_blank_values=True)
    provided_hash = next((value for key, value in pairs if key == "hash"), None)
    if not provided_hash:
        raise MiniAppAuthError("initData has no hash field")
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(pairs) if key != "hash"
    )
    secret_key = hmac.new(
        b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256
    ).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, provided_hash):
        raise MiniAppAuthError("initData signature mismatch")

    raw_auth_date = next((value for key, value in pairs if key == "auth_date"), None)
    if raw_auth_date is None:
        raise MiniAppAuthError("initData has no auth_date field")
    try:
        age = (now or datetime.now(UTC)).timestamp() - float(raw_auth_date)
    except ValueError:
        raise MiniAppAuthError("initData auth_date is not a timestamp") from None
    if age > MAX_AUTH_AGE_SECONDS:
        raise MiniAppAuthError("initData is stale — reopen the Mini App")

    raw_user = next((value for key, value in pairs if key == "user"), None)
    if raw_user is None:
        raise MiniAppAuthError("initData has no user field")
    try:
        user = json.loads(raw_user)
    except ValueError:
        raise MiniAppAuthError("initData user field is not JSON") from None
    try:
        telegram_id = int(user["id"])
    except KeyError, TypeError, ValueError:
        raise MiniAppAuthError("initData user has no numeric id") from None
    name_parts = [user.get("first_name"), user.get("last_name")]
    display_name = (
        " ".join(part for part in name_parts if part)
        or user.get("username")
        or f"tg-{telegram_id}"
    )
    return MiniAppUser(telegram_id=telegram_id, display_name=display_name)


def is_allowed(telegram_id: int) -> bool:
    """Closed by default: users must be listed in ALLOWED_TELEGRAM_IDS."""
    return telegram_id in settings.allowed_telegram_ids


def validate_dev_mode() -> None:
    """Dev mode must not weaken the closed allowlist."""
    if not settings.miniapp_dev_mode:
        return
    if not settings.allowed_telegram_ids:
        return
    if settings.miniapp_dev_user_id not in settings.allowed_telegram_ids:
        sys.exit(
            "MINIAPP_DEV_MODE is on, but MINIAPP_DEV_USER_ID "
            f"({settings.miniapp_dev_user_id}) is not in ALLOWED_TELEGRAM_IDS."
        )


def resolve_user(storage: Storage, identity: MiniAppUser) -> User:
    """Login matches on telegram_id; the allowlist is enforced here."""
    if not is_allowed(identity.telegram_id):
        raise NotAllowedError(
            f"Telegram ID {identity.telegram_id} is not on the allowlist."
        )
    return storage.get_or_create_user(
        identity.display_name, telegram_id=identity.telegram_id
    )
