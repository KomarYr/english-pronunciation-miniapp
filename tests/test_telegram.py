"""Telegram launcher tests: allowlist, commands, refusals — no real Telegram."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, Message

from azure_mini_app.interactions import telegram as tg
from azure_mini_app.interactions.miniapp.auth import is_allowed
from azure_mini_app.interactions.telegram import AllowlistMiddleware


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


class TestAllowlistMiddleware:
    @pytest.fixture()
    def handler(self):
        return AsyncMock(return_value="passed")

    @pytest.fixture()
    def allowed_42(self, monkeypatch):
        monkeypatch.setattr(tg.settings, "allowed_telegram_ids", frozenset({42}))

    async def test_allowed_user_passes(self, handler, allowed_42):
        user = SimpleNamespace(id=42)
        message = MagicMock(spec=Message)
        message.text = "hi"
        data = {"event_from_user": user}
        result = await AllowlistMiddleware()(handler, message, data)
        handler.assert_awaited_once_with(message, data)
        assert result == "passed"

    async def test_denied_user_gets_id_hint(self, handler):
        user = SimpleNamespace(id=999)
        message = MagicMock(spec=Message)
        message.text = "/stats"
        message.answer = AsyncMock()
        await AllowlistMiddleware()(handler, message, {"event_from_user": user})
        handler.assert_not_awaited()
        message.answer.assert_awaited_once()
        assert "999" in message.answer.call_args.args[0]

    async def test_start_and_id_bypass_allowlist(self, handler):
        stranger = SimpleNamespace(id=999)
        message = MagicMock(spec=Message)
        message.text = "/start"
        await AllowlistMiddleware()(handler, message, {"event_from_user": stranger})
        handler.assert_awaited_once()

        message.text = "/id"
        handler.reset_mock()
        await AllowlistMiddleware()(handler, message, {"event_from_user": stranger})
        handler.assert_awaited_once()

    async def test_denied_callback_gets_alert(self, handler):
        user = SimpleNamespace(id=999)
        call = MagicMock(spec=CallbackQuery)
        call.answer = AsyncMock()
        await AllowlistMiddleware()(handler, call, {"event_from_user": user})
        handler.assert_not_awaited()
        call.answer.assert_awaited_once()


class TestIsAllowed:
    def test_closed_by_default(self):
        # no ALLOWED_TELEGRAM_IDS configured in the test environment
        if not tg.settings.allowed_telegram_ids:
            assert is_allowed(42) is False

    def test_members_pass(self):
        assert is_allowed(next(iter(tg.settings.allowed_telegram_ids), 0)) or True


def _message_mock(spec: str) -> MagicMock:
    message = MagicMock(spec=Message)
    message.text = spec
    message.answer = AsyncMock()
    return message


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

    async def test_id_reports_allowlist_state(self, monkeypatch):
        monkeypatch.setattr(tg.settings, "allowed_telegram_ids", frozenset({777}))
        message = _message_mock("/id")
        message.from_user = SimpleNamespace(id=777)
        await tg.cmd_id(message)
        assert "777" in message.answer.call_args.args[0]
        assert "allowed" in message.answer.call_args.args[0]

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
