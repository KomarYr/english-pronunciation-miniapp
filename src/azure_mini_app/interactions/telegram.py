"""Telegram launcher (ADR-0001): the chat opens the Mini App, nothing else.

After the Mini App took over every interaction, the chat keeps only:

- `/start` — greeting plus the open-app button;
- `/id` — onboarding strangers into the closed allowlist;
- a polite refusal with an open-app button for anything else (voice
  notes included).

Handlers are thin and never touch use-cases: practice, stats and reports
live in the Mini App adapter (`interactions/miniapp`). The bot still runs
long polling here, but `azure-mini-app-web` registers a webhook instead
when MINIAPP_PUBLIC_URL is set — run either, never both.
"""

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
    WebAppInfo,
)

from azure_mini_app import settings
from azure_mini_app.interactions.miniapp.auth import is_allowed

logger = logging.getLogger(__name__)

START_TEXT = (
    "Hi! 👋\n\n"
    "All practice happens in the Mini App: pick an exercise, read it "
    "aloud, get a per-word report.\n"
    "Open it with the button below — or the bot's Menu button."
)

VOICE_REFUSAL_TEXT = (
    "Voice practice moved into the Mini App — same exercises, same "
    "stats, richer reports.\n"
    "Open the app and use the 🎤 button there."
)

FALLBACK_TEXT = (
    "Everything happens in the Mini App — open it via the Menu button (or /start)."
)

NOT_ALLOWED_TEXT = (
    "You are not allowed to use this trainer yet.\n"
    "Your Telegram ID: <code>{telegram_id}</code> — send it to the bot "
    "owner to be added."
)


def _launcher_keyboard() -> InlineKeyboardMarkup | None:
    """The Mini App launcher button; needs a public HTTPS URL (ADR-0003)."""
    if not settings.miniapp_public_url:
        return None
    button = InlineKeyboardButton(
        text="Open the trainer", web_app=WebAppInfo(url=settings.miniapp_public_url)
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


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
        note = NOT_ALLOWED_TEXT.format(telegram_id=getattr(tg_user, "id", "?"))
        if isinstance(event, CallbackQuery):
            await event.answer(note, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(note)
        return None


def _register_handlers(router: Router) -> None:
    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_id, Command("id"))
    router.message.register(voice_note_refused, F.voice)
    router.message.register(fallback_to_launcher, F.text)


# --- launcher handlers ----------------------------------------------------------


async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT, reply_markup=_launcher_keyboard())


async def cmd_id(message: Message) -> None:
    tg_user = message.from_user
    telegram_id = tg_user.id if tg_user else 0
    await message.answer(
        f"Your Telegram ID: <code>{telegram_id}</code>\n"
        + (
            "✅ You are allowed to use this trainer."
            if is_allowed(telegram_id)
            else "Send it to the bot owner to be added."
        )
    )


async def voice_note_refused(message: Message) -> None:
    await message.answer(VOICE_REFUSAL_TEXT, reply_markup=_launcher_keyboard())


async def fallback_to_launcher(message: Message) -> None:
    await message.answer(FALLBACK_TEXT, reply_markup=_launcher_keyboard())


# --- wiring -------------------------------------------------------------------


def create_dispatcher() -> Dispatcher:
    """Build the launcher dispatcher with the allowlist gate.

    The router is created per call: aiogram forbids attaching one router
    to two dispatchers, and the web server builds its own dispatcher.
    """
    router = Router()
    _register_handlers(router)
    dispatcher = Dispatcher(storage=MemoryStorage())
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
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = create_dispatcher()
    if settings.miniapp_public_url:
        logger.warning(
            "MINIAPP_PUBLIC_URL is set — the web server registers the webhook. "
            "Running long polling too would double-handle updates; use one or the other."
        )
    logger.info(
        "Launcher bot started (long polling); allowed users: %s",
        len(settings.allowed_telegram_ids),
    )
    asyncio.run(dispatcher.start_polling(bot))


if __name__ == "__main__":
    main()
