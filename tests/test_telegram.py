"""Telegram launcher tests: the database access gate, commands, refusals.

Ticket 04: the launcher gate consults the access table (Seam 3 — real
storage, mocked aiogram objects); /id is a status checker.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, Message

from azure_mini_app import settings
from azure_mini_app.interactions import telegram as tg
from azure_mini_app.interactions.telegram import AccessMiddleware
from azure_mini_app.services.storage import Storage


class TestAllowlistParsing:
    def test_parse_valid_ids(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", " 123, 456 ,789")
        from azure_mini_app.settings import _parse_allowed_telegram_ids

        assert _parse_allowed_telegram_ids() == frozenset({123, 456, 789})

    def test_parse_empty_allows_nobody(self, monkeypatch):
        from azure_mini_app.settings import _parse_allowed_telegram_ids

        monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "")
        assert _parse_allowed_telegram_ids() == frozenset()

    def test_parse_garbage_exits(self, monkeypatch):
        from azure_mini_app.settings import _parse_allowed_telegram_ids

        monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "abc")
        with pytest.raises(SystemExit):
            _parse_allowed_telegram_ids()


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    """Fresh database, nothing seeded — like the test_storage fixture."""
    monkeypatch.setattr(settings, "db_path", tmp_path / "launcher-test.db")
    monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset())
    monkeypatch.setattr(settings, "admin_telegram_ids", frozenset())
    monkeypatch.setattr(settings, "telegram_bot_token", None)
    return Storage(settings.db_path)


def grant(
    storage: Storage,
    telegram_id: int = 42,
    *,
    status: str = "active",
    role: str = "user",
) -> None:
    """Arrange an access entry the way seeding or /approve would."""
    user = storage.get_or_create_user("Ada", telegram_id=telegram_id)
    assert user.id is not None
    storage.ensure_access(telegram_id, user.id, status=status, role=role)


def make_message(text: str, user_id: int | None = 42) -> MagicMock:
    message = MagicMock(spec=Message)
    message.text = text
    message.answer = AsyncMock()
    message.from_user = (
        SimpleNamespace(
            id=user_id, first_name="Ada", last_name="Lovelace", username=None
        )
        if user_id
        else None
    )
    return message


class TestAccessGate:
    """The middleware reads access state from the database."""

    async def test_active_user_passes(self, storage):
        grant(storage)
        handler = AsyncMock(return_value="passed")
        message = make_message("hello")
        data: dict = {}
        result = await AccessMiddleware(storage)(handler, message, data)
        assert result == "passed"
        handler.assert_awaited_once_with(message, data)

    async def test_admin_passes(self, storage):
        grant(storage, role="admin")
        handler = AsyncMock(return_value="passed")
        result = await AccessMiddleware(storage)(handler, make_message("hi"), {})
        assert result == "passed"

    async def test_pending_user_gets_awaiting_approval_message(self, storage):
        grant(storage, status="pending")
        handler = AsyncMock()
        message = make_message("/stats")
        await AccessMiddleware(storage)(handler, message, {})
        handler.assert_not_awaited()
        text = message.answer.call_args.args[0]
        assert "waiting for approval" in text

    async def test_blocked_user_is_refused(self, storage):
        grant(storage, status="blocked")
        handler = AsyncMock()
        message = make_message("/stats")
        await AccessMiddleware(storage)(handler, message, {})
        handler.assert_not_awaited()
        text = message.answer.call_args.args[0]
        assert "blocked" in text

    async def test_pending_callback_gets_alert(self, storage):
        grant(storage, status="pending")
        handler = AsyncMock()
        call = MagicMock(spec=CallbackQuery)
        call.answer = AsyncMock()
        data = {
            "event_from_user": SimpleNamespace(
                id=42, first_name="Ada", last_name="Lovelace", username=None
            )
        }
        await AccessMiddleware(storage)(handler, call, data)
        handler.assert_not_awaited()
        assert "waiting for approval" in call.answer.call_args.args[0]

    async def test_blocked_callback_gets_alert(self, storage):
        grant(storage, status="blocked")
        handler = AsyncMock()
        call = MagicMock(spec=CallbackQuery)
        call.answer = AsyncMock()
        data = {
            "event_from_user": SimpleNamespace(
                id=42, first_name="Ada", last_name="Lovelace", username=None
            )
        }
        await AccessMiddleware(storage)(handler, call, data)
        handler.assert_not_awaited()
        call.answer.assert_awaited_once()


class TestStrangerRegistration:
    """Closed by default: a stranger's message records their request."""

    async def test_stranger_is_registered_pending_and_told_so(self, storage):
        handler = AsyncMock()
        message = make_message("/stats", user_id=999)
        await AccessMiddleware(storage)(handler, message, {})
        handler.assert_not_awaited()
        text = message.answer.call_args.args[0]
        assert "waiting for approval" in text

        entry = storage.get_access(999)
        assert entry is not None
        assert entry.status == "pending"
        user = storage.get_user_by_telegram_id(999)
        assert user is not None
        assert user.display_name == "Ada Lovelace"

    async def test_stranger_display_name_falls_back_to_username(self, storage):
        handler = AsyncMock()
        message = make_message("hi", user_id=999)
        message.from_user = SimpleNamespace(
            id=999, first_name="", last_name=None, username="ada99"
        )
        await AccessMiddleware(storage)(handler, message, {})
        user = storage.get_user_by_telegram_id(999)
        assert user is not None
        assert user.display_name == "ada99"

    async def test_first_registration_notifies_admins_once(self, storage, monkeypatch):
        from aiogram import Bot

        monkeypatch.setattr(settings, "telegram_bot_token", "123:test")
        monkeypatch.setattr(settings, "admin_telegram_ids", frozenset({777}))
        sent: list[tuple[int, str]] = []

        async def fake_send(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))

        monkeypatch.setattr(Bot, "send_message", fake_send)

        first = make_message("hello", user_id=999)
        await AccessMiddleware(storage)(AsyncMock(), first, {})
        assert [chat_id for chat_id, _ in sent] == [777]
        assert "Ada Lovelace" in sent[0][1]

        sent.clear()
        second = make_message("hello again", user_id=999)
        await AccessMiddleware(storage)(AsyncMock(), second, {})
        assert sent == []  # one notification per person, not per message


class TestGateBypassAndLegacyEnv:
    async def test_start_and_id_bypass_the_gate(self, storage):
        for command in ("/start", "/id"):
            handler = AsyncMock()
            stranger = make_message(command, user_id=999)
            await AccessMiddleware(storage)(handler, stranger, {})
            handler.assert_awaited_once()

    async def test_env_allowlist_is_no_longer_consulted(self, storage, monkeypatch):
        # 42 is in the legacy env allowlist but has no access entry:
        # the database, not .env, decides.
        monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset({42}))
        handler = AsyncMock()
        message = make_message("/stats", user_id=42)
        await AccessMiddleware(storage)(handler, message, {})
        handler.assert_not_awaited()
        entry = storage.get_access(42)
        assert entry is not None
        assert entry.status == "pending"


def _message_mock(spec: str) -> MagicMock:
    message = MagicMock(spec=Message)
    message.text = spec
    message.answer = AsyncMock()
    return message


class TestIdStatusChecker:
    async def test_id_reports_pending(self, storage):
        grant(storage, status="pending")
        message = make_message("/id")
        await tg.cmd_id(message, storage)
        assert "pending" in message.answer.call_args.args[0]

    async def test_id_reports_active(self, storage):
        grant(storage)
        message = make_message("/id")
        await tg.cmd_id(message, storage)
        assert "active" in message.answer.call_args.args[0]

    async def test_id_reports_admin_role(self, storage):
        grant(storage, role="admin")
        message = make_message("/id")
        await tg.cmd_id(message, storage)
        assert "admin" in message.answer.call_args.args[0]

    async def test_id_reports_blocked(self, storage):
        grant(storage, status="blocked")
        message = make_message("/id")
        await tg.cmd_id(message, storage)
        assert "blocked" in message.answer.call_args.args[0]

    async def test_id_reports_no_request_yet(self, storage):
        message = make_message("/id", user_id=999)
        await tg.cmd_id(message, storage)
        text = message.answer.call_args.args[0]
        assert "no access request" in text

    async def test_id_does_not_register_the_caller(self, storage):
        message = make_message("/id", user_id=999)
        await tg.cmd_id(message, storage)
        assert storage.get_access(999) is None


class TestLauncher:
    async def test_start_sends_greeting(self):
        message = _message_mock("/start")
        await tg.cmd_start(message)
        text = message.answer.call_args.args[0]
        assert "Mini App" in text

    async def test_start_keyboard_without_public_url_is_none(self, monkeypatch):
        monkeypatch.setattr(tg.settings, "miniapp_public_url", None)
        message = _message_mock("/start")
        await tg.cmd_start(message)
        assert message.answer.call_args.kwargs["reply_markup"] is None

    async def test_start_keyboard_with_public_url_opens_web_app(self, monkeypatch):
        monkeypatch.setattr(tg.settings, "miniapp_public_url", "https://t.example/app")
        message = _message_mock("/start")
        await tg.cmd_start(message)
        keyboard = message.answer.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[0][0].web_app.url == "https://t.example/app"

    async def test_voice_note_is_refused_with_app_button(self):
        message = _message_mock("voice")
        await tg.voice_note_refused(message)
        text = message.answer.call_args.args[0]
        assert "Mini App" in text

    async def test_unknown_text_falls_back_to_launcher(self):
        message = _message_mock("anything")
        await tg.fallback_to_launcher(message)
        text = message.answer.call_args.args[0]
        assert "Mini App" in text
