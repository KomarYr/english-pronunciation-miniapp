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

- [x] A real initData payload containing a `signature` field is pinned as a
      test fixture (captured with the bot owner's help by opening the Mini App
      once).
- [x] The validator's behavior on that payload is decided and documented:
      whether `signature` belongs in the data-check string or must be excluded,
      per the Telegram spec.
- [x] The validator passes the pinned vector; if the data-check string
      construction needed a fix, it is made and the existing auth tests still
      pass.
- [x] A note records the finding (sources disagreed; this is now empirical).

## Finding (2026-11-17)

Captured a real Bot API 8.0 initData in Telegram Desktop (auth_date
1794918356 = 2026-11-17 12:25:56 UTC), carrying both `signature` and
`hash`. Computed the bot-token HMAC over the data-check string both
ways against the published `hash`:

- all fields **except `hash`** (`signature` **included**): **exact
  match** (`fbd85baa83f1fd90...`)
- all fields except `hash` and `signature` (`signature` excluded):
  mismatch (`5ba252a1cb17...`)

So `signature` belongs **inside** the data-check string — the "every
field except `hash`" construction the validator already used is
correct, and the pre-8.0 validators' rejection of modern payloads must
have had another cause. **No fix to the data-check string was needed.**
Pinned as `TestPinnedRealPayload` in `tests/test_miniapp_auth.py`. The
raw capture lives in gitignored `tests/fixtures/real_init_data.txt`
(it is personal data and must not be committed); the test derives all
assertions from the payload itself and skips on machines without the
fixture or the real bot token. The full auth suite and the 143-test
suite pass.
