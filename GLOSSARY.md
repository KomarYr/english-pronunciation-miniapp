# English Pronunciation Trainer

A personal English-practice tool built on Azure Speech pronunciation
assessment. The user reads an exercise aloud and receives a per-word
report; weak exercises accumulate in a mistake bank.

## Language

### Practice

**Exercise**:
A text the user practices reading aloud. Either builtin (shipped in the
catalog) or a custom text the user added.
_Avoid_: task, lesson, drill

**Custom text**:
An exercise the user pasted themselves; it dedupes by hash of the
normalized text, so the same text is saved once.
_Avoid_: personal text, my text

**Reference text**:
The exact text the user is asked to read for an attempt. An attempt
snapshots it so history stays truthful even if the exercise changes.
_Avoid_: source text, target text

**Attempt**:
One reading of a reference text, submitted as audio and scored.
_Avoid_: try, session

**Score report**:
The per-attempt outcome: overall scores (pronunciation, accuracy,
fluency, completeness, prosody) plus word scores.
_Avoid_: result, feedback

**Word score**:
A single word's outcome in an attempt (score and verdict: said wrong,
skipped, or extra).
_Avoid_: word result

**Mistake bank**:
The derived set of exercises whose attempts average below 70/100.
It is never stored — it self-heals when an exercise is practiced well.
_Avoid_: weak list, error bank

### People & access

**User**:
A person practicing the language, identified by their Telegram ID.
_Avoid_: account, profile, customer

**Access entry**:
One row per Telegram ID in the database: the person's word on whether
they may use the tool. Created pending automatically; only an Admin
moves it to active. The sole source of truth after the first run.
_Avoid_: allowlist, whitelist, account

**Status**:
An Access entry's state: `pending`, `active` or `blocked`. Pending
means waiting for an Admin's approval; blocked means refused on both
surfaces, immediately and reversibly.
_Avoid_: permission, flag

**Admin**:
A person whose Access entry carries the admin role: they manage access
from the chat (`/requests`, `/approve`, `/block`, `/users`). Seeded
once from `ADMIN_TELEGRAM_IDS` on the first run.
_Avoid_: owner, moderator

### Surfaces

**Mini App**:
The Telegram web application inside the chat client. The single surface
for all user interactions — practice, custom texts, mistake bank, stats.
_Avoid_: webapp, web view, client

**Launcher**:
The chat surface after its demotion: it opens the Mini App, onboards
strangers and carries the admin access commands (ADR-0005 supersedes
ADR-0001's "nothing else" clause on that one point).
_Avoid_: bot

### Budget

**Audio budget**:
The monthly amount of audio the Azure free tier allows per subscription.
Every second of audio — including silent attempts — consumes it.
_Avoid_: quota (reserve for the ledger), minutes

**Quota ledger**:
The append-only record of every audio transaction, the source of truth
for the audio budget.
_Avoid_: usage log, history
