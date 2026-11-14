"""Mini App web server tests: webhook registration and health endpoint.

The Bot is mocked so set_webhook never touches Telegram; the bug these
tests pin is registering a webhook whose updates our own secret check
then rejects.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from azure_mini_app import settings
from azure_mini_app.interactions.miniapp import web as web_module


@pytest.fixture()
def webhook_env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "db_path", tmp_path / "web-test.db")
    monkeypatch.setattr(settings, "miniapp_public_url", "https://fun.example")
    monkeypatch.setattr(settings, "telegram_bot_token", "123456:fake-token")


def patched_bot(monkeypatch) -> MagicMock:
    bot = MagicMock()
    bot.set_webhook = AsyncMock()
    monkeypatch.setattr(web_module, "Bot", lambda **kwargs: bot)
    return bot


class TestWebhookRegistration:
    def test_set_webhook_includes_secret_token(self, webhook_env, monkeypatch):
        monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret")
        bot = patched_bot(monkeypatch)

        from azure_mini_app.interactions.miniapp.web import create_app

        with TestClient(create_app()):
            pass

        bot.set_webhook.assert_awaited_once()
        url: str = bot.set_webhook.call_args.args[0]
        kwargs: dict[str, Any] = bot.set_webhook.call_args.kwargs
        assert url == "https://fun.example/telegram/webhook/s3cret"
        assert kwargs["secret_token"] == "s3cret"

    def test_set_webhook_without_secret_still_gets_url(self, webhook_env, monkeypatch):
        monkeypatch.setattr(settings, "telegram_webhook_secret", None)
        bot = patched_bot(monkeypatch)

        from azure_mini_app.interactions.miniapp.web import create_app

        with TestClient(create_app()):
            pass

        url: str = bot.set_webhook.call_args.args[0]
        assert url == "https://fun.example/telegram/webhook/telegram"
        assert bot.set_webhook.call_args.kwargs["secret_token"] is None

    def test_no_public_url_skips_registration(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "db_path", tmp_path / "web-test.db")
        monkeypatch.setattr(settings, "miniapp_public_url", None)
        monkeypatch.setattr(settings, "telegram_bot_token", "123456:fake-token")
        monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret")
        bot = patched_bot(monkeypatch)

        from azure_mini_app.interactions.miniapp.web import create_app

        with TestClient(create_app()):
            pass

        bot.set_webhook.assert_not_awaited()

    def test_no_bot_token_skips_registration(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "db_path", tmp_path / "web-test.db")
        monkeypatch.setattr(settings, "miniapp_public_url", "https://fun.example")
        monkeypatch.setattr(settings, "telegram_bot_token", None)
        bot = patched_bot(monkeypatch)

        from azure_mini_app.interactions.miniapp.web import create_app

        with TestClient(create_app()):
            pass

        bot.set_webhook.assert_not_awaited()


class TestHealth:
    def test_health_is_200(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "db_path", tmp_path / "web-test.db")
        from azure_mini_app.interactions.miniapp.web import create_app

        with TestClient(create_app()) as client:
            response = client.get("/health")
        assert response.status_code == 200
        assert response.text == "OK"
