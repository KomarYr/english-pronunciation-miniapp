"""Access administration: the policy behind the launcher admin commands.

Approving, blocking and listing people is the admins' side of the
access lifecycle. This module owns the policy — who may act, what may
be listed or changed — and never renders anything: replies are the
launcher handlers' job (interactions/telegram.py). Enforcement lives
here (the command layer) *and* in the launcher middleware, so
non-admins can reach neither the data nor the state changes.

All functions take a Telegram ID for the acting admin; the audit trail
records the underlying users.id, resolved here.
"""

import logging
from dataclasses import dataclass
from typing import Literal

from azure_mini_app.services.storage import (
    AccessEntry,
    AccessRole,
    AccessStatus,
    Storage,
)

logger = logging.getLogger(__name__)

ApproveResult = Literal["activated", "reactivated", "already_active", "not_found"]
BlockResult = Literal["blocked", "last_admin", "not_found"]


class NotAdminError(Exception):
    """The caller holds no active admin entry; nothing was read or changed."""


@dataclass(frozen=True, slots=True)
class Person:
    """An access entry together with the display name admins see."""

    entry: AccessEntry
    display_name: str

    @property
    def telegram_id(self) -> int:
        return self.entry.telegram_id

    @property
    def status(self) -> AccessStatus:
        return self.entry.status

    @property
    def role(self) -> AccessRole:
        return self.entry.role


@dataclass(frozen=True, slots=True)
class AccessChange:
    """What an approve or block did, and who it happened to."""

    result: ApproveResult | BlockResult
    person: Person | None = None


def _active_admin_entry(storage: Storage, telegram_id: int) -> AccessEntry | None:
    """An admin only counts while their own entry is active."""
    entry = storage.get_access(telegram_id)
    if entry is not None and entry.role == "admin" and entry.status == "active":
        return entry
    return None


def is_admin(storage: Storage, telegram_id: int) -> bool:
    return _active_admin_entry(storage, telegram_id) is not None


def _admin_entry(storage: Storage, actor_telegram_id: int) -> AccessEntry:
    """The acting admin's entry — also their users.id for the audit trail."""
    entry = _active_admin_entry(storage, actor_telegram_id)
    if entry is None:
        logger.info("Refused admin action by %s", actor_telegram_id)
        raise NotAdminError(f"{actor_telegram_id} is not an active admin")
    return entry


def _person(storage: Storage, entry: AccessEntry) -> Person:
    user = storage.get_user(entry.user_id)
    return Person(
        entry=entry,
        display_name=user.display_name if user else f"tg-{entry.telegram_id}",
    )


def list_pending(storage: Storage, actor_telegram_id: int) -> list[Person]:
    """Everyone waiting for approval, oldest request first."""
    _admin_entry(storage, actor_telegram_id)
    return [
        _person(storage, entry)
        for entry in storage.list_access_entries(status="pending")
    ]


def list_all(storage: Storage, actor_telegram_id: int) -> list[Person]:
    """The whole access picture, oldest entry first."""
    _admin_entry(storage, actor_telegram_id)
    return [_person(storage, entry) for entry in storage.list_access_entries()]


def approve(
    storage: Storage, actor_telegram_id: int, target_telegram_id: int
) -> AccessChange:
    """Activate a pending stranger, or re-activate a blocked user.

    Data always survives: only the status column moves, and the audit
    trail records the admin's users.id as the actor.
    """
    actor = _admin_entry(storage, actor_telegram_id)
    entry = storage.get_access(target_telegram_id)
    if entry is None:
        return AccessChange("not_found")
    if entry.status == "active":
        return AccessChange("already_active", _person(storage, entry))
    result: ApproveResult = "activated" if entry.status == "pending" else "reactivated"
    entry = storage.set_access_status(
        target_telegram_id, "active", actor_id=actor.user_id
    )
    return AccessChange(result, _person(storage, entry))


def block(
    storage: Storage, actor_telegram_id: int, target_telegram_id: int
) -> AccessChange:
    """Revoke access on both surfaces immediately.

    The last-admin guard keeps the tool from locking itself out of its
    own admin surface: an *active* admin may not be blocked while no
    other active admin would remain.
    """
    actor = _admin_entry(storage, actor_telegram_id)
    entry = storage.get_access(target_telegram_id)
    if entry is None:
        return AccessChange("not_found")
    if (
        entry.role == "admin"
        and entry.status == "active"
        and storage.count_active_admins() == 1
    ):
        return AccessChange("last_admin", _person(storage, entry))
    entry = storage.set_access_status(
        target_telegram_id, "blocked", actor_id=actor.user_id
    )
    return AccessChange("blocked", _person(storage, entry))
