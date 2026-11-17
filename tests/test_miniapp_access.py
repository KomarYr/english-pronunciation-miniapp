"""Mini App access lifecycle tests (Seam 1 — HTTP API).

Ticket 03: the database is the gate. A stranger with valid initData is
auto-registered as pending (403 `pending`) and admins get notified once;
a blocked user gets a distinct 403 `blocked`; an active user gets the
full app exactly as before — checked on every request, so a block takes
effect mid-session. Storage side-effects are asserted through Seam 2
(Storage public methods).
"""

from datetime import UTC, datetime

import pytest
from aiogram import Bot
from fastapi.testclient import TestClient
from test_miniapp_auth import BOT_TOKEN, signed_init_data

from azure_mini_app import settings
from azure_mini_app.domain import ScoreReport
from azure_mini_app.services import azure_assessment
from azure_mini_app.services.storage import Storage

STRANGER = {"id": 42, "first_name": "Ada", "last_name": "Lovelace"}
ADMIN_ID = 777
ENGLISH_TEXT = "She sells six silver shells by the shiny seashore every sunny morning."


def fake_report() -> ScoreReport:
    return ScoreReport(
        recognized_text="She sells six silver shells.",
        accuracy_score=92.0,
        fluency_score=88.0,
        completeness_score=100.0,
        prosody_score=75.0,
        pronunciation_score=40.0,  # weak → lands in the mistake bank
        words=(),
    )


def stranger_headers(user: dict = STRANGER) -> dict[str, str]:
    """A fresh, validly signed initData — the way a real client would send it."""
    init_data = signed_init_data(
        user=user,
        auth_date=int(datetime.now(UTC).timestamp()) - 60,
        bot_token=BOT_TOKEN,
    )
    return {"Authorization": f"tma {init_data}"}


def make_app(
    monkeypatch,
    tmp_path,
    *,
    admin_ids: frozenset[int] = frozenset(),
    seed_ids: frozenset[int] = frozenset(),
) -> None:
    """initData-driven app: dev mode off, bot token set, nothing seeded.

    `seed_ids` are seeded active once at storage init (the ticket-02
    bootstrap), which is how an "already approved" user is arranged.
    """
    monkeypatch.setattr(settings, "db_path", tmp_path / "access-test.db")
    monkeypatch.setattr(settings, "miniapp_dev_mode", False)
    monkeypatch.setattr(settings, "allowed_telegram_ids", seed_ids)
    monkeypatch.setattr(settings, "admin_telegram_ids", admin_ids)
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)
    monkeypatch.setattr(settings, "miniapp_public_url", None)


@pytest.fixture()
def api_client_factory(monkeypatch, tmp_path):
    def factory(
        *,
        admin_ids: frozenset[int] = frozenset(),
        seed_ids: frozenset[int] = frozenset(),
    ) -> TestClient:
        make_app(monkeypatch, tmp_path, admin_ids=admin_ids, seed_ids=seed_ids)
        from azure_mini_app.interactions.miniapp.web import create_app

        return TestClient(create_app())

    return factory


@pytest.fixture()
def sent_messages(monkeypatch) -> list[tuple[int, str]]:
    """Mock the Bot API send boundary: record every sendMessage call."""
    calls: list[tuple[int, str]] = []

    async def fake_send(self, chat_id, text, **kwargs):
        calls.append((chat_id, text))

    monkeypatch.setattr(Bot, "send_message", fake_send)
    return calls


class TestPendingRegistration:
    def test_stranger_is_auto_registered_pending(self, api_client_factory):
        client = api_client_factory()
        response = client.get("/api/me", headers=stranger_headers())
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "pending"

        # the request itself recorded the stranger's identity
        storage = Storage(settings.db_path)
        user = storage.get_user_by_telegram_id(42)
        assert user is not None
        assert user.display_name == "Ada Lovelace"
        entry = storage.get_access(42)
        assert entry is not None
        assert entry.status == "pending"
        assert entry.role == "user"

    def test_reopening_does_not_duplicate(self, api_client_factory):
        client = api_client_factory()
        first = client.get("/api/me", headers=stranger_headers())
        second = client.get(
            "/api/me", headers=stranger_headers(user={"id": 42, "username": "ada"})
        )
        assert first.status_code == second.status_code == 403
        assert second.json()["detail"]["code"] == "pending"

        storage = Storage(settings.db_path)
        # same person, one user row and one access entry; the first
        # display name sticks (real names are never overwritten)
        reopened = storage.get_user_by_telegram_id(42)
        assert reopened is not None
        assert reopened.display_name == "Ada Lovelace"
        reopened_entry = storage.get_access(42)
        assert reopened_entry is not None
        assert reopened_entry.status == "pending"


class TestAdminNotification:
    def test_first_pending_registration_notifies_each_admin(
        self, api_client_factory, sent_messages
    ):
        client = api_client_factory(admin_ids=frozenset({ADMIN_ID, 778}))
        response = client.get("/api/me", headers=stranger_headers())
        assert response.status_code == 403
        # exactly one message per admin, to the right chats
        assert sorted(chat_id for chat_id, _ in sent_messages) == [ADMIN_ID, 778]
        assert len(sent_messages) == 2
        # the message tells the admin who is asking, by name and ID
        text = sent_messages[0][1]
        assert "Ada Lovelace" in text
        assert "42" in text

    def test_reopen_does_not_notify_again(self, api_client_factory, sent_messages):
        client = api_client_factory(admin_ids=frozenset({ADMIN_ID}))
        client.get("/api/me", headers=stranger_headers())
        assert len(sent_messages) == 1
        client.get("/api/me", headers=stranger_headers())
        assert len(sent_messages) == 1

    def test_notification_is_best_effort(self, monkeypatch, api_client_factory):
        async def broken_send(self, chat_id, text, **kwargs):
            raise RuntimeError("telegram is down")

        monkeypatch.setattr(Bot, "send_message", broken_send)
        client = api_client_factory(admin_ids=frozenset({ADMIN_ID}))
        response = client.get("/api/me", headers=stranger_headers())
        # the send failed but the stranger still got their typed answer
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "pending"

    def test_no_admins_no_send_attempt(self, api_client_factory, sent_messages):
        client = api_client_factory(admin_ids=frozenset())
        client.get("/api/me", headers=stranger_headers())
        assert sent_messages == []


class TestBlockedAndActive:
    def test_active_user_gets_the_full_app(self, api_client_factory):
        client = api_client_factory(seed_ids=frozenset({42}))
        response = client.get("/api/me", headers=stranger_headers())
        assert response.status_code == 200
        assert response.json()["user"]["telegram_id"] == 42
        assert response.json()["user"]["display_name"] == "Ada Lovelace"

    def test_pending_becomes_active_after_approval(self, api_client_factory):
        client = api_client_factory()
        assert client.get("/api/me", headers=stranger_headers()).status_code == 403
        storage = Storage(settings.db_path)
        storage.set_access_status(42, "active")
        response = client.get("/api/me", headers=stranger_headers())
        assert response.status_code == 200

    def test_block_takes_effect_mid_session(self, api_client_factory):
        client = api_client_factory(seed_ids=frozenset({42}))
        assert client.get("/api/me", headers=stranger_headers()).status_code == 200
        storage = Storage(settings.db_path)
        storage.set_access_status(42, "blocked")
        response = client.get("/api/me", headers=stranger_headers())
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "blocked"


class TestBlockedDataSurvives:
    """Blocking revokes access, never data: attempts and exercises stay."""

    @pytest.fixture()
    def practicing_user(self, api_client_factory, monkeypatch):
        """An active user with one graded attempt and one custom text."""
        client = api_client_factory(seed_ids=frozenset({42}))
        monkeypatch.setattr(
            azure_assessment,
            "assess",
            lambda audio, text, language="en-US": (fake_report(), 5.0),
        )
        monkeypatch.setattr(
            "azure_mini_app.interactions.miniapp.api.to_wav",
            lambda source, target_dir: source,
        )
        custom = client.post(
            "/api/texts", json={"text": ENGLISH_TEXT}, headers=stranger_headers()
        )
        assert custom.status_code == 200
        exercise_id = custom.json()["exercise"]["id"]
        graded = client.post(
            f"/api/assess?exercise_id={exercise_id}",
            content=b"fake-wav-bytes",
            headers=stranger_headers(),
        )
        assert graded.status_code == 200
        return client, exercise_id

    def test_blocked_user_gets_403_and_keeps_data(
        self, practicing_user, api_client_factory
    ):
        client, exercise_id = practicing_user
        storage = Storage(settings.db_path)
        storage.set_access_status(42, "blocked")
        response = client.get("/api/me", headers=stranger_headers())
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "blocked"

        user = storage.get_user_by_telegram_id(42)
        assert user is not None
        assert user.id is not None
        assert len(storage.attempts_for_user(user.id)) == 1
        owned = {e.id for e in storage.list_exercises_for_user(user.id)}
        assert exercise_id in owned

    def test_reapproval_restores_access_and_data(
        self, practicing_user, api_client_factory
    ):
        client, exercise_id = practicing_user
        storage = Storage(settings.db_path)
        storage.set_access_status(42, "blocked")
        assert client.get("/api/me", headers=stranger_headers()).status_code == 403
        storage.set_access_status(42, "active")

        response = client.get("/api/mistakes", headers=stranger_headers())
        assert response.status_code == 200
        mistakes = response.json()["mistakes"]
        assert [m["exercise"]["id"] for m in mistakes] == [exercise_id]
