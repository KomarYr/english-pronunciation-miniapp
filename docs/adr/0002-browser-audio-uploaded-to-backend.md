# Browser-recorded audio is uploaded to the backend

The Mini App records audio in the browser and POSTs it to the backend,
which converts it and feeds it through the existing Python
`azure_assessment` path. The Azure Speech SDK stays server-side, and the
typed error contract (`NoSpeechDetected`, `AssessmentCanceled`,
`QuotaExceededError`) plus the quota ledger keep working unchanged.

## Considered options

- **Speech SDK in the browser (rejected)**: lower latency, but the Azure
  key would need a token-exchange endpoint anyway, and the
  assessment/quota contract would be duplicated in TypeScript — two
  implementations of one policy.

## Consequences

- Assessment requests are only as good as the browser recording; sample
  rates and container formats vary, so backend conversion (ffmpeg) must
  accept what MediaRecorder produces, not just Telegram's OGG/Opus.
