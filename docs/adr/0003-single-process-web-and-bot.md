# One process serves the Mini App, the API, and the Telegram webhook

A single uvicorn process serves the Mini App static files, the JSON API,
and the aiogram webhook (replacing long polling). One process means one
SQLite writer, so the quota ledger's per-operation-connection pattern
needs no cross-process locking.

## Considered options

- **Two processes from day one (rejected)**: introduces concurrent SQLite
  writers and doubles deployment surface for a single-user tool.

## Consequences

- The bot and web parts stay independently runnable (each behind its own
  entrypoint against the same storage), so splitting into two processes
  later is an ops change, not a code rewrite.
- The public tunnel must carry both the webhook and the app/API traffic.
