# Booth Lead Capture Bot — prototype

Telegram bot that turns `badge photo + voice note` in a group chat into a
structured lead row, exportable as CSV for Pipedrive import.

See `../docs/PRD.md` for product rationale and `../docs/technical-design.md` for
architecture.

## Workflow at the booth

1. Rep takes a photo of the visitor's badge and sends it to the team group chat.
2. Rep records a voice note in the same thread (replying to the badge photo):
   *"Hot lead, wants a demo Friday, asked about self-hosting."*
3. Bot confirms in chat: `✅ #42 — Ada Lovelace @ Analytical Engines — hot`
4. At end of day, event lead runs `/export` and uploads the CSV to Pipedrive.

## Setup

```sh
cd prototype
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in the 5 required vars
```

### Creating the Telegram bot

1. Message `@BotFather` on Telegram, run `/newbot`, follow prompts. Copy the
   token into `TELEGRAM_BOT_TOKEN`.
2. In `@BotFather`, run `/setprivacy` → select your bot → **Disable**. Without
   this, bots in groups only see messages that mention them, and we need to
   see all photos and voices.
3. Create the event group chat, add your bot to it.
4. Send any message to the group, then in a browser:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` — find the negative
   chat id. Paste into `TELEGRAM_GROUP_CHAT_ID`.

### Running

```sh
set -a && source .env && set +a
python -m prototype.bot
```

The bot runs a long-poll loop — no inbound ports needed, fine on a hotel Wi-Fi
booth laptop.

## Commands (in the event group chat)

| Command | What it does |
|---|---|
| *(photo)* | Register a pending badge; bot prompts for a voice note |
| *(voice)* | Pair with a pending badge, extract fields, log the lead |
| `/list` | Show the last 10 captures |
| `/export` | Send a CSV of all active captures for this event |
| `/delete <id>` | Soft-delete a lead (doesn't appear in exports) |
| `/help` | Usage |

## Pairing rules

A voice note pairs to a pending badge photo if:

1. **The voice replies to the badge photo** (strongest — always use this in practice), or
2. **Same sender, within `PAIRING_WINDOW_SECONDS` (default 3 min)** — takes the most recent pending photo from that sender.

A badge photo with no voice within the window is still saved as a lead, with
`needs_voice=true` set, and the rep is nudged in chat.

## Running tests

```sh
cd prototype
pip install pytest
python -m pytest tests/
```

Tests cover pure logic only (pairing rules, CSV export, storage CRUD). Bot
handlers require Telegram/Anthropic/Whisper mocks and are left to smoke-testing
at a staging event.

## Costs

Rough per-lead cost: ~$0.017 (Claude Opus 4.6 ~$0.015 + Whisper ~$0.002).
A busy booth day at 100 leads ≈ $1.70 in API spend.

## Data retention

- Badge images and voice files are cached under `DATA_DIR` (default `./data/media/`).
  Delete the directory after importing to Pipedrive.
- SQLite (`leads.db`) is the source of truth until export. Delete it after the
  event if you don't want to retain raw transcripts.
- Env vars (API keys, tokens) are never logged. Logs are at INFO by default —
  set `PYTHONLOGLEVEL=DEBUG` only when debugging and rotate logs after.
