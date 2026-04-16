"""
Booth Lead Capture — Telegram Bot
----------------------------------
Send a badge photo → then a voice note (or text) → bot replies with
structured lead summary and saves to Supabase.

/export  — sends the full leads spreadsheet (.xlsx) in chat
/leads   — shows a count + recent leads
/cancel  — clears your pending session
/manual  — manually enter a lead
"""

import asyncio
import io
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database
import excel_export
import gemini_process

load_dotenv()

BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_ID  = int(os.getenv("TELEGRAM_ALLOWED_GROUP_ID", "0"))
SESSION_TTL = int(os.getenv("SESSION_TIMEOUT_SECONDS", "300"))


# ── Session store ─────────────────────────────────────────────────────────────

@dataclass
class Session:
    user_id:      int
    chat_id:      int
    username:     str
    photo_file_id: Optional[str] = None
    voice_file_id: Optional[str] = None
    text_note:    Optional[str]  = None
    ts:           float = field(default_factory=time.monotonic)

    def ready(self) -> bool:
        return bool(self.photo_file_id and (self.voice_file_id or self.text_note))

    def expired(self) -> bool:
        return (time.monotonic() - self.ts) > SESSION_TTL


_sessions: dict[int, Session] = {}
_lock = asyncio.Lock()


async def _get_or_create(user_id: int, chat_id: int, username: str) -> Session:
    async with _lock:
        s = _sessions.get(user_id)
        if s is None or s.expired():
            s = Session(user_id=user_id, chat_id=chat_id, username=username)
            _sessions[user_id] = s
        return s


async def _consume(user_id: int) -> Optional[Session]:
    async with _lock:
        s = _sessions.get(user_id)
        if s and s.ready():
            del _sessions[user_id]
            return s
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _allowed(update: Update) -> bool:
    if ALLOWED_ID == 0:
        return True
    return update.effective_chat.id == ALLOWED_ID


def _username(update: Update) -> str:
    u = update.effective_user
    return (u.username or u.full_name or str(u.id)) if u else "unknown"


async def _download(file_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": file_id},
        )
        r.raise_for_status()
        path = r.json()["result"]["file_path"]
        dl = await client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}")
        dl.raise_for_status()
        return dl.content


def _format_lead(lead: dict, row_id: Optional[int] = None) -> str:
    temp = lead.get("temperature", "WARM")
    emoji = {"HOT": "🔥", "WARM": "🌤", "COLD": "❄️"}.get(temp, "")
    lines = [f"{emoji} *{lead.get('name', 'Unknown')}*"]
    if lead.get("job_title"):
        lines.append(f"_{lead['job_title']}_")
    if lead.get("company"):
        lines.append(f"🏢 {lead['company']}")
    if lead.get("email"):
        lines.append(f"✉️ `{lead['email']}`")
    if lead.get("phone"):
        lines.append(f"📞 {lead['phone']}")
    lines.append(f"🌡 Temperature: *{temp}*")
    if lead.get("next_step"):
        lines.append(f"➡️ Next: {lead['next_step']}")
    if lead.get("notes"):
        lines.append(f"📝 _{lead['notes']}_")
    if row_id:
        lines.append(f"\n✅ Saved (#{row_id})")
    return "\n".join(lines)


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    await update.message.reply_text(
        "*Lead Capture Bot* 📋\n\n"
        "How to capture a lead:\n"
        "1️⃣ Send a *photo* of the badge\n"
        "2️⃣ Send a *voice note* with temperature + notes\n"
        "   _(or just type your notes as a message)_\n\n"
        "Commands:\n"
        "/leads — see all leads\n"
        "/export — get the Excel spreadsheet\n"
        "/cancel — clear your pending session\n"
        "/manual — type a lead manually",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_leads(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    n = database.count()
    if n == 0:
        await update.message.reply_text("No leads captured yet.")
        return

    leads = database.all_leads()
    lines = [f"*{n} lead{'s' if n != 1 else ''} captured:*\n"]
    for lead in leads[-10:]:  # show last 10
        temp = lead.get("temperature", "WARM")
        emoji = {"HOT": "🔥", "WARM": "🌤", "COLD": "❄️"}.get(temp, "")
        name = lead.get("name", "?")
        company = lead.get("company", "")
        lines.append(f"{emoji} {name}" + (f" · {company}" if company else ""))

    if n > 10:
        lines.append(f"\n_...and {n - 10} more. Use /export to see all._")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    leads = database.all_leads()
    if not leads:
        await update.message.reply_text("No leads yet — nothing to export.")
        return

    msg = await update.message.reply_text("Building spreadsheet...")

    try:
        xlsx_bytes = excel_export.build(leads)
        from datetime import datetime
        filename = f"leads_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.xlsx"
        await update.message.reply_document(
            document=io.BytesIO(xlsx_bytes),
            filename=filename,
            caption=f"📊 *{len(leads)} lead{'s' if len(leads) != 1 else ''}* — {filename}",
            parse_mode=ParseMode.MARKDOWN,
        )
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"Export failed: {e}")


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    async with _lock:
        removed = _sessions.pop(update.effective_user.id, None)
    await update.message.reply_text(
        "Session cleared." if removed else "Nothing to cancel."
    )


async def cmd_manual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /manual Name | Email | Company | HOT/WARM/COLD | Next step | Notes
    """
    if not _allowed(update):
        return
    text = update.message.text.replace("/manual", "").strip()
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 4:
        await update.message.reply_text(
            "Format:\n`/manual Name | Email | Company | HOT/WARM/COLD | Next step | Notes`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    temp = parts[3].upper() if len(parts) > 3 else "WARM"
    if temp not in ("HOT", "WARM", "COLD"):
        temp = "WARM"

    lead = {
        "name":        parts[0],
        "email":       parts[1] if len(parts) > 1 else "",
        "company":     parts[2] if len(parts) > 2 else "",
        "temperature": temp,
        "next_step":   parts[4] if len(parts) > 4 else "",
        "notes":       parts[5] if len(parts) > 5 else "",
        "captured_by": _username(update),
    }

    row_id = database.save(lead)
    await update.message.reply_text(
        _format_lead(lead, row_id),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Message handlers ──────────────────────────────────────────────────────────

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    user = update.effective_user
    file_id = update.message.photo[-1].file_id  # highest resolution

    session = await _get_or_create(user.id, update.effective_chat.id, _username(update))
    session.photo_file_id = file_id

    if session.ready():
        s = await _consume(user.id)
        if s:
            asyncio.create_task(_process(update, s))
    else:
        await update.message.reply_text(
            "✅ Badge photo received.\nNow send a *voice note* or type your notes.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    user = update.effective_user
    file_id = update.message.voice.file_id

    session = await _get_or_create(user.id, update.effective_chat.id, _username(update))
    session.voice_file_id = file_id

    if session.ready():
        s = await _consume(user.id)
        if s:
            asyncio.create_task(_process(update, s))
    else:
        await update.message.reply_text(
            "✅ Voice note received.\nNow send a *photo* of the badge.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Plain text message after a photo — treat it as notes."""
    if not _allowed(update):
        return
    # Ignore commands
    if update.message.text and update.message.text.startswith("/"):
        return

    user = update.effective_user
    async with _lock:
        session = _sessions.get(user.id)

    if session is None or not session.photo_file_id:
        # No active session — ignore the text
        return

    session.text_note = update.message.text

    if session.ready():
        s = await _consume(user.id)
        if s:
            asyncio.create_task(_process(update, s))


# ── Core processing pipeline ──────────────────────────────────────────────────

async def _process(update: Update, session: Session):
    chat_id = session.chat_id
    bot = update.get_bot()

    msg = await bot.send_message(chat_id, "⏳ Processing lead...")

    try:
        image_bytes = await _download(session.photo_file_id)

        if session.voice_file_id:
            voice_or_text = await _download(session.voice_file_id)
        else:
            voice_or_text = session.text_note or ""

        lead = await gemini_process.process(image_bytes, voice_or_text)
        lead["captured_by"] = session.username

        row_id = database.save(lead)

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg.message_id,
            text=_format_lead(lead, row_id),
            parse_mode=ParseMode.MARKDOWN,
        )

    except Exception as exc:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg.message_id,
            text=f"❌ Failed: {exc}\n\nUse /manual to enter the lead manually.",
        )


# ── Background cleanup ────────────────────────────────────────────────────────

async def _cleanup_loop():
    while True:
        await asyncio.sleep(60)
        async with _lock:
            expired = [uid for uid, s in _sessions.items() if s.expired()]
            for uid in expired:
                del _sessions[uid]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set in .env")
    if not os.getenv("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY is not set in .env")

    database.init()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("leads",   cmd_leads))
    app.add_handler(CommandHandler("export",  cmd_export))
    app.add_handler(CommandHandler("cancel",  cmd_cancel))
    app.add_handler(CommandHandler("manual",  cmd_manual))

    app.add_handler(MessageHandler(filters.PHOTO,          handle_photo))
    app.add_handler(MessageHandler(filters.VOICE,          handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Start background cleanup
    app.post_init = lambda _: asyncio.ensure_future(_cleanup_loop())

    print("Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
