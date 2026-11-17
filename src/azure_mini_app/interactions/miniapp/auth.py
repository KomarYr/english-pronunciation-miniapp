"""Mini App request authentication.

Two paths reach the API:

- **production** — Telegram signs `initData` (HMAC-SHA256 with the bot
  token). We verify the signature and reject stale data, producing a
  `MiniAppUser` identity; what that identity may do is decided by the
  access database (access.py), not here;
- **development** — `MINIAPP_DEV_MODE` lets a plain browser talk to the
  API as a fixed dev user (which must hold an active access entry like
  anyone else), so you can iterate on the UI without opening the app
  inside Telegram.
"""

import hashlib
import hmac
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl

from azure_mini_app import settings
from azure_mini_app.services.storage import Storage

# initData older than a day is not trusted: Telegram regenerates it every
# time the Mini App is opened, so a long-lived one only signals tampering.
MAX_AUTH_AGE_SECONDS = 86_400.0
# A real initData is a few hundred bytes; 4 KiB leaves room for long
# display names and signature fields while stopping payload stuffing.
MAX_INIT_DATA_BYTES = 4096

# Any '%' not followed by two hex digits is broken percent-encoding.
# Python's parse_qsl silently passes these through instead of failing.
_INVALID_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")


class MiniAppAuthError(Exception):
    """The request carries no valid Telegram identity."""


@dataclass(frozen=True, slots=True)
class MiniAppUser:
    """Who the API call claims to be, after signature verification."""

    telegram_id: int
    display_name: str


def display_name_for(
    first_name: str | None,
    last_name: str | None,
    username: str | None,
    telegram_id: int,
) -> str:
    """The person-facing name rule, shared by initData and the launcher:
    full name, else username, else a stable placeholder."""
    name_parts = [first_name, last_name]
    return (
        " ".join(part for part in name_parts if part) or username or f"tg-{telegram_id}"
    )


def _strict_pairs(init_data: str) -> dict[str, str]:
    """Parse initData strictly: no repair, no first-match-wins.

    Malformed percent-encoding, duplicate fields (a duplicated `hash`
    included) and oversized payloads are rejected here, before the
    signature is even computed — parser confusion must not reach the
    cryptographic check (OWASP: fail closed).
    """
    if len(init_data.encode("utf-8")) > MAX_INIT_DATA_BYTES:
        raise MiniAppAuthError("initData is too large")
    if _INVALID_PERCENT.search(init_data):
        raise MiniAppAuthError("initData has malformed percent-encoding")
    pairs = parse_qsl(init_data, keep_blank_values=True)
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise MiniAppAuthError("initData has duplicate fields")
    return dict(pairs)


def claimed_telegram_id(init_data: str) -> int | None:
    """Best-effort id from an *already-rejected* payload — audit hint only.

    Reuses the strict parser (no lenient re-implementation), so a
    malformed or duplicated payload simply yields None. Unverified by
    definition: the signature already failed, so this may name whoever
    the sender claimed to be — never a fact, never an access decision.
    """
    try:
        fields = _strict_pairs(init_data)
        user = json.loads(fields.get("user") or "null")
        return int(user["id"]) if user else None
    except MiniAppAuthError, KeyError, TypeError, ValueError:
        return None


def validate_init_data(init_data: str, *, now: datetime | None = None) -> MiniAppUser:
    """Verify Telegram-signed initData and extract the user identity.

    Follows the Telegram Mini Apps spec: the data-check string is every
    received field except `hash`, sorted alphabetically and joined with
    newlines; the HMAC key is `HMAC-SHA256(bot_token, "WebAppData")`.
    This includes the Bot API 8.0 `signature` field — proven empirically
    against a real client capture, pinned in tests/test_miniapp_auth.py.
    """
    if not settings.telegram_bot_token:
        raise MiniAppAuthError("TELEGRAM_BOT_TOKEN is not configured")
    fields = _strict_pairs(init_data)
    provided_hash = fields.get("hash")
    if not provided_hash:
        raise MiniAppAuthError("initData has no hash field")
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(fields.items()) if key != "hash"
    )
    secret_key = hmac.new(
        b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256
    ).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, provided_hash):
        raise MiniAppAuthError("initData signature mismatch")

    raw_auth_date = fields.get("auth_date")
    if raw_auth_date is None:
        raise MiniAppAuthError("initData has no auth_date field")
    try:
        age = (now or datetime.now(UTC)).timestamp() - float(raw_auth_date)
    except ValueError:
        raise MiniAppAuthError("initData auth_date is not a timestamp") from None
    if age > MAX_AUTH_AGE_SECONDS:
        raise MiniAppAuthError("initData is stale — reopen the Mini App")

    raw_user = fields.get("user")
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
    display_name = display_name_for(
        user.get("first_name"),
        user.get("last_name"),
        user.get("username"),
        telegram_id,
    )
    return MiniAppUser(telegram_id=telegram_id, display_name=display_name)


def validate_dev_mode(storage: Storage) -> None:
    """Dev mode must not weaken the closed gate: the fixed dev user needs
    an active access entry like anyone else (ticket 07).

    Arranged the same way as for a real person: seed it once via
    ALLOWED_TELEGRAM_IDS (the bootstrap), or /approve the dev id as an
    admin. A pending, blocked or missing entry refuses to start.
    """
    if not settings.miniapp_dev_mode:
        return
    entry = storage.get_access(settings.miniapp_dev_user_id)
    if entry is None or entry.status != "active":
        sys.exit(
            "MINIAPP_DEV_MODE is on, but the dev user (MINIAPP_DEV_USER_ID "
            f"{settings.miniapp_dev_user_id}) has no active access entry. "
            "On a fresh database, put the dev ID in ALLOWED_TELEGRAM_IDS "
            "before the first run (the bootstrap seed); on an existing "
            "database, start from a fresh DB file — dev data is disposable."
        )
