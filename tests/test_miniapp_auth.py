"""Mini App auth tests: initData HMAC validation, dev-mode guard.

initData is signed here exactly the way Telegram signs it (HMAC-SHA256
over the alphabetically-sorted fields, keyed by the bot token wrapped in
"WebAppData") — no real Telegram involved. A real Bot API 8.0 capture
carrying the `signature` field is pinned below as a test vector: it
settles that `signature` belongs inside the data-check string.
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus

import pytest

from azure_mini_app import settings
from azure_mini_app.interactions.miniapp.auth import (
    MiniAppAuthError,
    MiniAppUser,
    validate_dev_mode,
    validate_init_data,
)

BOT_TOKEN = "123456:test-bot-token"
NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
AUTH_DATE = int(NOW.timestamp()) - 60  # signed a minute ago
USER = {"id": 42, "first_name": "Ada", "last_name": "Lovelace"}


def signed_init_data(
    user: dict = USER,
    auth_date: int = AUTH_DATE,
    bot_token: str = BOT_TOKEN,
    *,
    tamper: bool = False,
    drop: str | None = None,
) -> str:
    pairs = {
        "auth_date": str(auth_date),
        "query_id": "AAGlawnkAAAAAGGlawnkaaaa",
        "user": json.dumps(user),
    }
    if drop is not None:
        del pairs[drop]
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(pairs.items())
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if tamper:
        signature = ("0" if signature[0] != "0" else "1") + signature[1:]
    pairs["hash"] = signature
    return "&".join(f"{key}={quote_plus(value)}" for key, value in pairs.items())


@pytest.fixture()
def bot_token(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)


class TestValidateInitData:
    def test_valid_signature_resolves_identity(self, bot_token):
        identity = validate_init_data(signed_init_data(), now=NOW)
        assert identity == MiniAppUser(telegram_id=42, display_name="Ada Lovelace")

    def test_display_name_falls_back_to_username_then_id(self, bot_token):
        identity = validate_init_data(
            signed_init_data(user={"id": 7, "username": "ada"}), now=NOW
        )
        assert identity.display_name == "ada"
        identity = validate_init_data(signed_init_data(user={"id": 7}), now=NOW)
        assert identity.display_name == "tg-7"

    def test_tampered_signature_rejected(self, bot_token):
        with pytest.raises(MiniAppAuthError, match="mismatch"):
            validate_init_data(signed_init_data(tamper=True), now=NOW)

    def test_stale_init_data_rejected(self, bot_token):
        stale_auth_date = AUTH_DATE - 2 * 86_400
        with pytest.raises(MiniAppAuthError, match="stale"):
            validate_init_data(signed_init_data(auth_date=stale_auth_date), now=NOW)

    def test_missing_hash_rejected(self, bot_token):
        raw = signed_init_data()
        without_hash = "&".join(
            part for part in raw.split("&") if not part.startswith("hash=")
        )
        with pytest.raises(MiniAppAuthError, match="no hash"):
            validate_init_data(without_hash, now=NOW)

    def test_missing_user_rejected(self, bot_token):
        with pytest.raises(MiniAppAuthError, match="no user"):
            validate_init_data(signed_init_data(drop="user"), now=NOW)

    def test_missing_auth_date_rejected(self, bot_token):
        with pytest.raises(MiniAppAuthError, match="no auth_date"):
            validate_init_data(signed_init_data(drop="auth_date"), now=NOW)

    def test_unparsable_user_json_rejected(self, bot_token):
        raw = signed_init_data()
        broken = raw.replace(quote_plus(json.dumps(USER)), "%7Bbroken")
        with pytest.raises(MiniAppAuthError):
            validate_init_data(broken, now=NOW)

    def test_user_without_numeric_id_rejected(self, bot_token):
        with pytest.raises(MiniAppAuthError, match="numeric id"):
            validate_init_data(signed_init_data(user={"id": "nine"}), now=NOW)

    def test_missing_bot_token_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, "telegram_bot_token", None)
        with pytest.raises(MiniAppAuthError, match="TELEGRAM_BOT_TOKEN"):
            validate_init_data(signed_init_data(), now=NOW)


# --- pinned real-world test vector (ticket 01) ---------------------------------
#
# A real initData payload captured in Telegram Desktop on 2026-11-17
# 12:25:56 UTC (Bot API 8.0 client), carrying the modern `signature`
# field. Sources disagreed about where `signature` belongs: one said it
# stays inside the bot-token HMAC data-check string, one showed
# pre-8.0 validators rejecting such payloads. Settled empirically
# against this capture: the published `hash` matches ONLY when
# `signature` is included in the data-check string (i.e. every field
# except `hash`, exactly what the spec says). Excluding it produces a
# different HMAC (5ba252a1cb17...) — so the pre-8.0 construction is the
# correct one and no validator fix was needed. This vector pins that:
# if anyone "fixes" the validator to exclude `signature`, this fails.
#
# The raw capture lives in tests/fixtures/real_init_data.txt, which is
# gitignored — it is personal data, and a verbatim capture is the only
# thing that can prove the data-check construction (a synthetic payload
# would be signed by the test itself, proving nothing). To regenerate:
# open the Mini App in Telegram Desktop, right-click → Inspect →
# console → `window.Telegram.WebApp.initData`, paste the string into
# the fixture file. Verification also needs the real TELEGRAM_BOT_TOKEN
# from .env; the test skips when either is missing.

_REAL_VECTOR_PATH = Path(__file__).parent / "fixtures" / "real_init_data.txt"


def _real_init_data() -> str | None:
    """The captured payload, or None on machines without the local fixture."""
    try:
        return _REAL_VECTOR_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


@pytest.mark.skipif(
    _real_init_data() is None,
    reason="no tests/fixtures/real_init_data.txt — see the comment above",
)
class TestPinnedRealPayload:
    def test_real_client_payload_with_signature_field_verifies(self):
        raw = _real_init_data()
        assert raw is not None
        pairs = dict(parse_qsl(raw, keep_blank_values=True))
        # frozen shortly after the capture so the staleness check passes
        now = datetime.fromtimestamp(int(pairs["auth_date"]) + 60, tz=UTC)
        identity = validate_init_data(raw, now=now)
        # identity must come from the payload itself — never hardcoded
        # here, the fixture holds personal data that stays out of git
        user = json.loads(pairs["user"])
        name = " ".join(
            part for part in (user.get("first_name"), user.get("last_name")) if part
        )
        assert identity == MiniAppUser(telegram_id=int(user["id"]), display_name=name)

    # (The allowlist helper retired in ticket 04: both surfaces — Mini App
    # (ticket 03) and Launcher (ticket 04) — now gate on the access
    # database; see tests/test_miniapp_access.py and test_telegram.py.)


class TestDevModeGuard:
    def test_off_is_always_fine(self):
        # dev mode is not enabled in the test environment
        if not settings.miniapp_dev_mode:
            validate_dev_mode()

    def test_empty_allowlist_allows_dev_user(self, monkeypatch):
        monkeypatch.setattr(settings, "miniapp_dev_mode", True)
        monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset())
        validate_dev_mode()  # no exit

    def test_dev_user_must_be_allowlisted(self, monkeypatch):
        monkeypatch.setattr(settings, "miniapp_dev_mode", True)
        monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset({42}))
        monkeypatch.setattr(settings, "miniapp_dev_user_id", 900000001)
        with pytest.raises(SystemExit):
            validate_dev_mode()
