# 01: Pin the signature-field test vector

**What to build:** Certainty that the initData validator is correct on modern
Telegram clients. A real captured initData payload carrying the Bot API 8.0
`signature` field is pinned as a test vector; the bot-token HMAC data-check
string construction is verified against it and corrected if needed. The
conflicting sources (one says `signature` stays inside the hashed string, one
shows pre-8.0 validators rejecting such payloads) are settled empirically, so
every later ticket builds on a validator known to match the spec.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] A real initData payload containing a `signature` field is pinned as a
      test fixture (captured with the bot owner's help by opening the Mini App
      once).
- [ ] The validator's behavior on that payload is decided and documented:
      whether `signature` belongs in the data-check string or must be excluded,
      per the Telegram spec.
- [ ] The validator passes the pinned vector; if the data-check string
      construction needed a fix, it is made and the existing auth tests still
      pass.
- [ ] A note records the finding (sources disagreed; this is now empirical).
