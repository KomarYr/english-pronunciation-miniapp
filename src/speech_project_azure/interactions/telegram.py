"""Telegram bot (M5): voice notes → WAV → the same app.py use-cases as the CLI.

Long polling, in-memory FSM, HTML reports from the shared rendering module.
Handlers are thin: they resolve the Telegram user, dispatch work to the
sync use-cases via asyncio.to_thread, and render results or typed errors.
"""

import asyncio
import logging
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from speech_project_azure import app, settings
from speech_project_azure.domain import TextValidationError, User
from speech_project_azure.interactions.rendering import (
    render_mistakes_html,
    render_report_html,
    render_stats_html,
)
from speech_project_azure.services.azure_assessment import (
    AssessmentCanceled,
    AssessmentError,
    NoSpeechDetected,
)
from speech_project_azure.services.quota import QuotaExceededError
from speech_project_azure.services.storage import Storage
from speech_project_azure.services.voice import VoiceConversionError, ogg_to_wav

logger = logging.getLogger(__name__)

MAX_VOICE_SECONDS = 120  # guard against accidental long recordings

HELP_TEXT = (
    "English pronunciation trainer.\n\n"
    "/practice — pick an exercise and read it aloud\n"
    "/text — practice your own English text (10–50 words)\n"
    "/mistakes — your weakest exercises\n"
    "/stats — attempts, scores and audio budget\n"
    "/cancel — cancel the current action\n\n"
    "Send a voice note after choosing an exercise."
)


class PracticeStates(StatesGroup):
    """FSM: the bot is waiting for a voice note (or a custom text)."""

    waiting_for_voice = State()
    waiting_for_text = State()


def is_allowed(telegram_id: int) -> bool:
    """Closed by default: users must be listed in ALLOWED_TELEGRAM_IDS."""
    return telegram_id in settings.allowed_telegram_ids


class AllowlistMiddleware(BaseMiddleware):
    """Gate every update: only /start and /id bypass the allowlist.

    New users send /id to learn their Telegram ID, then the owner appends
    it to ALLOWED_TELEGRAM_IDS in .env and restarts the bot.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user") or getattr(event, "from_user", None)
        text = getattr(event, "text", None) or ""
        bypass = text.startswith(("/start", "/id"))
        if bypass or (tg_user is not None and is_allowed(tg_user.id)):
            return await handler(event, data)

        logger.info("Denied update from non-allowed user %s", tg_user)
        note = (
            "You are not allowed to use this bot yet.\n"
            f"Your Telegram ID: <code>{getattr(tg_user, 'id', '?')}</code> —\n"
            "send it to the bot owner."
        )
        if isinstance(event, CallbackQuery):
            await event.answer(note, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(note)
        return None


def _resolve_user(storage: Storage, tg_user) -> User:
    """Same DB as the CLI: Telegram login matches on telegram_id."""
    return storage.get_or_create_user(tg_user.full_name, telegram_id=tg_user.id)


def _categories_keyboard(storage: Storage, user: User) -> InlineKeyboardMarkup:
    categories = sorted({e.category for e in app.list_exercises(storage, user)})
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=category, callback_data=f"cat:{category}")]
            for category in categories
        ]
    )


def _exercises_keyboard(
    storage: Storage, user: User, category: str
) -> InlineKeyboardMarkup:
    exercises = [e for e in app.list_exercises(storage, user) if e.category == category]
    buttons = [
        [InlineKeyboardButton(text=e.title, callback_data=f"ex:{e.id}")]
        for e in exercises
    ]
    buttons.append([InlineKeyboardButton(text="« Categories", callback_data="cats:")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


router = Router()


# --- commands ---------------------------------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(f"Hi! 👋\n\n{HELP_TEXT}")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    tg_user = message.from_user
    telegram_id = tg_user.id if tg_user else 0
    allowed = is_allowed(telegram_id)
    await message.answer(
        f"Your Telegram ID: <code>{telegram_id}</code>\n"
        + (
            "✅ You are allowed to use this bot."
            if allowed
            else "Send it to the bot owner to be added."
        )
    )


@router.message(Command("practice"))
async def cmd_practice(message: Message, storage: Storage, state: FSMContext) -> None:
    user = _resolve_user(storage, message.from_user)
    await state.clear()
    await message.answer(
        "Choose a category:", reply_markup=_categories_keyboard(storage, user)
    )


@router.message(Command("text"))
async def cmd_text(message: Message, state: FSMContext) -> None:
    await state.set_state(PracticeStates.waiting_for_text)
    await message.answer(
        "Send the English text you want to practice "
        f"({settings.CUSTOM_TEXT_MIN_WORDS}–{settings.CUSTOM_TEXT_MAX_WORDS} words), one message."
    )


@router.message(Command("mistakes"))
async def cmd_mistakes(message: Message, storage: Storage) -> None:
    user = _resolve_user(storage, message.from_user)
    await message.answer(render_mistakes_html(app.mistake_bank(storage, user)))


@router.message(Command("stats"))
async def cmd_stats(message: Message, storage: Storage) -> None:
    user = _resolve_user(storage, message.from_user)
    user_stats, quota_status = app.app_stats(storage, user)
    await message.answer(render_stats_html(user_stats, quota_status))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Canceled. /practice to start again.")


# --- inline keyboard navigation ---------------------------------------------


@router.callback_query(F.data == "cats:")
async def cb_categories(
    call: CallbackQuery, storage: Storage, state: FSMContext
) -> None:
    if not isinstance(call.message, Message):
        await call.answer()
        return
    user = _resolve_user(storage, call.from_user)
    await state.clear()
    await call.message.edit_reply_markup(
        reply_markup=_categories_keyboard(storage, user)
    )
    await call.answer()


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(call: CallbackQuery, storage: Storage, state: FSMContext) -> None:
    if not isinstance(call.message, Message):
        await call.answer()
        return
    category = (call.data or "").removeprefix("cat:")
    user = _resolve_user(storage, call.from_user)
    await call.message.edit_reply_markup(
        reply_markup=_exercises_keyboard(storage, user, category)
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex:"))
async def cb_exercise(call: CallbackQuery, storage: Storage, state: FSMContext) -> None:
    exercise_id = (call.data or "").removeprefix("ex:")
    exercise = storage.get_exercise(exercise_id)
    if exercise is None:
        await call.answer("That exercise no longer exists.", show_alert=True)
        return
    if not isinstance(call.message, Message):
        await call.answer()
        return
    await state.set_state(PracticeStates.waiting_for_voice)
    await state.update_data(exercise_id=exercise.id)
    await call.message.answer(
        f"<b>{exercise.title}</b>\n\n{exercise.reference_text}\n\n"
        "🎙 Now record a voice note reading this text."
    )
    await call.answer()


# --- practice input: text, then voice ----------------------------------------


@router.message(PracticeStates.waiting_for_text, F.text)
async def receive_custom_text(
    message: Message, storage: Storage, state: FSMContext
) -> None:
    assert message.text is not None
    try:
        app.validate_custom_text(message.text)
    except TextValidationError as error:
        await message.answer(f"❌ {error}\nTry again, or /cancel.")
        return
    await state.set_state(PracticeStates.waiting_for_voice)
    await state.update_data(custom_text=" ".join(message.text.split()))
    await message.answer(
        "✅ Text accepted:\n"
        + " ".join(message.text.split())
        + "\n\n🎙 Now record a voice note reading it."
    )


@router.message(PracticeStates.waiting_for_voice, F.voice)
async def receive_voice(
    message: Message, storage: Storage, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    user = _resolve_user(storage, message.from_user)

    voice = message.voice
    if voice is None:  # unreachable behind the F.voice filter; satisfies the type
        return
    if voice.duration > MAX_VOICE_SECONDS:
        await message.answer(
            f"That recording is {voice.duration}s — keep it under {MAX_VOICE_SECONDS}s. Try again."
        )
        return

    with tempfile.TemporaryDirectory() as tmp:
        ogg_path = Path(tmp) / "voice.ogg"
        file = await bot.get_file(voice.file_id)
        if file.file_path is None:
            await message.answer("⚠️ Could not download that voice note — try again.")
            return
        await bot.download_file(file.file_path, ogg_path)

        try:
            wav_path = await asyncio.to_thread(ogg_to_wav, ogg_path, Path(tmp))
            if "custom_text" in data:
                attempt = await asyncio.to_thread(
                    app.assess_custom_text, storage, user, data["custom_text"], wav_path
                )
            else:
                exercise = storage.get_exercise(data["exercise_id"])
                if exercise is None:
                    await message.answer(
                        "That exercise no longer exists — /practice again."
                    )
                    await state.clear()
                    return
                attempt = await asyncio.to_thread(
                    app.record_attempt, storage, user, exercise, wav_path
                )
        except NoSpeechDetected:
            await message.answer(
                "🤫 I didn't hear anything — hold the mic button and read aloud."
            )
            return
        except AssessmentCanceled as error:
            logger.warning("Assessment canceled: %s", error)
            await message.answer(f"⚠️ The service canceled the assessment:\n{error}")
            return
        except QuotaExceededError as status_error:
            await message.answer(
                "🚫 Monthly audio budget is exhausted — practice resets on the 1st.\n"
                f"{status_error}"
            )
            return
        except VoiceConversionError as error:
            await message.answer(f"⚠️ {error}")
            return
        except AssessmentError as error:
            await message.answer(f"⚠️ Assessment problem: {error}")
            return

    await message.answer(render_report_html(attempt))
    await state.clear()
    await message.answer("Practice again? /practice or /text")


@router.message(F.voice)
async def voice_without_exercise(message: Message) -> None:
    await message.answer(
        "Choose an exercise first: /practice (or /text to use your own text)."
    )


@router.message(PracticeStates.waiting_for_text)
async def waiting_text_got_non_text(message: Message) -> None:
    await message.answer("Send the text as a plain message, or /cancel.")


# --- wiring -------------------------------------------------------------------


def create_dispatcher(storage: Storage) -> Dispatcher:
    """Build the dispatcher with DI: handlers receive `storage` by name."""
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["storage"] = storage
    dispatcher.message.middleware(AllowlistMiddleware())
    dispatcher.callback_query.middleware(AllowlistMiddleware())
    dispatcher.include_router(router)
    return dispatcher


def main() -> None:
    if not settings.telegram_bot_token:
        sys.exit("Set TELEGRAM_BOT_TOKEN in .env")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    storage = Storage(settings.db_path)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = create_dispatcher(storage)
    logger.info(
        "Bot started (long polling); allowed users: %s",
        len(settings.allowed_telegram_ids),
    )
    asyncio.run(dispatcher.start_polling(bot))


if __name__ == "__main__":
    main()
