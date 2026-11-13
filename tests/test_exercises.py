import pytest

from azure_mini_app.exercises import (
    CATEGORIES,
    LEVELS,
    categories,
    get_exercise,
    list_exercises,
    next_exercise,
)


def test_all_exercises_have_unique_ids():
    ids = [e.id for e in list_exercises()]
    assert len(ids) == len(set(ids)), "exercise ids must be unique"


def test_all_exercises_have_valid_categories_and_levels():
    for e in list_exercises():
        assert e.category in CATEGORIES, f"{e.id}: bad category"
        assert e.level in LEVELS, f"{e.id}: bad level"
        assert e.title and e.reference_text, f"{e.id}: empty title/text"


def test_exercise_ids_are_stable_slugs():
    for e in list_exercises():
        assert " " not in e.id, f"{e.id}: ids must be slugs"


def test_categories_accessor_matches_constant():
    assert categories() == CATEGORIES


def test_list_filters_by_category_and_level():
    pairs = list_exercises(category="minimal_pairs")
    assert pairs and all(e.category == "minimal_pairs" for e in pairs)
    b2 = list_exercises(level="B2")
    assert b2 and all(e.level == "B2" for e in b2)


def test_list_raises_on_unknown_filters():
    with pytest.raises(ValueError):
        list_exercises(category="nope")
    with pytest.raises(ValueError):
        list_exercises(level="Z9")


def test_get_exercise_by_id_and_missing():
    some = list_exercises()[0]
    assert get_exercise(some.id) == some
    with pytest.raises(KeyError):
        get_exercise("does-not-exist")


def test_next_exercise_avoids_excluded():
    some = list_exercises()[0]
    others = [next_exercise(exclude_id=some.id) for _ in range(10)]
    assert some.id not in {e.id for e in others}
