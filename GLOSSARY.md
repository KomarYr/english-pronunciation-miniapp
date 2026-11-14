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

**Allowlist**:
The closed list of Telegram IDs permitted to use the tool. Nobody is
allowed until their ID is on it.
_Avoid_: whitelist, access list

### Surfaces

**Mini App**:
The Telegram web application inside the chat client. The single surface
for all user interactions — practice, custom texts, mistake bank, stats.
_Avoid_: webapp, web view, client

**Launcher**:
The chat surface after its demotion: it opens the Mini App and onboards
strangers, and does nothing else.
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
