"""Mini App auth tests: initData HMAC validation, dev-mode guard.

initData is signed here exactly the way Telegram signs it (HMAC-SHA256
over the alphabetically-sorted fields, keyed by the bot token wrapped in
"WebAppData") — no real Telegram involved.
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import quote_plus

import pytest

from azure_mini_app import settings
from azure_mini_app.interactions.miniapp.auth import (
    MiniAppAuthError,
    MiniAppUser,
    NotAllowedError,
    is_allowed,
    resolve_user,
    validate_dev_mode,
    validate_init_data,
)
from azure_mini_app.services.storage import Storage

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


class TestAllowlist:
    def test_closed_by_default(self, monkeypatch):
        monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset())
        assert is_allowed(42) is False

    def test_member_passes(self, monkeypatch):
        monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset({42}))
        assert is_allowed(42) is True

    def test_resolve_user_creates_and_reuses(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset({42}))
        storage = Storage(tmp_path / "auth-test.db")
        identity = MiniAppUser(telegram_id=42, display_name="Ada Lovelace")
        first = resolve_user(storage, identity)
        second = resolve_user(storage, identity)
        assert first.id == second.id
        assert first.telegram_id == 42

    def test_resolve_user_rejects_stranger(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset({42}))
        storage = Storage(tmp_path / "auth-test.db")
        with pytest.raises(NotAllowedError, match="not on the allowlist"):
            resolve_user(storage, MiniAppUser(telegram_id=999, display_name="Eve"))


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
