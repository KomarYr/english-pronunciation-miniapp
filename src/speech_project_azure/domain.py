"""Core vocabulary of the app: plain data types shared by services,
use-cases and interactions. Pure stdlib — no SDK or storage imports here.

Storage mapping (rows <-> dataclasses) lives in services/storage.py;
SDK-result conversion lives in services/azure_assessment.py.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# Azure's word-level error classification.
WordErrorType = Literal["None", "Mispronunciation", "Omission", "Insertion"]


class TextValidationError(ValueError):
    """Raised when user-provided practice text fails validation."""


@dataclass(frozen=True, slots=True)
class WordScore:
    word: str
    accuracy_score: float  # 0..100
    error_type: WordErrorType
    # (phoneme, accuracy_score) pairs in spoken order; Azure's phoneme-level
    # detail. Empty when the SDK returns no phoneme data for the word.
    phonemes: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class ScoreReport:
    """What Azure's pronunciation assessment returned for one attempt."""

    recognized_text: str
    accuracy_score: float
    fluency_score: float
    completeness_score: float
    prosody_score: float
    pronunciation_score: float  # the overall number users care about
    words: tuple[WordScore, ...] = ()


@dataclass(frozen=True, slots=True)
class User:
    """A learner. CLI users have telegram_id=None; Telegram login matches on it."""

    display_name: str
    id: int | None = None  # assigned by storage on insert
    telegram_id: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Attempt:
    """One graded reading of one exercise by one user.

    exercise_id is None for free-text attempts; reference_text is always a
    snapshot of what was actually assessed, so history stays truthful even if
    the exercise catalog changes later. audio_seconds is a display stat — the
    quota source of truth is the audio_usage ledger in storage.
    """

    user_id: int
    exercise_id: str | None
    reference_text: str
    created_at: datetime
    audio_seconds: float
    report: ScoreReport
    id: int | None = None  # assigned by storage on insert
