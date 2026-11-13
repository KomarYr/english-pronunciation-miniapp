# The Mini App fully replaces chat interactions

The Telegram Mini App is the single surface for all user interactions —
practice, custom texts, mistake bank, stats, and quota display. The chat
is demoted to a launcher: it keeps only `/start` and `/id` (onboarding
strangers into the closed allowlist) and a button to open the Mini App;
incoming voice notes are refused with an open-app button instead of being
assessed. This replaces the previous hybrid where voice-note practice
lived in the chat.

## Considered options

- **Hybrid (rejected)**: Mini App plus the existing chat voice-note flow.
  Rejected because two live practice paths must both keep passing the
  test suite and stay quota-correct — double the surface, no user benefit
  once the Mini App can record.
- **Mini App for practice only (rejected)**: keeping stats/mistakes in
  chat. Rejected because users would still fall back to a dying chat UI.

## Consequences

- The aiogram adapter shrinks to webhook + launcher handlers; chat-side
  HTML report rendering becomes dead code.
- The CLI and the legacy mic demo stay for debugging audio and Azure
  without Telegram.
