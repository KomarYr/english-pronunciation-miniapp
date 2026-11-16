"""Mini App web server: one process, three surfaces (ADR-0003).

- `/` — the built Mini App frontend (`frontend/dist`, Vite output);
- `/api/*` — the JSON API (api.py);
- `/telegram/webhook/{secret}` — Telegram updates fed to the same
  aiogram dispatcher the chat uses.

Storage is created once here and injected into every dependency, so all
SQLite access stays inside this single process. The bot and web parts
share this module but are wired independently: `MINIAPP_PUBLIC_URL` also
registers the webhook at startup, replacing the old long-polling loop.
"""

import logging
from contextlib import asynccontextmanager

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from azure_mini_app import settings
from azure_mini_app.interactions.miniapp import api
from azure_mini_app.interactions.miniapp.auth import validate_dev_mode
from azure_mini_app.interactions.telegram import create_dispatcher
from azure_mini_app.services.storage import Storage

logger = logging.getLogger(__name__)

FRONTEND_DIST = settings.PROJECT_ROOT / "frontend" / "dist"

_PLACEHOLDER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>azure-mini-app</title>
<style>body{font-family:system-ui;display:grid;place-items:center;min-height:100vh;color:#555}
code{background:#f4f4f4;padding:2px 6px;border-radius:4px}</style></head>
<body><div><h1>azure-mini-app</h1>
<p>The Mini App frontend is not built yet.</p>
<p>Run: <code>cd frontend && npm install && npm run build</code></p>
</div></body></html>"""


def _webhook_path() -> str:
    return f"/telegram/webhook/{settings.telegram_webhook_secret or 'telegram'}"


def create_app() -> FastAPI:
    validate_dev_mode()
    storage = Storage(settings.db_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if application.state.bot is not None and settings.miniapp_public_url:
            url = settings.miniapp_public_url.rstrip("/") + _webhook_path()
            # secret_token makes Telegram send X-Telegram-Bot-Api-Secret-Token
            # on every update — the webhook route below rejects requests
            # without it, so registration without the secret 403s everything.
            await application.state.bot.set_webhook(
                url, secret_token=settings.telegram_webhook_secret
            )
            logger.info("Telegram webhook registered at %s", url)
        yield

    application = FastAPI(title="azure-mini-app", lifespan=lifespan)
    application.state.storage = storage

    application.include_router(api.router)

    @application.middleware("http")
    async def frontend_cache_headers(request: Request, call_next):
        """Cache rules for the built frontend. The entry HTML must never be
        cached: Vite fingerprints asset URLs, so a webview holding a stale
        index.html keeps running the previous build even after a redeploy.
        Hashed /assets/ files are immutable, so they may cache forever."""
        response = await call_next(request)
        if request.method == "GET":
            path = request.url.path
            if path in ("/", "/index.html"):
                response.headers["Cache-Control"] = "no-store"
            elif path.startswith("/assets/"):
                response.headers["Cache-Control"] = (
                    "public, max-age=31536000, immutable"
                )
        return response

    if settings.telegram_bot_token:
        application.state.bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        application.state.dispatcher = create_dispatcher()
    else:
        application.state.bot = None
        application.state.dispatcher = None

    @application.post(_webhook_path())
    async def telegram_webhook(request: Request) -> PlainTextResponse:
        expected = settings.telegram_webhook_secret
        if expected:
            provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if provided != expected:
                # Never log the secret itself; say which side went wrong.
                if provided is None:
                    logger.warning(
                        "Webhook update rejected: secret header missing — "
                        "was the webhook registered without secret_token?"
                    )
                else:
                    logger.warning("Webhook update rejected: secret header mismatch")
                return PlainTextResponse("forbidden", status_code=403)
        bot = application.state.bot
        dispatcher = application.state.dispatcher
        if bot is None or dispatcher is None:  # pragma: no cover — guarded above
            return PlainTextResponse("bot not configured", status_code=503)
        update_json = await request.json()
        try:
            update = Update.model_validate(update_json, context={"bot": bot})
        except ValueError:
            return PlainTextResponse("bad update payload", status_code=400)
        await dispatcher.feed_update(bot, update)
        return PlainTextResponse("OK")

    @application.get("/health", include_in_schema=False)
    async def health() -> PlainTextResponse:
        return PlainTextResponse("OK")

    if FRONTEND_DIST.is_dir():
        application.mount(
            "/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend"
        )
    else:

        @application.get("/", include_in_schema=False)
        async def frontend_missing() -> HTMLResponse:
            return HTMLResponse(_PLACEHOLDER)

    return application


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if settings.miniapp_public_url and settings.telegram_bot_token:
        logger.info(
            "Point Telegram at %s%s (registered automatically on startup)",
            settings.miniapp_public_url.rstrip("/"),
            _webhook_path(),
        )
    elif settings.telegram_bot_token:
        logger.info(
            "No MINIAPP_PUBLIC_URL — webhook not registered. Set it in .env, "
            "or keep the long-polling bot via `uv run azure-mini-app-telegram`."
        )
    uvicorn.run(
        create_app(),
        host=settings.miniapp_host,
        port=settings.miniapp_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
