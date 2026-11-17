"""Telegram launcher (ADR-0001): the chat opens the Mini App, onboards strangers.

After the Mini App took over every interaction, the chat keeps only:

- `/start` — greeting plus the open-app button;
- `/id` — status checker: pending / active / blocked in plain language;
- a polite refusal with an open-app button for anything else (voice
  notes included).

The gate is the database access table (schema v2): a stranger's message
auto-registers them as `pending` and notifies admins, so nobody copies
numeric IDs around anymore. Handlers are thin and never touch use-cases:
practice, stats and reports live in the Mini App adapter
(`interactions/miniapp`). The bot still runs long polling here, but
`azure-mini-app-web` registers a webhook instead when MINIAPP_PUBLIC_URL
is set — run either, never both.
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
    User,
    WebAppInfo,
)

from azure_mini_app import settings
from azure_mini_app.interactions.miniapp.access import (
    notify_admins_pending,
    register_access,
)
from azure_mini_app.interactions.miniapp.auth import MiniAppUser, display_name_for
from azure_mini_app.services import access_admin
from azure_mini_app.services.access_admin import AccessChange, NotAdminError
from azure_mini_app.services.storage import Storage

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

PENDING_TEXT = (
    "🕐 Your access request has been recorded and is waiting for "
    "approval.\nYou'll be able to use the trainer once an admin approves it."
)

BLOCKED_TEXT = (
    "⛔ Your access to this trainer is blocked.\nContact the bot owner "
    "if you think this is a mistake."
)


def _launcher_keyboard() -> InlineKeyboardMarkup | None:
    """The Mini App launcher button; needs a public HTTPS URL (ADR-0003)."""
    if not settings.miniapp_public_url:
        return None
    button = InlineKeyboardButton(
        text="Open the trainer", web_app=WebAppInfo(url=settings.miniapp_public_url)
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


class AccessMiddleware(BaseMiddleware):
    """Gate every update on the database access table (schema v2).

    `/start` and `/id` bypass: onboarding and status checking must work
    for anyone. Everyone else needs an `active` Access entry; a caller
    without one is auto-registered as `pending` (closed by default) and
    admins are notified once, so "send the bot a message" replaces the
    old copy-your-ID dance.
    """

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # handlers that bypass the gate (/id) still need the storage
        data["storage"] = self.storage
        tg_user = data.get("event_from_user") or getattr(event, "from_user", None)
        text = getattr(event, "text", None) or ""
        if text.startswith(("/start", "/id")):
            return await handler(event, data)
        if tg_user is None:
            logger.warning("Denied update with no Telegram user: %r", event)
            return None
        registration = register_access(self.storage, _identity_of(tg_user))
        entry = registration.entry
        if entry.status == "active":
            return await handler(event, data)
        if registration.created:
            await notify_admins_pending(registration.user)
        note = PENDING_TEXT if entry.status == "pending" else BLOCKED_TEXT
        if isinstance(event, CallbackQuery):
            await event.answer(note, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(note)
        return None


def _identity_of(tg_user: User) -> MiniAppUser:
    """The same identity the Mini App derives from initData, taken from
    the Telegram update instead."""
    return MiniAppUser(
        telegram_id=tg_user.id,
        display_name=display_name_for(
            tg_user.first_name, tg_user.last_name, tg_user.username, tg_user.id
        ),
    )


# The launcher admin commands (ticket 05); each has a handler below.
ADMIN_COMMANDS = ("/requests", "/approve", "/block", "/users")


class AdminCommandMiddleware(BaseMiddleware):
    """Admin commands are invisible to everyone else (ticket 05).

    A non-admin gets the same fallback as any unknown text — no data,
    no state change — and no admin handler runs. The command layer
    (services/access_admin.py) enforces the same rule again, so admin
    logic stays unreachable even if this gate is bypassed.
    """

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        text = getattr(event, "text", None) or ""
        if not text.startswith(ADMIN_COMMANDS):
            return await handler(event, data)
        tg_user = data.get("event_from_user") or getattr(event, "from_user", None)
        if tg_user is not None and access_admin.is_admin(self.storage, tg_user.id):
            return await handler(event, data)
        logger.info("Hid admin command from non-admin %s", tg_user)
        if isinstance(event, Message):
            await event.answer(FALLBACK_TEXT, reply_markup=_launcher_keyboard())
        return None


def _register_handlers(router: Router) -> None:
    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_id, Command("id"))
    router.message.register(cmd_requests, Command("requests"))
    router.message.register(cmd_approve, Command("approve"))
    router.message.register(cmd_block, Command("block"))
    router.message.register(cmd_users, Command("users"))
    router.message.register(voice_note_refused, F.voice)
    router.message.register(fallback_to_launcher, F.text)


# --- launcher handlers ----------------------------------------------------------


async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT, reply_markup=_launcher_keyboard())


async def cmd_id(message: Message, storage: Storage) -> None:
    """Status checker (bypasses the gate): plain-language pending /
    active / blocked, or how to get a request on file."""
    tg_user = message.from_user
    telegram_id = tg_user.id if tg_user else 0
    entry = storage.get_access(telegram_id) if telegram_id else None
    if entry is None:
        text = (
            "You have no access request on file yet.\n"
            "Send any message to this bot — or open the Mini App — to "
            "request access."
        )
    elif entry.status == "active":
        text = "✅ Your access is active — practice happens in the Mini App." + (
            " You are an admin." if entry.role == "admin" else ""
        )
    elif entry.status == "pending":
        text = "⏳ Your access request is pending — an admin has to approve it."
    else:
        text = (
            "⛔ Your access is blocked.\nContact the bot owner if you "
            "think this is a mistake."
        )
    await message.answer(text)


# --- admin commands (ticket 05) --------------------------------------------

APPROVE_USAGE = "Usage: /approve <telegram id>"
BLOCK_USAGE = "Usage: /block <telegram id>"
NOT_FOUND_TEXT = (
    "No access request for ID <code>{telegram_id}</code> — ask them to "
    "open the Mini App or send any message here first."
)

APPROVE_TEXTS = {
    "activated": (
        "✅ {name} (<code>{telegram_id}</code>) is now active — their "
        "next Mini App request will just work."
    ),
    "reactivated": (
        "✅ {name} (<code>{telegram_id}</code>) was blocked and is "
        "active again — their history was kept."
    ),
    "already_active": "ℹ️ {name} (<code>{telegram_id}</code>) is already active.",
}


BLOCK_TEXTS = {
    "blocked": (
        "⛔ {name} (<code>{telegram_id}</code>) is now blocked — both the "
        "chat and the Mini App refuse them right away."
    ),
    "last_admin": (
        "Refused: {name} (<code>{telegram_id}</code>) is the last active "
        "admin — blocking them would lock the tool out of its own admin "
        "surface."
    ),
}


def _actor_id(message: Message) -> int:
    """0 for userless messages (channel posts): Telegram IDs are always
    positive, so 0 matches no entry and the admin check fails safely."""
    tg_user = message.from_user
    return tg_user.id if tg_user else 0


def _command_target(text: str | None) -> int | None:
    """The <telegram id> argument of /approve or /block, if well-formed."""
    parts = (text or "").split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


async def _reply_fallback(message: Message) -> None:
    """The launcher fallback — also the invisibility reply for admin
    commands, so a hidden command reads exactly like an unknown one."""
    await message.answer(FALLBACK_TEXT, reply_markup=_launcher_keyboard())


async def _apply_admin_change(
    message: Message,
    storage: Storage,
    action: Callable[[Storage, int, int], AccessChange],
    *,
    usage: str,
    texts: dict[str, str],
) -> None:
    """The shared /approve and /block shape. Invisibility comes first:
    a non-admin learns nothing — not even the usage line — before the
    argument is even looked at."""
    actor = _actor_id(message)
    if not access_admin.is_admin(storage, actor):
        await _reply_fallback(message)
        return
    target = _command_target(message.text)
    if target is None:
        await message.answer(usage)
        return
    try:
        change = action(storage, actor, target)
    except NotAdminError:
        # the is_admin verdict raced a status change — stay invisible
        await _reply_fallback(message)
        return
    if change.person is None:
        await message.answer(NOT_FOUND_TEXT.format(telegram_id=target))
        return
    person = change.person
    await message.answer(
        texts[change.result].format(
            name=person.display_name, telegram_id=person.telegram_id
        )
    )


async def cmd_requests(message: Message, storage: Storage) -> None:
    """List everyone waiting for approval (admin-only)."""
    try:
        pending = access_admin.list_pending(storage, _actor_id(message))
    except NotAdminError:
        await _reply_fallback(message)
        return
    if not pending:
        await message.answer("No pending access requests. ✅")
        return
    lines = [
        f"• {person.display_name} — <code>{person.telegram_id}</code>"
        for person in pending
    ]
    await message.answer("⏳ Pending access requests:\n" + "\n".join(lines))


async def cmd_approve(message: Message, storage: Storage) -> None:
    """Activate a pending or blocked user (admin-only)."""
    await _apply_admin_change(
        message, storage, access_admin.approve, usage=APPROVE_USAGE, texts=APPROVE_TEXTS
    )


async def cmd_block(message: Message, storage: Storage) -> None:
    """Revoke access immediately (admin-only, last admin protected)."""
    await _apply_admin_change(
        message, storage, access_admin.block, usage=BLOCK_USAGE, texts=BLOCK_TEXTS
    )


STATUS_MARKS = {"active": "✅", "pending": "⏳", "blocked": "⛔"}


async def cmd_users(message: Message, storage: Storage) -> None:
    """List everyone with their status and role (admin-only)."""
    try:
        people = access_admin.list_all(storage, _actor_id(message))
    except NotAdminError:
        await _reply_fallback(message)
        return
    lines = [
        f"{STATUS_MARKS[person.status]} {person.display_name} "
        f"(<code>{person.telegram_id}</code>) — {person.status}, {person.role}"
        for person in people
    ]
    await message.answer("👥 Everyone with an access entry:\n" + "\n".join(lines))


async def voice_note_refused(message: Message) -> None:
    await message.answer(VOICE_REFUSAL_TEXT, reply_markup=_launcher_keyboard())


async def fallback_to_launcher(message: Message) -> None:
    await message.answer(FALLBACK_TEXT, reply_markup=_launcher_keyboard())


# --- wiring -------------------------------------------------------------------


def create_dispatcher(storage: Storage) -> Dispatcher:
    """Build the launcher dispatcher with the database access gate.

    The router is created per call: aiogram forbids attaching one router
    to two dispatchers, and the web server builds its own dispatcher.
    Storage is passed in by whichever process owns the database (the web
    server shares its instance).
    """
    router = Router()
    _register_handlers(router)
    gate = AccessMiddleware(storage)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.message.middleware(gate)
    dispatcher.message.middleware(AdminCommandMiddleware(storage))
    dispatcher.callback_query.middleware(gate)
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
    storage = Storage(settings.db_path)
    dispatcher = create_dispatcher(storage)
    if settings.miniapp_public_url:
        logger.warning(
            "MINIAPP_PUBLIC_URL is set — the web server registers the webhook. "
            "Running long polling too would double-handle updates; use one or the other."
        )
    logger.info("Launcher bot started (long polling); the access table is the gate")
    asyncio.run(dispatcher.start_polling(bot))


if __name__ == "__main__":
    main()
