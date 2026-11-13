"""Mini App package: the Telegram web application adapter.

Thin FastAPI adapter like cli.py and telegram.py: it serves the built
frontend, exposes the app.py use-cases as JSON, and receives the bot's
webhook updates. All rules stay in app.py; this package only renders.
"""
