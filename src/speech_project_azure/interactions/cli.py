"""Interactive CLI: menu → microphone → per-word pronunciation report.

Thin adapter over app.py: reads input, calls use-cases, renders results
(via the shared rendering module). All business rules (quota, guardrails,
mistake bank) live in app.py.
"""

from speech_project_azure import app, settings
from speech_project_azure.domain import TextValidationError, User
from speech_project_azure.interactions.rendering import (
    format_mistake_bank,
    format_report,
    format_stats,
)
from speech_project_azure.services.azure_assessment import (
    AssessmentCanceled,
    AssessmentError,
    NoSpeechDetected,
)
from speech_project_azure.services.quota import QuotaExceededError
from speech_project_azure.services.storage import Storage

DB_PATH = settings.db_path

MENU = """
What would you like to do?
  1) Practice an exercise
  2) Practice my own text
  3) Mistake bank
  4) Stats
  5) Quit
"""


def _prompt_exercise(storage: Storage, user: User):
    """Category → exercise pick; returns None when the user backs out."""
    categories = sorted({e.category for e in app.list_exercises(storage, user)})
    print()
    for i, category in enumerate(categories, start=1):
        print(f"  {i}) {category}")
    choice = input("Category (number, empty to go back): ").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(categories):
        return None
    category = categories[int(choice) - 1]

    exercises = [e for e in app.list_exercises(storage, user) if e.category == category]
    print()
    for i, exercise in enumerate(exercises, start=1):
        print(f"  {i}) {exercise.title}")
    choice = input("Exercise (number, empty to go back): ").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(exercises):
        return None
    return exercises[int(choice) - 1]


def _countdown() -> None:
    for n in (3, 2, 1):
        print(f"Recording in {n}…")
    print("Listening — read the text aloud, then pause.")


def _practice_flow(
    storage: Storage, user: User, reference_text: str, attempt_fn
) -> None:
    """Common mic flow: countdown → attempt_fn() → render, or explain the error."""
    print(f"\nReference text:\n  {reference_text}")
    _countdown()
    try:
        attempt = attempt_fn()
    except NoSpeechDetected:
        print("\nI didn't hear anything — try again, a bit louder.")
    except AssessmentCanceled as error:
        print(f"\nAssessment was canceled by the service: {error}")
        if "invalid" in error.error_details.lower():
            raise SystemExit(1) from None
    except QuotaExceededError as status_error:
        print("\nMonthly audio budget is exhausted — practice resets on the 1st.")
        print(f"  ({status_error})")
    else:
        print(format_report(attempt))


def _practice_menu(storage: Storage, user: User) -> None:
    exercise = _prompt_exercise(storage, user)
    if exercise is None:
        return
    _practice_flow(
        storage,
        user,
        exercise.reference_text,
        lambda: app.record_attempt(storage, user, exercise),
    )


def _custom_text_flow(storage: Storage, user: User) -> None:
    print("\nPaste or type the English text you want to practice")
    print(
        f"({settings.CUSTOM_TEXT_MIN_WORDS}–{settings.CUSTOM_TEXT_MAX_WORDS} words), then press Enter:"
    )
    text = input("> ").strip()
    try:
        app.validate_custom_text(text)
    except TextValidationError as error:
        print(f"\n{error}")
        return
    words = len(" ".join(text.split()).split(" "))
    if input(f"Practice this {words}-word text? [Y/n] ").strip().lower() in ("n", "no"):
        return
    normalized = " ".join(text.split())
    _practice_flow(
        storage,
        user,
        normalized,
        lambda: app.assess_custom_text(storage, user, normalized),
    )


def _mistake_bank_flow(storage: Storage, user: User) -> None:
    print()
    print(format_mistake_bank(app.mistake_bank(storage, user)))


def _stats_flow(storage: Storage, user: User) -> None:
    user_stats, quota_status = app.app_stats(storage, user)
    print(format_stats(user_stats, quota_status))


def main() -> None:
    storage = Storage(DB_PATH)
    print("English pronunciation trainer (Azure Speech)")
    try:
        name = input("Your name: ").strip() or "anonymous"
    except EOFError, KeyboardInterrupt:
        print("\nBye!")
        return
    user: User = storage.get_or_create_user(name)

    actions = {
        "1": lambda: _practice_menu(storage, user),
        "2": lambda: _custom_text_flow(storage, user),
        "3": lambda: _mistake_bank_flow(storage, user),
        "4": lambda: _stats_flow(storage, user),
    }

    while True:
        print(MENU)
        try:
            choice = input("> ").strip()
        except EOFError, KeyboardInterrupt:
            print("\nBye!")
            return
        if choice == "5":
            print("Bye!")
            return
        action = actions.get(choice)
        if action is None:
            print("Pick 1–5.")
            continue
        try:
            action()
        except AssessmentError as error:
            print(f"Assessment problem: {error}")


if __name__ == "__main__":
    main()
