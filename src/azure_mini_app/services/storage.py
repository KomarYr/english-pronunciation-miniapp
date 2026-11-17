"""SQLite persistence: users, exercises, attempts, access, audit.

Speaks only in domain types (and small read-model dataclasses below).
No business logic here: quota policy and the low-score → mistake-bank
rule belong to app.py. Connection is opened per operation so worker
threads (asyncio.to_thread in the Telegram bot) never share one handle.

Schema is versioned via SQLite's `user_version` pragma. Opening a v1
database migrates it to v2 in place (access + audit tables) and seeds
access once from the env bootstrap; afterwards the database is the sole
source of truth and env changes do nothing.
"""

import hashlib
import json
import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from azure_mini_app import exercises as catalog
from azure_mini_app import settings
from azure_mini_app.domain import Attempt, ScoreReport, User, WordScore

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

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

-- Access control (schema v2): one row per Telegram ID. The database is
-- the source of truth; the env allowlist only seeds it once (see
-- _seed_access). Closed by default: nobody gets in without an entry.
CREATE TABLE IF NOT EXISTS access (
    telegram_id INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    status      TEXT NOT NULL CHECK (status IN ('pending', 'active', 'blocked')),
    role        TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Append-only trail of who changed access and when (actor/subject are
-- users.id; actor is NULL for system/bootstrap actions).
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id   INTEGER REFERENCES users(id),
    subject_id INTEGER REFERENCES users(id),
    event      TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_access_user ON access(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_subject ON audit_log(subject_id);
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


AccessStatus = Literal["pending", "active", "blocked"]
AccessRole = Literal["admin", "user"]

# Fixed audit vocabulary — not a free-form string (see _record_audit).
AuditEvent = Literal["access_seeded", "access_created", "status_changed", "role_changed"]


def _validate_status(status: AccessStatus) -> None:
    if status not in ("pending", "active", "blocked"):
        raise ValueError(f"status must be pending/active/blocked, got {status!r}")


def _validate_role(role: AccessRole) -> None:
    if role not in ("admin", "user"):
        raise ValueError(f"role must be admin/user, got {role!r}")


def _fetch_access_row(
    conn: sqlite3.Connection, telegram_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM access WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()


@dataclass(frozen=True, slots=True)
class AccessEntry:
    """One person's access state: the database's word on who may enter."""

    telegram_id: int
    user_id: int
    status: AccessStatus
    role: AccessRole
    created_at: datetime
    updated_at: datetime


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
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.executescript(_SCHEMA)
            self._seed_builtin_exercises(conn)
            if version < SCHEMA_VERSION:
                # First open of a v1 database (migration) or a brand-new
                # file: bootstrap access once. Already-migrated databases
                # skip this entirely — env changes then do nothing.
                self._seed_access(conn)
                # PRAGMA values can't be bound parameters; the value is
                # an internal constant, not user input.
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            else:
                # Diagnostic, not auto-repair: a v2 database whose access
                # table is empty while bootstrap IDs exist in .env means
                # the access data was probably wiped by hand.
                (access_count,) = conn.execute(
                    "SELECT COUNT(*) FROM access"
                ).fetchone()
                if not access_count and (
                    settings.allowed_telegram_ids or settings.admin_telegram_ids
                ):
                    logger.warning(
                        "access table is empty although bootstrap IDs are "
                        "set in .env — access data may have been wiped. "
                        "Nobody can use the Mini App until an entry exists."
                    )

    def _seed_builtin_exercises(self, conn: sqlite3.Connection) -> None:
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

    def _seed_access(self, conn: sqlite3.Connection) -> None:
        """One-time bootstrap: the env allowlist becomes the database.

        Every legacy ALLOWED_TELEGRAM_IDS entry becomes `active`; every
        ADMIN_TELEGRAM_IDS entry becomes `active` with role `admin` (an
        id in both gets the admin role). This is the only path by which
        env IDs enter the access table — after it, the database is the
        sole source of truth.
        """
        admins = settings.admin_telegram_ids
        legacy = settings.allowed_telegram_ids
        if not admins:
            logger.warning(
                "ADMIN_TELEGRAM_IDS is empty — nobody will be able to "
                "approve access requests. Add at least one admin ID to .env."
            )
        for telegram_id in sorted(legacy | admins):
            role: AccessRole = "admin" if telegram_id in admins else "user"
            # row-level idempotency: a partially-seeded file (interrupted
            # migration) re-opens without crashing
            already = conn.execute(
                "SELECT 1 FROM access WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()
            if already is not None:
                continue
            row = conn.execute(
                "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()
            if row is None:
                # person never opened the app: placeholder user row so
                # the access entry has something to point at
                cursor = conn.execute(
                    """
                    INSERT INTO users (display_name, telegram_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (f"tg-{telegram_id}", telegram_id, _utcnow_iso()),
                )
                user_id = int(cursor.lastrowid)
            else:
                user_id = int(row["id"])
            now = _utcnow_iso()
            conn.execute(
                """
                INSERT INTO access
                    (telegram_id, user_id, status, role, created_at, updated_at)
                VALUES (?, ?, 'active', ?, ?, ?)
                """,
                (telegram_id, user_id, role, now, now),
            )
            self._record_audit(
                conn,
                actor_id=None,
                subject_id=user_id,
                event="access_seeded",
                detail=f"role={role}",
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
        """Match on telegram_id only — display names never identify anyone.

        Two people with the same display name must never merge into one
        user row. Without a telegram id there is no identity to match
        on, so a fresh row is created.
        """
        if telegram_id is None:
            return self.create_user(display_name, None)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO users (display_name, telegram_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (display_name, telegram_id, datetime.now(UTC).isoformat()),
                )
                row = conn.execute(
                    "SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
            elif (
                row["display_name"] == f"tg-{telegram_id}" and display_name
            ):
                # seeded placeholder ("tg-42") gets the real name the
                # first time the actual person logs in
                conn.execute(
                    "UPDATE users SET display_name = ? WHERE id = ?",
                    (display_name, row["id"]),
                )
                row = conn.execute(
                    "SELECT * FROM users WHERE id = ?", (row["id"],)
                ).fetchone()
        return self._user_from_row(row)

    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> User:
        return User(
            display_name=row["display_name"],
            id=row["id"],
            telegram_id=row["telegram_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # --- access + audit -----------------------------------------------------

    def _access_from_row(self, row: sqlite3.Row) -> AccessEntry:
        return AccessEntry(
            telegram_id=int(row["telegram_id"]),
            user_id=int(row["user_id"]),
            status=row["status"],
            role=row["role"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _record_audit(
        self,
        conn: sqlite3.Connection,
        *,
        actor_id: int | None,
        subject_id: int | None,
        event: AuditEvent,
        detail: str | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO audit_log (actor_id, subject_id, event, detail, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (actor_id, subject_id, event, detail, _utcnow_iso()),
        )

    def ensure_access(
        self,
        telegram_id: int,
        user_id: int,
        *,
        status: AccessStatus = "pending",
        role: AccessRole = "user",
        actor_id: int | None = None,
    ) -> AccessEntry:
        """Create the access entry if absent; an existing row is untouched.

        The audit trail records *changes*, so a re-ensure of an existing
        entry writes nothing.
        """
        _validate_status(status)
        _validate_role(role)
        with self._connect() as conn:
            now = _utcnow_iso()
            cursor = conn.execute(
                """
                INSERT INTO access
                    (telegram_id, user_id, status, role, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO NOTHING
                """,
                (telegram_id, user_id, status, role, now, now),
            )
            row = _fetch_access_row(conn, telegram_id)
            if cursor.rowcount == 1:
                self._record_audit(
                    conn,
                    actor_id=actor_id,
                    subject_id=int(row["user_id"]),
                    event="access_created",
                )
        return self._access_from_row(row)

    def get_access(self, telegram_id: int) -> AccessEntry | None:
        with self._connect() as conn:
            row = _fetch_access_row(conn, telegram_id)
        return self._access_from_row(row) if row else None

    def list_access_entries(
        self, *, status: AccessStatus | None = None
    ) -> list[AccessEntry]:
        """Every access entry, or just one status's, by creation order."""
        query = "SELECT * FROM access"
        params: tuple = ()
        if status is not None:
            _validate_status(status)
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at, telegram_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._access_from_row(row) for row in rows]

    def count_active_admins(self) -> int:
        """The last-admin guard's input: how many admins could still act."""
        with self._connect() as conn:
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM access WHERE role = 'admin' AND status = 'active'"
            ).fetchone()
        return int(count)

    def set_access_status(
        self, telegram_id: int, status: AccessStatus, *, actor_id: int | None = None
    ) -> AccessEntry:
        """Move an entry to pending/active/blocked; audit records the move."""
        _validate_status(status)
        return self._change_access(
            telegram_id, "status", status, actor_id=actor_id, event="status_changed"
        )

    def set_access_role(
        self, telegram_id: int, role: AccessRole, *, actor_id: int | None = None
    ) -> AccessEntry:
        """Promote/demote between admin and user; audit records the change."""
        _validate_role(role)
        return self._change_access(
            telegram_id, "role", role, actor_id=actor_id, event="role_changed"
        )

    def _change_access(
        self,
        telegram_id: int,
        column: Literal["status", "role"],
        value: str,
        *,
        actor_id: int | None,
        event: AuditEvent,
    ) -> AccessEntry:
        """Update one access column; audit records the change.

        Setting a field to its current value is not a change: nothing is
        written and nothing is audited. `column` is a Literal, never
        caller input.
        """
        with self._connect() as conn:
            row = _fetch_access_row(conn, telegram_id)
            if row is None:
                raise KeyError(f"no access entry for telegram id {telegram_id}")
            if row[column] == value:
                return self._access_from_row(row)
            conn.execute(
                f"UPDATE access SET {column} = ?, updated_at = ? WHERE telegram_id = ?",
                (value, _utcnow_iso(), telegram_id),
            )
            self._record_audit(
                conn,
                actor_id=actor_id,
                subject_id=int(row["user_id"]),
                event=event,
                detail=f"{row[column]} → {value}",
            )
            row = _fetch_access_row(conn, telegram_id)
        return self._access_from_row(row)

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
