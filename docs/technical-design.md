# Technical Design — Booth Lead Capture Bot

Status: Draft
Owner: TBD
Last updated: 2026-04-16

This doc covers the implementation of the workflow specified in `PRD.md`.

## 1. Architecture

```
┌─────────────────────────┐
│ Telegram group chat     │
│  - badge photos         │
│  - voice notes          │
│  - /export, /list, etc. │
└───────────┬─────────────┘
            │  Telegram Bot API (long polling)
            ▼
┌─────────────────────────────────────────────────┐
│  bot.py  (python-telegram-bot)                  │
│                                                 │
│  ┌───────────┐   ┌───────────┐   ┌───────────┐  │
│  │ handlers  │──▶│  pairer   │──▶│ extractor │  │
│  │ (photo,   │   │ (photo↔   │   │ (Claude   │  │
│  │  voice,   │   │  voice by │   │  vision + │  │
│  │  command) │   │  reply /  │   │  parse)   │  │
│  └───────────┘   │  sender+  │   └─────┬─────┘  │
│                  │  time)    │         │        │
│                  └───────────┘         ▼        │
│                                ┌───────────────┐│
│                                │   storage     ││
│                                │   (SQLite)    ││
│                                └───────┬───────┘│
│                                        │        │
│                                        ▼        │
│                                 ┌──────────┐    │
│                                 │ export.py│    │
│                                 │   → CSV  │    │
│                                 └──────────┘    │
└──────────────┬──────────────────────────────────┘
               │
   ┌───────────┴───────────┐
   ▼                       ▼
┌──────────┐         ┌──────────────┐
│ Anthropic│         │OpenAI Whisper│
│ Claude   │         │ (audio STT)  │
│ (vision) │         │              │
└──────────┘         └──────────────┘
```

**Runtime:** single Python process. Long-polls Telegram. Single-box deployment (laptop, laptop-in-a-backpack, or a small VM).

**No server framework, no webhook infrastructure.** Long polling avoids exposing a public endpoint, which matters because the bot is likely to run from a booth laptop over hotel Wi-Fi.

## 2. Component responsibilities

### `bot.py`
Entry point. Wires `python-telegram-bot` handlers to the pipeline. Owns the event loop.

Handlers:
- `photo_handler` — on `PHOTO` message from the configured group chat, download the image, store a `PendingPhoto` record, start a pairing timer.
- `voice_handler` — on `VOICE` message, transcribe it, try to pair it to a pending photo.
- `export_command` — `/export` → build CSV → send as document to the chat.
- `list_command` — `/list` → last 10 captures as a table.
- `delete_command` — `/delete <id>` → mark lead deleted.
- `start_command` / `help_command` — onboarding.

### `ai.py`
Calls Claude with the badge image and the voice transcript. Returns a `Lead` via structured outputs (Pydantic model). One call per captured pair. Prompt-caches the system prompt.

### `transcribe.py`
Calls OpenAI Whisper API with the voice file. Returns a transcript string. Pluggable — interface lets us swap in local `faster-whisper` later.

### `pairing.py`
Stateless rules engine. Given a new photo or voice event and the set of pending photos, returns a pairing decision:
- Voice is a direct reply to a badge photo → pair.
- Voice sender matches a pending photo's sender, within pairing window → pair to the newest such photo.
- Else → orphan. If photo, leave pending. If voice, log and ignore.

After pairing window expires (background sweep), unpaired photos are promoted to `needs_voice=true` leads and the sender is nudged.

### `storage.py`
SQLite via `sqlite3` stdlib (no ORM). Single file `leads.db`. Schemas:

- `pending_photos(id, chat_id, message_id, sender_user_id, sender_username, file_id, local_path, created_at)`
- `leads(id, chat_id, badge_message_id, voice_message_id, sender_username, name, email, company, title, notes, transcript, lead_temp, next_step, captured_at, event, needs_review, needs_voice, deleted_at)`

`deleted_at` is a soft delete — preserves audit trail, excluded from export.

### `export.py`
Builds CSV with the columns from PRD §5. Uses `csv.DictWriter`. Output is UTF-8 with a BOM (Excel-friendly). Column order is explicit — matches PRD schema.

### `config.py`
Loads env vars via `os.environ`. No framework dependency. Validates required vars at startup and exits with a clear message if missing.

## 3. Data flow: a captured lead

Concrete example of F1–F8 from the PRD.

```
t=0    Alice sends a badge photo to the group.
       │
       ├─ photo_handler downloads the file via bot.get_file(...).download_to_drive()
       ├─ stores row in pending_photos
       └─ starts a 3-minute expiry timer

t+15s  Alice sends a voice note replying to the badge photo.
       │
       ├─ voice_handler downloads the .ogg file
       ├─ transcribe.py sends it to Whisper, gets back:
       │    "Hot lead, Ada from Analytical Engines, wants a demo Friday,
       │     asked about self-hosting, send her the deployment guide."
       ├─ pairing.py sees message has reply_to_message_id pointing at the badge photo
       │    → pair immediately
       ├─ ai.py calls Claude with (badge image, transcript):
       │    system: extraction prompt (cached)
       │    user: [image block, text block with transcript]
       │    output_config: Lead schema
       ├─ Claude returns:
       │    {name: "Ada Lovelace",
       │     email: "ada@analyticalengines.com",
       │     company: "Analytical Engines Ltd",
       │     title: "Head of Research",
       │     notes: "Wants demo Friday; asked about self-hosting; send deployment guide",
       │     lead_temp: "hot",
       │     next_step: "Send demo link and deployment guide before Friday"}
       ├─ storage.py writes to leads
       ├─ bot reacts ✅ to the badge photo
       └─ bot replies: "Ada Lovelace @ Analytical Engines — hot — logged (id 42)"

t+24h  Event lead runs /export. Bot builds CSV of non-deleted leads for this event, sends as document.
```

## 4. Pairing logic in detail

Photos and voice notes arrive as independent messages. The bot has three signals to link them:

1. **Explicit reply.** `message.reply_to_message.message_id` on the voice points at the photo's message ID. Strongest signal. Use first.
2. **Same sender within window.** `voice.from_user.id == photo.from_user.id`, `voice.date - photo.date ≤ pairing_window` (default 3 minutes, configurable). Use second. If multiple pending photos match, take the most recent.
3. **Manual.** `/pair <photo_id> <voice_id>` — admin override. Not in v1.

A photo may be paired with **at most one** voice. Once paired, it moves from `pending_photos` to `leads`. If the sender records a second voice note as a correction, they have to `/delete` the lead and re-post.

Expiry: a background job runs every 60s, finds pending photos where `now - created_at > pairing_window`, and promotes them to `needs_voice=true` leads with OCR-only fields, then nudges the sender.

## 5. AI call details

### Extraction call

One call per paired lead. Model: `claude-opus-4-6` (accuracy matters more than latency; the team is already waiting on a voice note).

- **`system`**: extraction prompt (see below). Marked `cache_control: ephemeral` — same prefix across every lead, so caching kicks in after the first call per run.
- **`messages`**: one user turn with two content blocks:
  - `{type: "image", source: {type: "base64", media_type: "image/jpeg", data: <badge>}}`
  - `{type: "text", text: "Voice transcript:\n\n<transcript>"}`
- **`output_config.format`**: JSON schema derived from a `Lead` Pydantic model, via `client.messages.parse()`.
- **`thinking`**: `{type: "adaptive"}` — let Claude think harder on blurry badges or garbled transcripts.

System prompt outline:
```
You extract structured lead data from a conference badge photo and an optional
voice note transcript recorded by a booth rep.

Extract these fields from the badge photo:
- Full name (exactly as printed)
- Company (the employer, not the conference)
- Email (the attendee's email, not generic like info@)
- Job title (if printed)

Extract these from the transcript:
- lead_temp: hot | warm | cold (default: warm if unclear)
- next_step: one short sentence, or null if none implied
- notes: a one-paragraph summary capturing product interest, objections,
  commitments made, anything worth remembering on first follow-up.

Rules:
- If a field is not present, return null — do not guess.
- Emails: verify the domain looks plausible for the company; if mismatched,
  return both and flag in notes.
- If the image is not a conference badge, set all OCR fields to null and
  set notes to "Image is not a badge".
```

### Transcription call

OpenAI Whisper API (`whisper-1`). One call per voice note. Output: transcript string. No structured output, no post-processing — the structure is added in the Claude extraction step.

**Why not Claude for audio too?** The Anthropic API doesn't offer speech-to-text at the time of writing. The `transcribe.py` module is a thin wrapper so we can swap providers without touching the rest of the code.

### Cost back-of-envelope

Per lead:
- Whisper: ~$0.006/minute; voice notes average 15s → $0.0015
- Claude Opus 4.6 with a ~500-token system prompt (cached after first lead), a ~1500-token badge image, ~100 tokens of transcript, ~200 tokens output → ~$0.015/lead
- Total: ~$0.017/lead. A busy booth day with 100 leads is ~$1.70 in API costs.

## 6. Data model

```sql
CREATE TABLE pending_photos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL,
    message_id      INTEGER NOT NULL,
    sender_user_id  INTEGER NOT NULL,
    sender_username TEXT,
    file_id         TEXT NOT NULL,    -- Telegram file_id (for audit)
    local_path      TEXT NOT NULL,    -- path to downloaded image
    created_at      TEXT NOT NULL,    -- ISO8601 UTC
    UNIQUE(chat_id, message_id)
);

CREATE TABLE leads (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id            INTEGER NOT NULL,
    event              TEXT NOT NULL,
    badge_message_id   INTEGER,
    voice_message_id   INTEGER,
    sender_username    TEXT,
    name               TEXT,
    email              TEXT,
    company            TEXT,
    title              TEXT,
    notes              TEXT,
    transcript         TEXT,
    lead_temp          TEXT,       -- hot | warm | cold | NULL
    next_step          TEXT,
    captured_at        TEXT NOT NULL,
    needs_review       INTEGER NOT NULL DEFAULT 0,
    needs_voice        INTEGER NOT NULL DEFAULT 0,
    deleted_at         TEXT
);

CREATE INDEX idx_leads_chat_event ON leads(chat_id, event) WHERE deleted_at IS NULL;
```

## 7. Config

Env vars:

| Name | Purpose | Required |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot auth | Yes |
| `TELEGRAM_GROUP_CHAT_ID` | The one group the bot listens to | Yes |
| `ANTHROPIC_API_KEY` | Claude vision + extraction | Yes |
| `OPENAI_API_KEY` | Whisper transcription | Yes |
| `EVENT_NAME` | Tagged on every captured lead | Yes |
| `PAIRING_WINDOW_SECONDS` | How long to wait for a voice after a photo | No (default 180) |
| `DATA_DIR` | Where SQLite + downloaded media live | No (default `./data`) |
| `ANTHROPIC_MODEL` | Override model | No (default `claude-opus-4-6`) |

## 8. Failure modes & handling

| Failure | Current behavior | Acceptable? |
|---|---|---|
| Telegram API unreachable | `python-telegram-bot` retries; bot logs and continues | Yes |
| Claude API error | Log, store lead with `needs_review=true`, fields from transcript keyword extraction (fallback) | Yes for v1 |
| Whisper API error | Store lead with `needs_review=true`, `transcript=""`, `notes="[voice transcription failed]"`, still pair | Yes |
| OCR returns nothing usable | Store with blank name/email/company, `needs_review=true` | Yes |
| Two voices reply to one photo | First-wins; second is ignored with a warning reply | Yes |
| Photo sent outside the configured group chat | Ignored | Yes |
| Sender posts non-badge image | Claude returns `notes="Image is not a badge"`, `needs_review=true`, sender notified | Yes |
| Bot restarts mid-event | Pending photos persist in SQLite; pairing windows resume (with clock drift risk — acceptable) | Yes |
| SQLite file corrupted | Manual recovery; leads table has everything needed | Acceptable for v1 |

## 9. Privacy & security

See PRD §8 OQ1. Key points for the implementation:

- **Never log** email addresses, names, or transcript text at INFO level. Use DEBUG only, and document that DEBUG logs should not be kept after the event.
- **Delete downloaded media** from `DATA_DIR` after the lead is captured (keep the Telegram `file_id` reference for re-download if audit needed — Telegram retains chat history).
- **At end of event**: support `/purge` command (admin-only) that deletes the local SQLite file and cached media. Pipedrive (post-import) becomes the system of record.
- **API keys**: env vars only, never committed, never logged. `.env.example` documents shape; `.env` is in `.gitignore`.

## 10. Testing strategy

v1 is a prototype — full test coverage is out of scope, but we target:

- **Unit tests** for pure logic that's easy to regress:
  - `pairing.py` rules — reply beats time, time window, orphan handling
  - `export.py` CSV generation — column order, BOM, escaping of commas in notes
  - `storage.py` basic CRUD
- **Integration tests** skipped for v1 — would need Telegram/Anthropic/Whisper mocks that would be more code than the bot itself. Instead:
- **End-to-end smoke test**: `scripts/smoke.py` that feeds a fixture badge image + fixture transcript directly to `ai.extract_lead()` and prints the result. Run this whenever the prompt or model changes.

## 11. Deployment

v1 runs on a laptop or small VM. Start script: `python -m prototype.bot`.

- Process supervisor: `systemd` unit or just `tmux` at first event.
- Logs: stdout → optional file redirect.
- Restart policy: restart on crash.
- Monitoring: none beyond eyeballing the group chat for missing ✅ reactions.

Post-v1, if this becomes permanent: Docker image, managed container host (Fly.io, Railway), proper logging.

## 12. Open technical questions

- **TQ1.** Does Telegram's `Bot.set_message_reaction` (reactions API) require a premium bot? Fallback is a reply message with `✅ logged`.
- **TQ2.** Should we store the badge image in the DB as a blob, or only the `file_id`? Blob makes auditing easier but bloats the DB and raises retention questions. Recommend `file_id` only.
- **TQ3.** Rate limiting: Telegram throttles bots to ~30 messages/second per group, well above our needs. Anthropic/Whisper rate limits depend on tier — assume fine for v1.
- **TQ4.** Concurrent extraction: should we kick off Claude extraction in the background as soon as a photo arrives (pre-OCR), and only merge in transcript+temp later? Saves 1–2s per lead. Skipped for v1 — adds state-machine complexity.

## 13. Out of scope (explicit)

- Pipedrive API integration (use CSV + manual upload).
- Any web UI.
- Multi-tenant / multi-org support.
- Real-time dedup.
- Handling images other than conference badges.
- Anything on WhatsApp or Signal (see PRD §9).
