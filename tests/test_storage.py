"""Storage invariants: schema, seeding, round-trips, ownership, quotas.

Ticket 02 adds the v2 sections: access table + audit table, the
user_version-driven v1→v2 migration with one-time env seeding, and the
telegram_id-only login match.
"""

import logging
import sqlite3
from datetime import UTC, datetime

import pytest

from azure_mini_app import exercises as catalog
from azure_mini_app import settings
from azure_mini_app.domain import Attempt, ScoreReport, WordScore
from azure_mini_app.services.storage import SCHEMA_VERSION, Storage


def make_report(overrides: dict | None = None) -> ScoreReport:
    values = {
        "recognized_text": "She sells six silver shells.",
        "accuracy_score": 92.0,
        "fluency_score": 88.0,
        "completeness_score": 100.0,
        "prosody_score": 75.0,
        "pronunciation_score": 89.0,
        "words": (
            WordScore("She", 95.0, "None"),
            WordScore("sells", 90.0, "None"),
            WordScore("shells", 60.0, "Mispronunciation"),
        ),
    }
    values.update(overrides or {})
    return ScoreReport(**values)


def make_attempt(
    *,
    user_id: int = 1,
    exercise_id: str | None = "s-vs-sh-sentence-1",
    reference_text: str = "She sells six silver shells by the sea shore.",
    created_at: datetime = datetime(2026, 11, 10, 12, 0, tzinfo=UTC),
    audio_seconds: float = 4.2,
    report: ScoreReport | None = None,
) -> Attempt:
    return Attempt(
        user_id=user_id,
        exercise_id=exercise_id,
        reference_text=reference_text,
        created_at=created_at,
        audio_seconds=audio_seconds,
        report=report if report is not None else make_report(),
    )


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    # isolate from the real .env: fresh databases in tests seed nothing
    monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset())
    monkeypatch.setattr(settings, "admin_telegram_ids", frozenset())
    return Storage(tmp_path / "test.db")


class TestSeeding:
    def test_builtin_exercises_seeded(self, storage):
        names = {e.id for e in storage.list_exercises_for_user(user_id=1)}
        assert "s-vs-sh-sentence-1" in names

    def test_seeding_is_idempotent(self, tmp_path):
        db = tmp_path / "test.db"
        count = len(Storage(db).list_exercises_for_user(user_id=1))
        Storage(db).init_schema()
        again = len(Storage(db).list_exercises_for_user(user_id=1))
        assert count == again


class TestUsers:
    def test_create_and_get(self, storage):
        user = storage.create_user("yurii")
        assert user.id is not None
        assert storage.get_user(user.id) == user

    def test_telegram_lookup(self, storage):
        created = storage.create_user("tg-user", telegram_id=123456)
        assert storage.get_user_by_telegram_id(123456) == created
        assert storage.get_user_by_telegram_id(999) is None

    def test_get_or_create(self, storage):
        first = storage.get_or_create_user("yurii", telegram_id=42)
        second = storage.get_or_create_user("yurii", telegram_id=42)
        assert first == second


class TestLoginMatchesTelegramIdOnly:
    def test_same_named_users_stay_distinct(self, storage):
        ada_one = storage.get_or_create_user("Ada", telegram_id=1)
        ada_two = storage.get_or_create_user("Ada", telegram_id=2)
        assert ada_one.id != ada_two.id
        assert storage.get_or_create_user("Ada", telegram_id=1) == ada_one

    def test_without_telegram_id_there_is_no_identity_to_match(self, storage):
        # a display name is not an identity: without a telegram id the
        # call can only create
        first = storage.get_or_create_user("Solo")
        second = storage.get_or_create_user("Solo")
        assert first.id != second.id

    def test_seeded_placeholder_gets_real_name_on_first_login(self, storage):
        placeholder = storage.create_user("tg-42", telegram_id=42)
        named = storage.get_or_create_user("Ada Komar", telegram_id=42)
        assert named.id == placeholder.id  # same person, not a new row
        assert named.display_name == "Ada Komar"
        assert storage.get_or_create_user("Ada Komar", telegram_id=42) == named

    def test_real_names_are_never_overwritten(self, storage):
        storage.create_user("Real Name", telegram_id=42)
        same = storage.get_or_create_user("Different Name", telegram_id=42)
        assert same.display_name == "Real Name"


# --- ticket 02: schema v2 — access, audit, migration --------------------------


def _strip_to_v1(db_path):
    """Revert a v2 database file to the shipped v1 state.

    v1 had no access/audit tables and never set user_version (so it
    reads 0). v2 does not alter any v1 table, so dropping the new tables
    and resetting the pragma reconstructs a faithful v1 file.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE access")
        conn.execute("DROP TABLE audit_log")
        conn.execute("PRAGMA user_version = 0")


def _audit_events(db_path) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT event, detail FROM audit_log ORDER BY id").fetchall()


def _access_ids(db_path) -> list[int]:
    with sqlite3.connect(db_path) as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT telegram_id FROM access ORDER BY telegram_id"
            )
        ]


@pytest.fixture()
def seeded_env(monkeypatch):
    monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset({42}))
    monkeypatch.setattr(settings, "admin_telegram_ids", frozenset({7}))


class TestSchemaV2Migration:
    def test_fresh_database_is_version_2(self, storage, tmp_path):
        with sqlite3.connect(tmp_path / "test.db") as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    def test_fresh_database_seeds_allowlist_and_admins(self, seeded_env, tmp_path):
        storage = Storage(tmp_path / "test.db")
        entry_user = storage.get_access(42)
        entry_admin = storage.get_access(7)
        assert (entry_user.status, entry_user.role) == ("active", "user")
        assert (entry_admin.status, entry_admin.role) == ("active", "admin")

    def test_seed_creates_missing_user_rows(self, seeded_env, tmp_path):
        # an allowlisted person who never opened the app still gets a
        # user row, so the access entry's foreign key has a target
        storage = Storage(tmp_path / "test.db")
        assert storage.get_user_by_telegram_id(42).telegram_id == 42
        assert storage.get_user_by_telegram_id(7).telegram_id == 7

    def test_migration_preserves_data_and_reuses_user_rows(self, monkeypatch, tmp_path):
        db = tmp_path / "test.db"
        # phase 1: a pre-v2 world — no env seeds, a real user with data
        monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset())
        monkeypatch.setattr(settings, "admin_telegram_ids", frozenset())
        storage = Storage(db)
        ada = storage.create_user("Ada", telegram_id=42)
        attempt = storage.add_attempt(make_attempt(user_id=ada.id))
        _strip_to_v1(db)
        # phase 2: upgrade with the legacy allowlist pointing at Ada
        monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset({42}))
        monkeypatch.setattr(settings, "admin_telegram_ids", frozenset({7}))
        migrated = Storage(db)
        assert migrated.get_user(ada.id) == ada
        assert migrated.attempts_for_user(ada.id) == [attempt]
        entry = migrated.get_access(42)
        assert (entry.status, entry.role) == ("active", "user")
        assert entry.user_id == ada.id  # Ada's row is reused, not duplicated

    def test_reopening_migrated_database_changes_nothing(self, seeded_env, monkeypatch, tmp_path):
        db = tmp_path / "test.db"
        Storage(db)
        # the database is now the source of truth: env changes must not
        # re-seed, duplicate or delete anything
        monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset({42, 99}))
        monkeypatch.setattr(settings, "admin_telegram_ids", frozenset())
        before = _audit_events(db)
        Storage(db)
        assert _access_ids(db) == [7, 42]
        assert _audit_events(db) == before

    def test_empty_admin_seed_is_reported(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setattr(settings, "allowed_telegram_ids", frozenset({42}))
        monkeypatch.setattr(settings, "admin_telegram_ids", frozenset())
        with caplog.at_level(logging.WARNING, logger="azure_mini_app.services.storage"):
            Storage(tmp_path / "test.db")
        assert "ADMIN_TELEGRAM_IDS" in caplog.text

    def test_wiped_access_table_on_migrated_database_warns(
        self, seeded_env, tmp_path, caplog
    ):
        db = tmp_path / "test.db"
        Storage(db)
        with sqlite3.connect(db) as conn:
            conn.execute("DROP TABLE access")
        # reopening recreates the table (empty) but must not silently
        # pretend everything is fine while bootstrap IDs exist in .env
        with caplog.at_level(logging.WARNING, logger="azure_mini_app.services.storage"):
            Storage(db)
        assert "may have been wiped" in caplog.text


class TestAccessEntries:
    def test_ensure_creates_pending_entry(self, storage):
        user = storage.create_user("ada", telegram_id=42)
        entry = storage.ensure_access(42, user.id)
        assert entry.telegram_id == 42
        assert entry.user_id == user.id
        assert (entry.status, entry.role) == ("pending", "user")
        assert entry.created_at == entry.updated_at

    def test_ensure_is_idempotent(self, storage, tmp_path):
        user = storage.create_user("ada", telegram_id=42)
        first = storage.ensure_access(42, user.id)
        second = storage.ensure_access(42, user.id, status="active")
        assert first == second  # existing row is never overwritten
        # and the audit trail only saw one creation, not two
        assert _audit_events(tmp_path / "test.db") == [("access_created", None)]

    def test_get_access_unknown_is_none(self, storage):
        assert storage.get_access(123) is None

    def test_status_change_updates_entry_and_audit(self, storage, tmp_path):
        user = storage.create_user("ada", telegram_id=42)
        storage.ensure_access(42, user.id, status="active")
        blocked = storage.set_access_status(42, "blocked", actor_id=user.id)
        assert blocked.status == "blocked"
        assert blocked.created_at == storage.get_access(42).created_at
        assert blocked.updated_at == storage.get_access(42).updated_at
        assert _audit_events(tmp_path / "test.db") == [
            ("access_created", None),
            ("status_changed", "active → blocked"),
        ]

    def test_role_change_updates_entry_and_audit(self, storage, tmp_path):
        user = storage.create_user("ada", telegram_id=42)
        storage.ensure_access(42, user.id, status="active")
        promoted = storage.set_access_role(42, "admin", actor_id=user.id)
        assert promoted.role == "admin"
        assert _audit_events(tmp_path / "test.db") == [
            ("access_created", None),
            ("role_changed", "user → admin"),
        ]

    def test_change_on_missing_entry_raises(self, storage):
        with pytest.raises(KeyError):
            storage.set_access_status(42, "blocked")
        with pytest.raises(KeyError):
            storage.set_access_role(42, "admin")

    def test_rejects_unknown_status_and_role(self, storage):
        user = storage.create_user("ada", telegram_id=42)
        with pytest.raises(ValueError):
            storage.ensure_access(42, user.id, status="banned")
        storage.ensure_access(42, user.id)
        with pytest.raises(ValueError):
            storage.set_access_role(42, "superuser")

    def test_noop_change_writes_nothing(self, storage, tmp_path):
        # setting a field to its current value is not a change
        user = storage.create_user("ada", telegram_id=42)
        entry = storage.ensure_access(42, user.id, status="active")
        before = _audit_events(tmp_path / "test.db")
        again = storage.set_access_status(42, "active", actor_id=user.id)
        assert again == entry
        assert again.updated_at == entry.updated_at
        assert _audit_events(tmp_path / "test.db") == before
        storage.set_access_role(42, "user", actor_id=user.id)
        assert _audit_events(tmp_path / "test.db") == before


class TestCustomExercises:
    def test_add_and_get(self, storage):
        user = storage.create_user("u")
        e = storage.add_custom_exercise(user.id, "My own strange sentence.")
        assert (
            e.source if hasattr(e, "source") else True
        )  # Exercise has no source field
        fetched = storage.get_exercise(e.id)
        assert fetched is not None
        assert fetched.reference_text == "My own strange sentence."

    def test_same_text_dedupes_to_one_row(self, storage):
        user = storage.create_user("u")
        a = storage.add_custom_exercise(user.id, "Same sentence.")
        b = storage.add_custom_exercise(user.id, "  Same   sentence. ")
        assert a.id == b.id
        assert (
            len(storage.list_exercises_for_user(user.id))
            == len(catalog.list_exercises()) + 1
        )

    def test_visibility_is_builtin_plus_own(self, storage):
        alice = storage.create_user("alice")
        bob = storage.create_user("bob")
        storage.add_custom_exercise(alice.id, "Alice private sentence.")
        alice_view = {e.id for e in storage.list_exercises_for_user(alice.id)}
        bob_view = {e.id for e in storage.list_exercises_for_user(bob.id)}
        assert "Alice private sentence." in {
            e.reference_text for e in storage.list_exercises_for_user(alice.id)
        }
        custom_ids = alice_view - {e.id for e in catalog.list_exercises()}
        assert not custom_ids & bob_view

    def test_owner_only_delete(self, storage):
        alice = storage.create_user("alice")
        bob = storage.create_user("bob")
        e = storage.add_custom_exercise(alice.id, "Delete me please.")
        assert storage.delete_custom_exercise(e.id, bob.id) is False  # not owner
        assert (
            storage.delete_custom_exercise("s-vs-sh-sentence-1", alice.id) is False
        )  # builtin
        assert storage.delete_custom_exercise(e.id, alice.id) is True

    def test_delete_blocked_when_attempts_exist(self, storage):
        user = storage.create_user("u")
        e = storage.add_custom_exercise(user.id, "Historic sentence.")
        storage.add_attempt(
            make_attempt(
                user_id=user.id, exercise_id=e.id, reference_text=e.reference_text
            )
        )
        with pytest.raises(sqlite3.IntegrityError):
            storage.delete_custom_exercise(e.id, user.id)


class TestAttempts:
    def test_roundtrip_preserves_everything(self, storage):
        user = storage.create_user("u")
        stored = storage.add_attempt(make_attempt(user_id=user.id))
        assert stored.id is not None
        loaded = storage.attempts_for_user(user.id)[0]
        assert loaded == stored
        assert loaded.report.words == make_report().words  # words round-trip exactly

    def test_phonemes_roundtrip(self, storage):
        user = storage.create_user("u")
        report = make_report(
            {
                "words": (
                    WordScore(
                        "jumbles",
                        73.0,
                        "Mispronunciation",
                        phonemes=(("ʤ", 84.0), ("ʌ", 61.0)),
                    ),
                    WordScore("six", 0.0, "Omission"),
                )
            }
        )
        storage.add_attempt(make_attempt(user_id=user.id, report=report))
        loaded = storage.attempts_for_user(user.id)[0]
        assert loaded.report.words[0].phonemes == (("ʤ", 84.0), ("ʌ", 61.0))
        assert loaded.report.words[1].phonemes == ()

    def test_legacy_rows_without_phonemes_load(self, storage, tmp_path):
        # simulate rows written before phonemes existed in words_json
        user = storage.create_user("u")
        storage.add_attempt(make_attempt(user_id=user.id))
        legacy_json = '[{"word": "She", "accuracy_score": 95.0, "error_type": "None"}]'
        import sqlite3

        with sqlite3.connect(tmp_path / "test.db") as conn:
            conn.execute("UPDATE attempts SET words_json = ?", (legacy_json,))
        loaded = storage.attempts_for_user(user.id)[0]
        assert loaded.report.words[0].word == "She"
        assert loaded.report.words[0].phonemes == ()

    def test_monthly_audio_seconds_filters_by_month(self, storage):
        user = storage.create_user("u")
        storage.add_attempt(make_attempt(user_id=user.id))  # graded usage, Nov 10
        storage.add_audio_usage(
            user.id,
            10.0,
            reason="no_speech",
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        )  # September — outside November's bucket
        month_start = datetime(2026, 11, 1, tzinfo=UTC)
        assert storage.monthly_audio_seconds(month_start) == pytest.approx(4.2)
        assert storage.monthly_audio_seconds(
            datetime(2026, 1, 1, tzinfo=UTC)
        ) == pytest.approx(14.2)

    def test_graded_attempt_writes_ledger_row(self, storage):
        user = storage.create_user("u")
        storage.add_attempt(make_attempt(user_id=user.id))
        # ledger, not attempts, is the quota source of truth
        assert storage.monthly_audio_seconds(
            datetime(2026, 1, 1, tzinfo=UTC)
        ) == pytest.approx(4.2)

    def test_no_speech_usage_is_counted(self, storage):
        # Azure bills silent attempts exactly like graded ones
        user = storage.create_user("u")
        storage.add_audio_usage(user.id, 2.5, reason="no_speech")
        assert storage.monthly_audio_seconds(
            datetime(2026, 1, 1, tzinfo=UTC)
        ) == pytest.approx(2.5)

    def test_add_audio_usage_rejects_graded_reason(self, storage):
        user = storage.create_user("u")
        with pytest.raises(ValueError):
            storage.add_audio_usage(
                user.id, 1.0, reason="graded"
            )  # only via add_attempt

    def test_score_stats_weakest_first(self, storage):
        user = storage.create_user("u")
        storage.add_attempt(
            make_attempt(
                user_id=user.id, report=make_report({"pronunciation_score": 40.0})
            )
        )
        storage.add_attempt(
            make_attempt(
                user_id=user.id,
                exercise_id="para-beach-1",
                reference_text=catalog.get_exercise("para-beach-1").reference_text,
                report=make_report({"pronunciation_score": 90.0}),
            )
        )
        stats = storage.exercise_score_stats(user.id)
        assert stats[0].exercise.id == "s-vs-sh-sentence-1"  # 40 < 90
        assert stats[0].avg_pronunciation_score == pytest.approx(40.0)
        assert stats[0].attempt_count == 1

    def test_user_stats_totals(self, storage):
        user = storage.create_user("u")
        storage.add_attempt(make_attempt(user_id=user.id))
        storage.add_attempt(make_attempt(user_id=user.id))
        stats = storage.user_stats(user.id)
        assert stats.total_attempts == 2
        assert stats.total_audio_seconds == pytest.approx(8.4)
        assert stats.avg_pronunciation_score == pytest.approx(89.0)

    def test_attempt_requires_known_exercise(self, storage):
        user = storage.create_user("u")
        with pytest.raises(sqlite3.IntegrityError):
            storage.add_attempt(
                make_attempt(user_id=user.id, exercise_id="no-such-exercise")
            )
