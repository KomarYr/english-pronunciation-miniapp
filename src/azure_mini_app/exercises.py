"""Built-in exercise catalog: the texts learners read aloud.

Pure data + accessors. No I/O, no Azure, no storage — M3 callers
(interactions, app use-cases) import only the functions below.
Exercise ids are stable slugs: they are stored in SQLite attempt rows,
so they must never be renamed or reused for different texts.
"""

import random
from dataclasses import dataclass

CATEGORIES = (
    "minimal_pairs",
    "sentences",
    "tongue_twisters",
    "paragraphs",
)

LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")


@dataclass(frozen=True, slots=True)
class Exercise:
    id: str
    title: str
    reference_text: str  # what the learner reads aloud; sent to Azure as-is
    category: str
    level: str | None  # CEFR for catalog exercises; None for custom ones
    # IPA phonemes this exercise trains; metadata for menus and stats only.
    phoneme_focus: frozenset[str] = frozenset()


_EXERCISES: tuple[Exercise, ...] = (
    # --- minimal pairs ---------------------------------------------------
    Exercise(
        id="th-vs-s-1",
        title="think / sink",
        reference_text="I think this sink is too big for the kitchen.",
        category="minimal_pairs",
        level="A2",
        phoneme_focus=frozenset({"θ", "s"}),
    ),
    Exercise(
        id="ship-vs-sheep-1",
        title="ship / sheep",
        reference_text="The sheep is sleeping on the ship.",
        category="minimal_pairs",
        level="A2",
        phoneme_focus=frozenset({"ɪ", "iː"}),
    ),
    Exercise(
        id="v-vs-w-1",
        title="v / w",
        reference_text="We visited the wine valley in the winter.",
        category="minimal_pairs",
        level="A2",
        phoneme_focus=frozenset({"v", "w"}),
    ),
    # --- sentences -------------------------------------------------------
    Exercise(
        id="th-sentence-1",
        title="The thirsty traveller",
        reference_text=(
            "Something thick, something thin, and thirty-three thoughts in between."
        ),
        category="sentences",
        level="B1",
        phoneme_focus=frozenset({"θ", "ð"}),
    ),
    Exercise(
        id="r-vs-l-sentence-1",
        title="Red lorry",
        reference_text="The red lorry really liked the long, lonely road.",
        category="sentences",
        level="A2",
        phoneme_focus=frozenset({"r", "l"}),
    ),
    Exercise(
        id="s-vs-sh-sentence-1",
        title="Silver shells",
        reference_text="She sells six silver shells by the sea shore.",
        category="sentences",
        level="A2",
        phoneme_focus=frozenset({"s", "ʃ"}),
    ),
    # --- tongue twisters -------------------------------------------------
    Exercise(
        id="twister-peter-1",
        title="Peter Piper",
        reference_text="Peter Piper picked a peck of pickled peppers.",
        category="tongue_twisters",
        level="B1",
        phoneme_focus=frozenset({"p"}),
    ),
    Exercise(
        id="twister-woodchuck-1",
        title="Woodchuck",
        reference_text=(
            "How much wood would a woodchuck chuck if a woodchuck could chuck wood?"
        ),
        category="tongue_twisters",
        level="B1",
        phoneme_focus=frozenset({"w", "ʊ"}),
    ),
    # --- paragraphs ------------------------------------------------------
    Exercise(
        id="para-beach-1",
        title="A day at the beach",
        reference_text=(
            "The sea breeze drifted over the warm sand. "
            "Children searched for shells while the sun "
            "slowly slipped toward the horizon."
        ),
        category="paragraphs",
        level="B1",
        phoneme_focus=frozenset({"s", "z", "b"}),
    ),
    Exercise(
        id="para-theatre-1",
        title="An autumn evening",
        reference_text=(
            "Three thoughtful brothers thought the theatre was thrilling. "
            "Together they watched the weather change "
            "throughout the autumn evening."
        ),
        category="paragraphs",
        level="B2",
        phoneme_focus=frozenset({"θ", "ð"}),
    ),
)


def categories() -> tuple[str, ...]:
    return CATEGORIES


def list_exercises(
    category: str | None = None, level: str | None = None
) -> list[Exercise]:
    """Exercises filtered by category and/or CEFR level, in catalog order."""
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"Unknown category: {category!r}")
    if level is not None and level not in LEVELS:
        raise ValueError(f"Unknown level: {level!r}")
    return [
        e
        for e in _EXERCISES
        if (category is None or e.category == category)
        and (level is None or e.level == level)
    ]


def get_exercise(exercise_id: str) -> Exercise:
    for e in _EXERCISES:
        if e.id == exercise_id:
            return e
    raise KeyError(f"No exercise with id {exercise_id!r}")


def next_exercise(
    category: str | None = None, exclude_id: str | None = None
) -> Exercise:
    """Random exercise, optionally from one category, avoiding the last one."""
    pool = [e for e in list_exercises(category) if e.id != exclude_id]
    if not pool:
        raise ValueError(f"No exercise available for category={category!r}")
    return random.choice(pool)
