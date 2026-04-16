# PRD — Booth Lead Capture via Group Chat + AI

Status: Draft
Owner: TBD
Last updated: 2026-04-16

## 1. Problem

At events, the team currently captures leads by scanning badges with a dedicated scanner
app. The scanner works, but **typing notes on a phone in a loud booth is slow and error-prone**,
so notes get skipped or truncated. When the team sits down to follow up in Pipedrive later,
they can't remember who was hot vs. warm, what was said, or what the next step should be.

The friction is on **note capture**, not the badge scan itself.

## 2. Goal

Replace (or supplement) the scanner-with-typing workflow with a faster capture loop that
keeps the team's hands free and their attention on the booth visitor, then uses AI to
produce a structured lead list ready for Pipedrive follow-up.

**Success criteria:**

- Time-to-capture per lead ≤ 20 seconds of the booth rep's attention.
- ≥ 95% of captured leads have all five fields populated (Name, Email, Company, Notes, Lead Temp).
- Zero post-event retyping: CSV imports into Pipedrive without manual editing beyond assigning owners.
- Team reports capture feels "easier than typing" in post-event retro.

**Non-goals (v1):**

- Direct Pipedrive API integration (see §9 Future).
- Real-time Pipedrive dedupe.
- Capturing leads outside the booth (street scans, referrals).
- Multi-language badge OCR beyond Latin-script languages.

## 3. Users

| Persona | Role at booth | Needs from the system |
|---|---|---|
| **Booth rep** (2–4 per event) | Greets visitors, does quick qualification, moves on | Minimize fiddly phone work; trust that what they said was captured |
| **Event lead** (1 per event) | Owns post-event follow-up, imports to Pipedrive | Complete, clean CSV; an audit trail if something looks wrong |
| **Sales / AE** | Owns deal after handoff | Accurate notes and lead temperature so their first outreach is on-target |

## 4. Workflow (v1)

1. Booth rep takes a photo of the visitor's badge.
2. They send the photo to the team's **Telegram group chat** dedicated to this event.
3. They record a **voice note in the same thread**, posted as a reply to the badge photo.
   Voice note covers:
   - Lead temperature (hot / warm / cold)
   - Anything worth remembering — product interest, objections, next step
4. A bot watching the chat:
   - Downloads the badge photo and runs OCR + field extraction (Claude vision).
   - Transcribes the voice note.
   - Extracts structured fields from the transcript.
   - Stores the lead.
   - Reacts to the badge photo with ✅ and posts a one-line confirmation: `Ada Lovelace @ Analytical Engines Ltd — warm, logged`.
5. Event lead runs `/export` in the group chat at end-of-day. Bot returns a CSV.
6. Event lead uploads the CSV to Pipedrive via the UI's bulk import.
7. Follow-up is sent based on Lead Temperature field.

## 5. Output Schema

One row per lead. Columns are Pipedrive-import-friendly.

| Column | Source | Example |
|---|---|---|
| `Name` | Badge OCR | `Ada Lovelace` |
| `Email` | Badge OCR | `ada@analyticalengines.com` |
| `Company` | Badge OCR | `Analytical Engines Ltd` |
| `Title` | Badge OCR (if present) | `Head of Research` |
| `Notes` | Voice transcript (verbatim) + AI summary | `"Interested in the Python SDK, asked about self-hosting. Wants a demo next week."` |
| `Lead Temp` | AI classifier from voice | `hot` \| `warm` \| `cold` |
| `Next Step` | AI extracted from voice | `Send demo link` \| `Follow up in 2 weeks` \| `None` |
| `Captured At` | Bot timestamp | `2026-04-16T14:32:11Z` |
| `Captured By` | Telegram sender | `@alice` |
| `Event` | Config (per-event) | `KubeCon EU 2026` |
| `Needs Review` | Bot flag | `true` if OCR confidence low or voice missing |

## 6. Functional requirements

### Must

- F1. Bot joins a Telegram group and listens only to that group.
- F2. Bot downloads image messages sent to the group and runs badge extraction.
- F3. Bot downloads voice messages sent to the group and transcribes them.
- F4. Bot pairs a voice note to a badge photo when the voice is a **reply to the badge photo**.
- F5. Bot falls back to pairing by `(same sender, within N minutes of the badge photo)` when no reply link exists. N configurable, default 3 minutes.
- F6. Bot stores structured leads in a local database.
- F7. Bot reacts to each badge photo after capture so the team knows it worked.
- F8. `/export` command returns a CSV of all leads captured for the current event.
- F9. `/list` command shows the last 10 captures with per-row flags.
- F10. `/delete <id>` command removes a capture (for bad scans, duplicates, accidental personal photos).

### Should

- S1. `Needs Review` flag surfaces in the CSV so event lead can spot-check before uploading.
- S2. If only a photo arrives with no voice note within the pairing window, the lead is still stored, marked `needs_voice=true`, and the bot prompts the sender: `@alice — voice note?`.
- S3. Bot preserves the original badge photo file ID for audit (Telegram retains media for the chat).

### Could

- C1. Multiple concurrent events — bot scopes leads by chat ID or slash command.
- C2. Per-rep summary at end of day (`@alice captured 23, @bob captured 17`).
- C3. Dedup detection — warn if a badge matches an already-captured name+email.

### Won't (v1)

- W1. Direct Pipedrive API push.
- W2. WhatsApp / Signal support.
- W3. Multi-modal extraction from freeform photos (just a business card, handwritten notes, etc.).
- W4. Web dashboard.

## 7. Acceptance criteria

- **A1.** Given a badge photo and a voice note replying to it, the bot produces a row in the CSV with all Must-have fields populated within 30 seconds of the voice note.
- **A2.** Given a badge photo with no follow-up voice within 3 minutes, the lead is still captured (fields from OCR only, `Needs Review=true`, `needs_voice=true`) and the sender is nudged in chat.
- **A3.** Given OCR fails to extract an email, `Email` is blank, `Needs Review=true`, and the raw badge photo file ID is retained in storage.
- **A4.** Given the transcript says "definitely hot, wants a demo Friday", `Lead Temp=hot` and `Next Step` contains a reference to the demo.
- **A5.** Given two reps post photos and voices interleaved, each pair routes correctly (no cross-contamination). Verified via same-sender pairing rule.
- **A6.** The CSV output opens cleanly in Excel, Google Sheets, and Pipedrive's import flow.

## 8. Open questions

- **OQ1.** **Privacy / consent.** Badges typically include first name, last name, company, email. Is a group chat + AI transcription pipeline compatible with the privacy notices we display at the booth? Needs legal/ops sign-off before we use this at a live event. Specifically: GDPR lawful basis (legitimate interest?), retention period for photos in Telegram, retention period for voice notes. Draft privacy language needs to cover "we may record and transcribe a voice note about you".
- **OQ2.** **Hosting.** Where does the bot process live during the event? Laptop at booth (simplest), small cloud VM, or ephemeral Docker on someone's machine? Affects reliability.
- **OQ3.** **API key ownership.** Who holds the Anthropic + OpenAI (Whisper) API keys? Per-event or per-team? Spend guardrails?
- **OQ4.** **Voice transcription provider.** OpenAI Whisper API is the default assumption. Alternatives: Deepgram, local whisper.cpp. Local avoids sending voice to a third party but needs more setup. See technical design doc.
- **OQ5.** **Failure mode if bot goes down mid-event.** Does the team fall back to the existing scanner, or do messages queue and replay on reconnect?
- **OQ6.** **CSV field mapping to Pipedrive.** Which Pipedrive custom fields do `Lead Temp` and `Next Step` map to? Needs a pass from the event lead before the next event.
- **OQ7.** **Multi-event isolation.** One bot instance per event (simplest), or one bot with event selection via command? Affects setup overhead.

## 9. Future (post-v1)

- Direct Pipedrive API integration (skip CSV).
- Real-time dedup against existing Pipedrive contacts.
- WhatsApp Business API support for teams that live in WhatsApp.
- Owner routing — auto-assign leads to the AE who owns the territory based on company country.
- Follow-up email draft generation per lead temperature.
- Per-rep leaderboards.

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Badge OCR misreads email address (one character off) | Medium | High — bad email kills the lead | `Needs Review` flag when confidence is low; event lead spot-checks before Pipedrive upload |
| Voice note captures sensitive info (visitor's salary, a complaint, etc.) stored in chat indefinitely | Medium | Medium — compliance | Document retention; `/delete` command; delete Telegram chat after import |
| Bot crashes mid-event | Low | High — rep thinks capture worked, it didn't | Bot reacts with ✅ *after* successful storage, not on receipt; absence of reaction = try again |
| API rate limits (Anthropic, Whisper) during a busy booth hour | Low | Medium — delayed captures | Retry with backoff; SDK handles this; degrade gracefully by queuing |
| Team member posts unrelated photos to the event group | Medium | Low — noise | Bot only processes images; unrelated images get flagged as `Needs Review`, easy to `/delete` |
