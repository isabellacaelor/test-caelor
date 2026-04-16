# Booth Lead Capture — Telegram + AI

A faster way for an event booth team to capture leads: snap the badge, record
a voice note, get a Pipedrive-ready CSV at the end of the day. No typing on
phones in a loud booth.

## Repo layout

```
docs/
  PRD.md                  Product rationale, user flows, acceptance criteria
  technical-design.md     Architecture, data flow, APIs, failure modes, privacy

prototype/
  bot.py                  Telegram bot entry point
  ai.py                   Claude vision + structured extraction
  transcribe.py           OpenAI Whisper wrapper
  storage.py              SQLite — pending photos + leads
  pairing.py              Pure rules: photo ↔ voice pairing
  export.py               CSV builder (Pipedrive-ready)
  models.py               Pydantic schema for Claude structured output
  config.py               Env-var loading, validation
  tests/                  Pairing rules, CSV, storage CRUD
  README.md               Setup and usage
  .env.example            Required env vars
  requirements.txt
```

## Read this first

1. `docs/PRD.md` — why, for whom, what success looks like, open questions for
   legal/ops.
2. `docs/technical-design.md` — how it's built, where it can fail, what's
   deliberately out of scope.
3. `prototype/README.md` — how to run it.

## Development branch

This feature is being developed on `claude/mobile-lead-capture-VzMlw`.
