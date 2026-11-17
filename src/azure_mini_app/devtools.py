"""Developer CLI for the access database: seed, reset, list.

The admin commands manage access from the chat; this tool exists for
the cases they cannot reach — chiefly the fixed dev user
(MINIAPP_DEV_USER_ID), who never sends a Telegram request and can
therefore never be /approved. `seed` arranges the dev user's active
entry idempotently, on fresh and existing databases alike (the env
bootstrap only runs at first run); `reset` recreates the database from
the .env seeds behind a confirmation prompt; `list` shows the whole
access picture. Run as `uv run azure-mini-app-dev <command>`.

Everything goes through the real Storage API, so every change lands in
the audit trail exactly like any other access change — no SQL is
hand-rolled here.
"""

import argparse
import sys
from pathlib import Path

from azure_mini_app import settings
from azure_mini_app.services.storage import Storage


def seed_dev_user(storage: Storage) -> str:
    """Ensure the dev user holds an active access entry; idempotent.

    Missing → created active; pending/blocked → activated; already
    active → no write at all. Returns the report line to print.
    """
    dev_id = settings.miniapp_dev_user_id
    who = f"{settings.miniapp_dev_user_name} ({dev_id})"
    entry = storage.get_access(dev_id)
    if entry is None:
        user = storage.get_or_create_user(
            settings.miniapp_dev_user_name, telegram_id=dev_id
        )
        assert user.id is not None, "get_or_create_user always saves the row"
        storage.ensure_access(dev_id, user.id, status="active")
        return f"Created active access entry for {who}."
    if entry.status != "active":
        storage.set_access_status(dev_id, "active")
        return f"{who} was {entry.status} → now active."
    return f"{who} is already active — nothing to do."


def print_access_table(storage: Storage) -> None:
    """Every access entry, one line each — the debugging view."""
    entries = storage.list_access_entries()
    if not entries:
        print("No access entries.")
        return
    for entry in entries:
        user = storage.get_user(entry.user_id)
        name = user.display_name if user else f"tg-{entry.telegram_id}"
        print(f"{entry.telegram_id:>12}  {entry.status:<8} {entry.role:<6} {name}")


def reset_database(*, assume_yes: bool) -> int:
    """Delete and recreate the database from the .env bootstrap seeds.

    WAL sidecars go with the main file. With dev mode on, the dev user
    is seeded active afterwards — same effect as `seed`, one command.
    """
    db_path = Path(settings.db_path)
    if not assume_yes:
        answer = input(f"Delete {db_path} and recreate it from .env seeds? [yes/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted — nothing deleted.")
            return 1
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    storage = Storage(db_path)
    if settings.miniapp_dev_mode:
        print(seed_dev_user(storage))
    print(f"Recreated {db_path}")
    print_access_table(storage)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="azure-mini-app-dev",
        description="Developer tool for the access database.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="ensure the dev user has an active access entry")
    reset = sub.add_parser(
        "reset", help="delete and recreate the database from .env seeds"
    )
    reset.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt"
    )
    sub.add_parser("list", help="print every access entry")

    args = parser.parse_args(argv)
    if args.command == "seed":
        print(seed_dev_user(Storage(settings.db_path)))
        return 0
    if args.command == "list":
        print_access_table(Storage(settings.db_path))
        return 0
    return reset_database(assume_yes=args.yes)


if __name__ == "__main__":
    sys.exit(main())
