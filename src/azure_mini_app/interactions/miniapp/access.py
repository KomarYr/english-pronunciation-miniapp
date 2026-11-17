"""Mini App access lifecycle: the database decides who gets in.

Every request with a valid identity is resolved against the access
table (schema v2): a stranger is auto-registered as `pending` (user row
plus pending access entry, idempotent), a pending or blocked caller
gets a typed 403 the frontend renders as its own screen, and an active
caller proceeds — so a block takes effect mid-session, not just on the
next login. Admin notification of a new pending user rides in
`notify_admins_pending`, sent directly through the Bot API so it works
in both webhook and long-polling modes.
"""

import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from azure_mini_app import settings
from azure_mini_app.domain import User
from azure_mini_app.interactions.miniapp.auth import MiniAppUser
from azure_mini_app.services.storage import AccessEntry, Storage

logger = logging.getLogger(__name__)

# Per-send ceiling: the notification is best-effort and rides inside the
# stranger's first request, which must not hang on a slow Bot API.
ADMIN_NOTIFY_TIMEOUT_SECONDS = 5

ADMIN_NOTIFY_TEXT = (
    "🔔 <b>{display_name}</b> (Telegram ID <code>{telegram_id}</code>) "
    "opened the trainer and is waiting for access approval."
)


@dataclass(frozen=True, slots=True)
class Registration:
    """Who the caller is, what their access says, and whether it's new."""

    user: User
    entry: AccessEntry
    created: bool


def register_access(storage: Storage, identity: MiniAppUser) -> Registration:
    """Load the caller's user row and access entry, registering if new.

    Idempotent: the user row matches on telegram_id only and the access
    entry is created once — reopening the app never duplicates either.
    A new entry is `pending` (closed by default); an existing entry is
    left exactly as its status says.
    """
    user = storage.get_or_create_user(
        identity.display_name, telegram_id=identity.telegram_id
    )
    assert user.id is not None, "get_or_create_user always saves the row"
    entry = storage.get_access(identity.telegram_id)
    if entry is None:
        entry = storage.ensure_access(identity.telegram_id, user.id)
        return Registration(user=user, entry=entry, created=True)
    return Registration(user=user, entry=entry, created=False)


async def notify_admins_pending(user: User) -> None:
    """Tell every admin that someone requested access. Best effort.

    Sent straight to the Bot API (no dispatcher involved), so it works
    whether the bot runs webhook or long polling. Failures are logged,
    never raised — the registration itself already succeeded.
    """
    if not settings.telegram_bot_token or not settings.admin_telegram_ids:
        return
    if user.telegram_id is None:  # registration always carries one
        return
    text = ADMIN_NOTIFY_TEXT.format(
        display_name=user.display_name, telegram_id=user.telegram_id
    )
    try:
        async with Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        ) as bot:
            for admin_id in sorted(settings.admin_telegram_ids):
                try:
                    await bot.send_message(
                        admin_id, text, request_timeout=ADMIN_NOTIFY_TIMEOUT_SECONDS
                    )
                except Exception:
                    logger.warning(
                        "Admin notification to %s failed", admin_id, exc_info=True
                    )
    except Exception:
        logger.warning("Admin notification failed", exc_info=True)
