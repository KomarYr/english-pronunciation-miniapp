# 03: Mini App access lifecycle, end-to-end

**What to build:** The complete stranger story inside the Mini App. A stranger
who opens the app with valid initData is auto-registered as pending and sees a
"waiting for approval" screen; the bot owner (admins) gets a Telegram
notification that someone requested access. A blocked user sees a distinct
"you were blocked" screen instead of waiting in vain. An active user gets the
full app exactly as today, checked on every request so a block takes effect
mid-session. Pending and blocked users keep their identity data; a blocked
user's attempts and exercises are untouched.

**Blocked by:** 02.

**Status:** ready-for-agent

- [ ] A stranger's valid initData creates a pending user row plus pending
      access entry (idempotent: reopening does not duplicate) and the API
      returns 403 with the `pending` code.
- [ ] A blocked user's requests return 403 with the `blocked` code.
- [ ] The frontend renders distinct screens for `pending` and `blocked`,
      driven by the 403 code contract (manual verification, no new frontend
      test harness).
- [ ] Creating a pending user sends a best-effort notification message to
      each admin via the Bot API (works in webhook and long-polling modes).
- [ ] An active user's requests behave exactly as before the change.
- [ ] Blocked users' stored attempts and custom exercises survive.
