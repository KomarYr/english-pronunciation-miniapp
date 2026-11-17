"""Developer DB CLI tests (ticket 08): seed, reset, list.

The CLI's own seam: argv in, stdout and exit code out, state asserted
through the Storage public API — no sqlite hand-rolls, no internal
call-graph checks. The env is isolated per test like every other suite.
"""

import builtins

import pytest

from azure_mini_app import settings
from azure_mini_app.devtools import main
from azure_mini_app.services.storage import Storage

DEV_ID = 900_000_001


@pytest.fixture()
def cli_env(monkeypatch, tmp_path):
    """Isolated settings for the CLI: fresh database, nothing seeded."""
    monkeypatch.setattr(settings, "db_path", tmp_path / "dev.db")
    monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset())
    monkeypatch.setattr(settings, "admin_telegram_ids", frozenset())
    monkeypatch.setattr(settings, "miniapp_dev_mode", False)
    monkeypatch.setattr(settings, "miniapp_dev_user_id", DEV_ID)
    monkeypatch.setattr(settings, "miniapp_dev_user_name", "Dev Browser User")


def grant(
    storage: Storage, telegram_id: int, *, status="active", role="user", name="u"
):
    user = storage.get_or_create_user(name, telegram_id=telegram_id)
    assert user.id is not None
    storage.ensure_access(telegram_id, user.id, status=status, role=role)


class TestSeed:
    def test_seed_creates_the_dev_users_active_entry(self, cli_env, capsys):
        assert main(["seed"]) == 0
        assert "Created" in capsys.readouterr().out
        entry = Storage(settings.db_path).get_access(DEV_ID)
        assert entry is not None
        assert entry.status == "active"

    def test_seed_activates_a_pending_dev_user(self, cli_env, capsys):
        storage = Storage(settings.db_path)
        grant(storage, DEV_ID, status="pending", name="Dev Browser User")
        assert main(["seed"]) == 0
        assert "pending → now active" in capsys.readouterr().out
        assert Storage(settings.db_path).get_access(DEV_ID).status == "active"

    def test_seed_reactivates_a_blocked_dev_user(self, cli_env):
        storage = Storage(settings.db_path)
        grant(storage, DEV_ID, status="blocked", name="Dev Browser User")
        assert main(["seed"]) == 0
        assert Storage(settings.db_path).get_access(DEV_ID).status == "active"

    def test_seed_on_an_already_active_entry_writes_nothing(self, cli_env, capsys):
        storage = Storage(settings.db_path)
        grant(storage, DEV_ID, status="active", name="Dev Browser User")
        before = storage.get_access(DEV_ID)
        assert main(["seed"]) == 0
        assert "already active" in capsys.readouterr().out
        after = Storage(settings.db_path).get_access(DEV_ID)
        assert after.updated_at == before.updated_at  # nothing changed


class TestList:
    def test_list_prints_every_entry_with_name_status_role(self, cli_env, capsys):
        storage = Storage(settings.db_path)
        grant(storage, 101, name="Ada")
        grant(storage, 102, status="pending", role="admin", name="Boss")
        assert main(["list"]) == 0
        out = capsys.readouterr().out
        assert "101" in out and "Ada" in out
        assert "102" in out and "Boss" in out
        assert "pending" in out and "admin" in out

    def test_list_on_an_empty_table_says_so(self, cli_env, capsys):
        assert main(["list"]) == 0
        assert "No access entries" in capsys.readouterr().out


class TestReset:
    def test_reset_yes_recreates_and_seeds_from_env(self, cli_env, monkeypatch, capsys):
        monkeypatch.setattr(settings, "admin_telegram_ids", frozenset({777}))
        monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset({42}))
        storage = Storage(settings.db_path)
        grant(storage, 999, name="Old")

        assert main(["reset", "--yes"]) == 0
        out = capsys.readouterr().out
        assert "Recreated" in out

        fresh = Storage(settings.db_path)
        assert fresh.get_access(999) is None  # old data gone
        admin = fresh.get_access(777)
        assert admin is not None and admin.role == "admin" and admin.status == "active"
        assert fresh.get_access(42).status == "active"

    def test_reset_seeds_the_dev_user_when_dev_mode_is_on(self, cli_env, monkeypatch):
        monkeypatch.setattr(settings, "miniapp_dev_mode", True)
        assert main(["reset", "--yes"]) == 0
        entry = Storage(settings.db_path).get_access(DEV_ID)
        assert entry is not None and entry.status == "active"

    def test_reset_without_dev_mode_seeds_no_dev_entry(self, cli_env, monkeypatch):
        # dev mode is off in the fixture — reset must not create the entry
        monkeypatch.setattr(settings, "admin_telegram_ids", frozenset({777}))
        assert main(["reset", "--yes"]) == 0
        assert Storage(settings.db_path).get_access(DEV_ID) is None

    def test_reset_without_legacy_ids_seeds_no_legacy_rows(self, cli_env, monkeypatch):
        # ALLOWED_TELEGRAM_IDS is empty in the fixture — only the admin
        monkeypatch.setattr(settings, "admin_telegram_ids", frozenset({777}))
        assert main(["reset", "--yes"]) == 0
        fresh = Storage(settings.db_path)
        assert fresh.get_access(42) is None
        assert [e.telegram_id for e in fresh.list_access_entries()] == [777]

    def test_reset_asks_for_confirmation_and_aborts_on_no(
        self, cli_env, monkeypatch, capsys
    ):
        storage = Storage(settings.db_path)
        grant(storage, 999, name="Kept")

        answers = iter(["no"])
        monkeypatch.setattr(builtins, "input", lambda prompt: next(answers))
        assert main(["reset"]) == 1
        assert "Aborted" in capsys.readouterr().out
        assert Storage(settings.db_path).get_access(999) is not None

    def test_reset_confirmation_yes_proceeds(self, cli_env, monkeypatch, capsys):
        storage = Storage(settings.db_path)
        grant(storage, 999, name="Gone")

        monkeypatch.setattr(builtins, "input", lambda prompt: "yes")
        assert main(["reset"]) == 0
        assert "Recreated" in capsys.readouterr().out
        assert Storage(settings.db_path).get_access(999) is None

    def test_reset_confirmation_prompt_names_the_database(self, cli_env, monkeypatch):
        prompts = []

        def fake_input(prompt: str) -> str:
            prompts.append(prompt)
            return "no"

        monkeypatch.setattr(builtins, "input", fake_input)
        main(["reset"])
        assert prompts and str(settings.db_path) in prompts[0]
