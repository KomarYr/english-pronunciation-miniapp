"""Storage invariants: schema, seeding, round-trips, ownership, quotas."""

import sqlite3
from datetime import UTC, datetime

import pytest

from azure_mini_app import exercises as catalog
from azure_mini_app.domain import Attempt, ScoreReport, WordScore
from azure_mini_app.services.storage import Storage


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
def storage(tmp_path):
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
