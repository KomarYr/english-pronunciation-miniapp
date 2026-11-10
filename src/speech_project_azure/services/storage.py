"""SQLite persistence: users, exercises, attempts.

Speaks only in domain types (and small read-model dataclasses below).
No business logic here: quota policy and the low-score → mistake-bank
rule belong to app.py. Connection is opened per operation so worker
threads (asyncio.to_thread in the Telegram bot) never share one handle.
"""

import hashlib
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from speech_project_azure import exercises as catalog
from speech_project_azure.domain import Attempt, ScoreReport, User, WordScore

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    telegram_id  INTEGER UNIQUE,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exercises (
    id             TEXT PRIMARY KEY,
    owner_user_id  INTEGER REFERENCES users(id),
    title          TEXT NOT NULL,
    reference_text TEXT NOT NULL,
    category       TEXT NOT NULL,
    level          TEXT,
    phoneme_focus  TEXT,
    source         TEXT NOT NULL CHECK (source IN ('builtin', 'custom')),
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL REFERENCES users(id),
    exercise_id         TEXT NOT NULL REFERENCES exercises(id),
    reference_text      TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    audio_seconds       REAL NOT NULL,
    recognized_text     TEXT NOT NULL,
    accuracy_score      REAL NOT NULL,
    fluency_score       REAL NOT NULL,
    completeness_score  REAL NOT NULL,
    prosody_score       REAL NOT NULL,
    pronunciation_score REAL NOT NULL,
    words_json          TEXT NOT NULL
);

-- Quota ledger: one row per Azure audio transaction, graded or not
-- (Azure bills silent NoMatch attempts too). The single source of
-- truth for monthly_audio_seconds.
CREATE TABLE IF NOT EXISTS audio_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    exercise_id TEXT REFERENCES exercises(id),
    reason      TEXT NOT NULL CHECK (reason IN ('graded', 'no_speech', 'canceled')),
    audio_seconds REAL NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_user ON attempts(user_id);
CREATE INDEX IF NOT EXISTS idx_attempts_exercise ON attempts(exercise_id);
CREATE INDEX IF NOT EXISTS idx_exercises_owner ON exercises(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_audio_usage_user ON audio_usage(user_id);
"""


@dataclass(frozen=True, slots=True)
class ExerciseStat:
    """Read model for the mistake-bank queue: one exercise plus its scores."""

    exercise: catalog.Exercise
    avg_pronunciation_score: float
    attempt_count: int
    last_attempted: datetime


@dataclass(frozen=True, slots=True)
class UserStats:
    """Read model for the /stats screen."""

    user_id: int
    total_attempts: int
    total_audio_seconds: float
    avg_pronunciation_score: float | None
    avg_accuracy_score: float | None


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _custom_exercise_id(reference_text: str) -> str:
    """Deterministic id → saving the same text twice dedupes automatically."""
    digest = hashlib.sha1(" ".join(reference_text.split()).encode()).hexdigest()
    return f"custom-{digest[:10]}"


def _exercise_row_to_exercise(row: sqlite3.Row) -> catalog.Exercise:
    focus = tuple(json.loads(row["phoneme_focus"])) if row["phoneme_focus"] else ()
    return catalog.Exercise(
        id=row["id"],
        title=row["title"],
        reference_text=row["reference_text"],
        category=row["category"],
        level=row["level"],
        phoneme_focus=frozenset(focus),
    )


def _report_to_row(report: ScoreReport) -> dict:
    return {
        "recognized_text": report.recognized_text,
        "accuracy_score": report.accuracy_score,
        "fluency_score": report.fluency_score,
        "completeness_score": report.completeness_score,
        "prosody_score": report.prosody_score,
        "pronunciation_score": report.pronunciation_score,
        "words_json": json.dumps(
            [
                {
                    "word": w.word,
                    "accuracy_score": w.accuracy_score,
                    "error_type": w.error_type,
                    "phonemes": [
                        {"phoneme": p, "accuracy_score": s} for p, s in w.phonemes
                    ],
                }
                for w in report.words
            ]
        ),
    }


def _words_from_json(raw: str) -> tuple[WordScore, ...]:
    return tuple(
        WordScore(
            word=item["word"],
            accuracy_score=item["accuracy_score"],
            error_type=item["error_type"],
            phonemes=tuple(
                (p["phoneme"], p["accuracy_score"])
                for p in item.get("phonemes", [])  # rows written before phonemes
            ),
        )
        for item in json.loads(raw)
    )


class Storage:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- schema + seeding -------------------------------------------------

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            for e in catalog.list_exercises():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO exercises
                        (id, owner_user_id, title, reference_text, category,
                         level, phoneme_focus, source, created_at)
                    VALUES (?, NULL, ?, ?, ?, ?, ?, 'builtin', ?)
                    """,
                    (
                        e.id,
                        e.title,
                        e.reference_text,
                        e.category,
                        e.level,
                        json.dumps(sorted(e.phoneme_focus))
                        if e.phoneme_focus
                        else None,
                        _utcnow_iso(),
                    ),
                )

    # --- users -------------------------------------------------------------

    def create_user(self, display_name: str, telegram_id: int | None = None) -> User:
        created_at = datetime.now(UTC)
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO users (display_name, telegram_id, created_at) VALUES (?, ?, ?)",
                (display_name, telegram_id, created_at.isoformat()),
            )
            return User(
                display_name=display_name,
                id=cursor.lastrowid,
                telegram_id=telegram_id,
                created_at=created_at,
            )

    def get_user(self, user_id: int) -> User | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._user_from_row(row) if row else None

    def get_user_by_telegram_id(self, telegram_id: int) -> User | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()
        return self._user_from_row(row) if row else None

    def get_or_create_user(
        self, display_name: str, telegram_id: int | None = None
    ) -> User:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_id = ? OR display_name = ?",
                (telegram_id, display_name),
            ).fetchone()
        return (
            self._user_from_row(row)
            if row
            else self.create_user(display_name, telegram_id)
        )

    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> User:
        return User(
            display_name=row["display_name"],
            id=row["id"],
            telegram_id=row["telegram_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # --- exercises -----------------------------------------------------------

    def add_custom_exercise(
        self, owner_user_id: int, reference_text: str, title: str | None = None
    ) -> catalog.Exercise:
        """Idempotent: the same normalized text always maps to the same id."""
        normalized = " ".join(reference_text.split())
        exercise_id = _custom_exercise_id(normalized)
        existing = self.get_exercise(exercise_id)
        if existing is not None:
            return existing
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO exercises
                    (id, owner_user_id, title, reference_text, category,
                     level, phoneme_focus, source, created_at)
                VALUES (?, ?, ?, ?, 'custom', NULL, NULL, 'custom', ?)
                """,
                (
                    exercise_id,
                    owner_user_id,
                    title or (normalized[:40] + ("…" if len(normalized) > 40 else "")),
                    normalized,
                    _utcnow_iso(),
                ),
            )
        exercise = self.get_exercise(exercise_id)
        assert exercise is not None, "exercise was just inserted"
        return exercise

    def get_exercise(self, exercise_id: str) -> catalog.Exercise | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM exercises WHERE id = ?", (exercise_id,)
            ).fetchone()
        return _exercise_row_to_exercise(row) if row else None

    def list_exercises_for_user(self, user_id: int) -> list[catalog.Exercise]:
        """Builtin exercises plus the user's own custom ones."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM exercises
                WHERE owner_user_id IS NULL OR owner_user_id = ?
                ORDER BY source, category, id
                """,
                (user_id,),
            ).fetchall()
        return [_exercise_row_to_exercise(row) for row in rows]

    def delete_custom_exercise(self, exercise_id: str, user_id: int) -> bool:
        """Only the owner can delete a custom exercise; builtins never match."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM exercises
                WHERE id = ? AND owner_user_id = ? AND source = 'custom'
                """,
                (exercise_id, user_id),
            )
        return cursor.rowcount > 0

    # --- attempts ------------------------------------------------------------

    def add_attempt(self, attempt: Attempt) -> Attempt:
        """Insert and return the attempt with its storage-assigned id.

        Also writes the graded audio usage to the quota ledger.
        """
        row = _report_to_row(attempt.report)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO attempts
                    (user_id, exercise_id, reference_text, created_at,
                     audio_seconds, recognized_text, accuracy_score,
                     fluency_score, completeness_score, prosody_score,
                     pronunciation_score, words_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.user_id,
                    attempt.exercise_id,
                    attempt.reference_text,
                    attempt.created_at.isoformat(),
                    attempt.audio_seconds,
                    row["recognized_text"],
                    row["accuracy_score"],
                    row["fluency_score"],
                    row["completeness_score"],
                    row["prosody_score"],
                    row["pronunciation_score"],
                    row["words_json"],
                ),
            )
            conn.execute(
                """
                INSERT INTO audio_usage
                    (user_id, exercise_id, reason, audio_seconds, created_at)
                VALUES (?, ?, 'graded', ?, ?)
                """,
                (
                    attempt.user_id,
                    attempt.exercise_id,
                    attempt.audio_seconds,
                    attempt.created_at.isoformat(),
                ),
            )
        return Attempt(
            user_id=attempt.user_id,
            exercise_id=attempt.exercise_id,
            reference_text=attempt.reference_text,
            created_at=attempt.created_at,
            audio_seconds=attempt.audio_seconds,
            report=attempt.report,
            id=cursor.lastrowid,
        )

    def add_audio_usage(
        self,
        user_id: int,
        audio_seconds: float,
        reason: str,
        created_at: datetime | None = None,
        exercise_id: str | None = None,
    ) -> None:
        """Record a non-graded Azure transaction (no_speech / canceled).

        Call this for every assessment exception that carries audio_seconds:
        Azure bills silent attempts exactly like graded ones.
        """
        if reason not in ("no_speech", "canceled"):
            raise ValueError(
                f"reason must be 'no_speech' or 'canceled', got {reason!r}"
            )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audio_usage
                    (user_id, exercise_id, reason, audio_seconds, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    exercise_id,
                    reason,
                    audio_seconds,
                    (created_at or datetime.now(UTC)).isoformat(),
                ),
            )

    def attempts_for_user(self, user_id: int, limit: int = 50) -> list[Attempt]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*, u.display_name
                FROM attempts a JOIN users u ON u.id = a.user_id
                WHERE a.user_id = ?
                ORDER BY a.created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [self._attempt_from_row(row) for row in rows]

    def monthly_audio_seconds(self, month_start: datetime) -> float:
        """Total audio billed since month_start — QuotaTracker's backend.

        Reads the audio_usage ledger: covers graded attempts AND silent
        no-speech/canceled transactions, because Azure bills them all.
        """
        with self._connect() as conn:
            (total,) = conn.execute(
                "SELECT COALESCE(SUM(audio_seconds), 0) FROM audio_usage WHERE created_at >= ?",
                (month_start.isoformat(),),
            ).fetchone()
        return float(total)

    def exercise_score_stats(self, user_id: int) -> list[ExerciseStat]:
        """Per-exercise average pronunciation score, weakest first.

        The mistake-bank queue: derived from attempts, so it self-heals —
        practice an exercise, score better, it sinks down the list.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.*,
                       AVG(a.pronunciation_score) AS avg_score,
                       COUNT(a.id)                AS attempt_count,
                       MAX(a.created_at)          AS last_attempted
                FROM attempts a
                JOIN exercises e ON e.id = a.exercise_id
                WHERE a.user_id = ?
                  AND (e.owner_user_id IS NULL OR e.owner_user_id = ?)
                GROUP BY e.id
                ORDER BY avg_score ASC, last_attempted ASC
                """,
                (user_id, user_id),
            ).fetchall()
        return [
            ExerciseStat(
                exercise=_exercise_row_to_exercise(row),
                avg_pronunciation_score=row["avg_score"],
                attempt_count=row["attempt_count"],
                last_attempted=datetime.fromisoformat(row["last_attempted"]),
            )
            for row in rows
        ]

    def user_stats(self, user_id: int) -> UserStats:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)                                          AS total_attempts,
                       COALESCE(SUM(audio_seconds), 0)                   AS total_audio_seconds,
                       AVG(pronunciation_score)                          AS avg_pron,
                       AVG(accuracy_score)                               AS avg_acc
                FROM attempts WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        return UserStats(
            user_id=user_id,
            total_attempts=row["total_attempts"],
            total_audio_seconds=row["total_audio_seconds"],
            avg_pronunciation_score=row["avg_pron"],
            avg_accuracy_score=row["avg_acc"],
        )

    def _attempt_from_row(self, row: sqlite3.Row) -> Attempt:
        return Attempt(
            id=row["id"],
            user_id=row["user_id"],
            exercise_id=row["exercise_id"],
            reference_text=row["reference_text"],
            created_at=datetime.fromisoformat(row["created_at"]),
            audio_seconds=row["audio_seconds"],
            report=ScoreReport(
                recognized_text=row["recognized_text"],
                accuracy_score=row["accuracy_score"],
                fluency_score=row["fluency_score"],
                completeness_score=row["completeness_score"],
                prosody_score=row["prosody_score"],
                pronunciation_score=row["pronunciation_score"],
                words=_words_from_json(row["words_json"]),
            ),
        )
