"""Telegram bot — message routing and orchestration.

Listens to a single configured group chat. Handles badge photos, voice notes,
and admin commands. Pairs photo + voice, runs extraction, stores leads, and
acknowledges the rep with a reply.

Run with:
    python -m prototype.bot
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .ai import LeadExtractor
from .config import Config, load_config
from .export import build_csv
from .models import ExtractedLead
from .pairing import pair_voice
from .storage import PendingPhoto, Store
from .transcribe import WhisperTranscriber

logger = logging.getLogger(__name__)


class BotRuntime:
    """Holds shared dependencies so handlers can reach them without globals."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.store = Store(config.db_path)
        self.extractor = LeadExtractor(
            api_key=config.anthropic_api_key, model=config.anthropic_model
        )
        self.transcriber = WhisperTranscriber(api_key=config.openai_api_key)


# ---------- handlers ----------


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    message = update.effective_message
    if message is None or message.chat.id != runtime.config.telegram_group_chat_id:
        return
    if not message.photo:
        return

    # Largest resolution Telegram served us.
    photo = message.photo[-1]
    user = message.from_user

    local_path = runtime.config.media_dir / f"badge_{message.chat.id}_{message.message_id}.jpg"
    file = await photo.get_file()
    await file.download_to_drive(custom_path=str(local_path))

    pending = runtime.store.add_pending_photo(
        chat_id=message.chat.id,
        message_id=message.message_id,
        sender_user_id=user.id if user else 0,
        sender_username=user.username if user else None,
        file_id=photo.file_id,
        local_path=str(local_path),
    )
    logger.info("photo pending id=%s from @%s", pending.id, pending.sender_username)

    # Nudge the rep.
    try:
        await message.reply_text(
            "Got the badge. Reply with a voice note "
            f"(hot/warm + notes) within {runtime.config.pairing_window_seconds // 60} min.",
            quote=True,
        )
    except Exception:
        logger.exception("failed to reply to photo message")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    message = update.effective_message
    if message is None or message.chat.id != runtime.config.telegram_group_chat_id:
        return
    voice = message.voice or message.audio
    if voice is None:
        return

    user = message.from_user
    voice_sent_at = message.date or datetime.now(timezone.utc)

    reply_to_message_id = (
        message.reply_to_message.message_id if message.reply_to_message else None
    )
    reply_target_photo: Optional[PendingPhoto] = None
    if reply_to_message_id is not None:
        reply_target_photo = runtime.store.get_pending_photo_by_message(
            chat_id=message.chat.id, message_id=reply_to_message_id
        )

    candidate_photo = runtime.store.find_pending_photo_for_sender(
        chat_id=message.chat.id,
        sender_user_id=user.id if user else 0,
    )

    decision = pair_voice(
        voice_chat_id=message.chat.id,
        voice_sender_user_id=user.id if user else 0,
        voice_sent_at=voice_sent_at,
        voice_reply_to_message_id=reply_to_message_id,
        reply_target_photo=reply_target_photo,
        candidate_photo=candidate_photo,
        pairing_window_seconds=runtime.config.pairing_window_seconds,
    )

    if decision.photo is None:
        logger.info("voice had no pairing candidate; ignoring")
        try:
            await message.reply_text(
                "Voice note with no matching badge photo. "
                "Send the badge first, then reply with voice.",
                quote=True,
            )
        except Exception:
            logger.exception("failed to reply to orphan voice")
        return

    # Download and transcribe.
    voice_path = runtime.config.media_dir / f"voice_{message.chat.id}_{message.message_id}.ogg"
    try:
        voice_file = await voice.get_file()
        await voice_file.download_to_drive(custom_path=str(voice_path))
        transcript = await asyncio.to_thread(runtime.transcriber.transcribe, voice_path)
    except Exception:
        logger.exception("voice download/transcribe failed")
        transcript = ""

    # Extract.
    badge_path = Path(decision.photo.local_path)
    extracted: Optional[ExtractedLead] = None
    try:
        extracted = await asyncio.to_thread(
            runtime.extractor.extract,
            badge_image_path=badge_path,
            transcript=transcript or None,
        )
    except Exception:
        logger.exception("lead extraction failed")

    lead_id = _store_lead_from_pairing(
        runtime=runtime,
        photo=decision.photo,
        voice_message_id=message.message_id,
        sender_username=(user.username if user else None),
        transcript=transcript,
        extracted=extracted,
        needs_voice=False,
    )

    # Confirm in chat, replying to the badge photo so it sits in context.
    try:
        await context.bot.send_message(
            chat_id=message.chat.id,
            text=_confirmation_text(lead_id, extracted, decision.reason),
            reply_to_message_id=decision.photo.message_id,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception("failed to send confirmation")


async def handle_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    message = update.effective_message
    if message is None or message.chat.id != runtime.config.telegram_group_chat_id:
        return

    leads = runtime.store.list_leads_for_export(
        chat_id=message.chat.id, event=runtime.config.event_name
    )
    if not leads:
        await message.reply_text("No leads captured yet for this event.")
        return

    data = build_csv(leads)
    filename = f"leads-{_slug(runtime.config.event_name)}-{_today_iso()}.csv"

    await context.bot.send_document(
        chat_id=message.chat.id,
        document=data,
        filename=filename,
        caption=f"{len(leads)} lead{'s' if len(leads) != 1 else ''} exported.",
    )


async def handle_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    message = update.effective_message
    if message is None or message.chat.id != runtime.config.telegram_group_chat_id:
        return

    leads = runtime.store.list_recent_leads(
        chat_id=message.chat.id, event=runtime.config.event_name, limit=10
    )
    if not leads:
        await message.reply_text("No leads yet.")
        return

    lines = ["<b>Last 10 leads:</b>"]
    for lead in leads:
        name = lead.name or "(name?)"
        company = lead.company or "(company?)"
        temp = lead.lead_temp or "?"
        flags = []
        if lead.needs_review:
            flags.append("review")
        if lead.needs_voice:
            flags.append("no voice")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"#{lead.id} — {name} @ {company} — {temp}{flag_str}")
    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    message = update.effective_message
    if message is None or message.chat.id != runtime.config.telegram_group_chat_id:
        return
    if not context.args or not context.args[0].isdigit():
        await message.reply_text("Usage: /delete <lead_id>")
        return

    lead_id = int(context.args[0])
    ok = runtime.store.soft_delete_lead(lead_id)
    await message.reply_text(
        f"Deleted lead #{lead_id}." if ok else f"No active lead with id {lead_id}."
    )


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Booth Lead Capture\n\n"
        "1. Send a photo of the visitor's badge.\n"
        "2. Reply to it with a voice note (hot/warm + notes).\n"
        "3. I'll log the lead and confirm.\n\n"
        "Commands:\n"
        "  /list — last 10 captures\n"
        "  /export — CSV of all captures for this event\n"
        "  /delete <id> — remove a lead\n"
        "  /help — this message"
    )
    if update.effective_message:
        await update.effective_message.reply_text(text)


# ---------- background sweep for orphaned photos ----------


async def sweep_expired_photos(context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=runtime.config.pairing_window_seconds
    )
    expired = runtime.store.list_expired_pending_photos(older_than=cutoff)
    for photo in expired:
        if photo.chat_id != runtime.config.telegram_group_chat_id:
            continue
        logger.info("expiring pending photo id=%s (no voice)", photo.id)

        extracted: Optional[ExtractedLead] = None
        try:
            extracted = await asyncio.to_thread(
                runtime.extractor.extract,
                badge_image_path=Path(photo.local_path),
                transcript=None,
            )
        except Exception:
            logger.exception("OCR-only extraction failed on expired photo")

        lead_id = _store_lead_from_pairing(
            runtime=runtime,
            photo=photo,
            voice_message_id=None,
            sender_username=photo.sender_username,
            transcript=None,
            extracted=extracted,
            needs_voice=True,
        )

        try:
            nudge = (
                f"No voice note yet for that badge — saved lead #{lead_id} with "
                "`needs_voice` flagged. Reply here with `/delete "
                f"{lead_id}` and re-post if you need to redo it."
            )
            await context.bot.send_message(
                chat_id=photo.chat_id,
                text=nudge,
                reply_to_message_id=photo.message_id,
            )
        except Exception:
            logger.exception("failed to nudge for expired photo")


# ---------- helpers ----------


def _store_lead_from_pairing(
    *,
    runtime: BotRuntime,
    photo: PendingPhoto,
    voice_message_id: Optional[int],
    sender_username: Optional[str],
    transcript: Optional[str],
    extracted: Optional[ExtractedLead],
    needs_voice: bool,
) -> int:
    """Persist a lead row, then remove the pending photo. Returns lead id."""
    if extracted is not None:
        needs_review = extracted.confidence_low or not extracted.is_badge
        name = extracted.name
        email = extracted.email
        company = extracted.company
        title = extracted.title
        notes = extracted.notes
        lead_temp = extracted.lead_temp
        next_step = extracted.next_step
    else:
        # Extraction failed — store whatever we have, flag for review.
        needs_review = True
        name = email = company = title = None
        notes = transcript or ""
        lead_temp = None
        next_step = None

    lead_id = runtime.store.insert_lead(
        chat_id=photo.chat_id,
        event=runtime.config.event_name,
        badge_message_id=photo.message_id,
        voice_message_id=voice_message_id,
        sender_username=sender_username or photo.sender_username,
        name=name,
        email=email,
        company=company,
        title=title,
        notes=notes,
        transcript=transcript,
        lead_temp=lead_temp,
        next_step=next_step,
        needs_review=needs_review,
        needs_voice=needs_voice,
    )
    runtime.store.remove_pending_photo(photo.id)
    return lead_id


def _confirmation_text(
    lead_id: int, extracted: Optional[ExtractedLead], pairing_reason: str
) -> str:
    if extracted is None:
        return (
            f"Logged #{lead_id} but extraction failed — please check with /list and "
            "edit in Pipedrive."
        )
    name = extracted.name or "(name?)"
    company = extracted.company or "(company?)"
    temp = extracted.lead_temp or "?"
    flag = " <i>(needs review)</i>" if extracted.confidence_low or not extracted.is_badge else ""
    return (
        f"✅ <b>#{lead_id}</b> — {name} @ {company} — <b>{temp}</b>{flag}"
        f"\n<i>paired via {pairing_reason}</i>"
    )


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s).strip("-").lower() or "event"


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ---------- entry ----------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # python-telegram-bot is chatty at INFO; quiet it down.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)

    config = load_config()
    runtime = BotRuntime(config)

    app = Application.builder().token(config.telegram_bot_token).build()
    app.bot_data["runtime"] = runtime

    group_filter = filters.Chat(chat_id=config.telegram_group_chat_id)

    app.add_handler(MessageHandler(filters.PHOTO & group_filter, handle_photo))
    app.add_handler(
        MessageHandler((filters.VOICE | filters.AUDIO) & group_filter, handle_voice)
    )
    app.add_handler(CommandHandler("export", handle_export, filters=group_filter))
    app.add_handler(CommandHandler("list", handle_list, filters=group_filter))
    app.add_handler(CommandHandler("delete", handle_delete, filters=group_filter))
    app.add_handler(CommandHandler(["help", "start"], handle_help))

    # Sweep orphaned photos every 30 seconds.
    if app.job_queue is not None:
        app.job_queue.run_repeating(sweep_expired_photos, interval=30, first=30)
    else:
        logger.warning(
            "job_queue unavailable — pending photos will not auto-expire. "
            "Install python-telegram-bot[job-queue]."
        )

    logger.info(
        "starting bot for event=%r on chat_id=%s (model=%s)",
        config.event_name,
        config.telegram_group_chat_id,
        config.anthropic_model,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
