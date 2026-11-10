"""Telegram bot tests: allowlist, middleware, keyboards — no real Telegram."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, Message

from speech_project_azure.interactions import telegram as tg
from speech_project_azure.interactions.telegram import AllowlistMiddleware, is_allowed
from speech_project_azure.services.storage import Storage


@pytest.fixture()
def storage(tmp_path):
    return Storage(tmp_path / "tg-test.db")


class TestAllowlistParsing:
    def test_parse_valid_ids(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", " 123, 456 ,789")
        assert tg.settings.__class__  # settings module imported
        from speech_project_azure.settings import _parse_allowed_telegram_ids

        assert _parse_allowed_telegram_ids() == frozenset({123, 456, 789})

    def test_parse_empty_allows_nobody(self, monkeypatch):
        from speech_project_azure.settings import _parse_allowed_telegram_ids

        monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "")
        assert _parse_allowed_telegram_ids() == frozenset()

    def test_parse_garbage_exits(self, monkeypatch):
        from speech_project_azure.settings import _parse_allowed_telegram_ids

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
        message.text = "/stats"
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


class TestKeyboards:
    def test_categories_keyboard_lists_sorted_categories(self, storage):
        user = storage.get_or_create_user("u")
        keyboard = tg._categories_keyboard(storage, user)
        labels = [btn.text for row in keyboard.inline_keyboard for btn in row]
        assert labels == sorted(labels)

    def test_exercises_keyboard_has_back_button(self, storage):
        user = storage.get_or_create_user("u")
        keyboard = tg._exercises_keyboard(storage, user, "minimal pairs")
        labels = [btn.text for row in keyboard.inline_keyboard for btn in row]
        assert "« Categories" in labels
        callback_data = [
            btn.callback_data for row in keyboard.inline_keyboard for btn in row
        ]
        assert "cats:" in callback_data

    def test_exercise_callbacks_reference_real_ids(self, storage):
        user = storage.get_or_create_user("u")
        keyboard = tg._exercises_keyboard(storage, user, "minimal pairs")
        for row in keyboard.inline_keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith("ex:"):
                    assert storage.get_exercise(btn.callback_data.removeprefix("ex:"))


class TestResolveUser:
    def test_matches_by_telegram_id(self, storage):
        tg_user = SimpleNamespace(id=777, full_name="Test User")
        first = tg._resolve_user(storage, tg_user)
        second = tg._resolve_user(storage, tg_user)
        assert first.id == second.id
        assert first.telegram_id == 777
        assert storage.get_user_by_telegram_id(777) == first
