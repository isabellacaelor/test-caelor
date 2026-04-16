"""Pure pairing rules — no I/O. Takes a decision given known state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .storage import PendingPhoto


@dataclass
class PairingDecision:
    photo: Optional[PendingPhoto]
    reason: str  # "reply", "same_sender", "none"


def pair_voice(
    *,
    voice_chat_id: int,
    voice_sender_user_id: int,
    voice_sent_at: datetime,
    voice_reply_to_message_id: Optional[int],
    reply_target_photo: Optional[PendingPhoto],
    candidate_photo: Optional[PendingPhoto],
    pairing_window_seconds: int,
) -> PairingDecision:
    """Decide which (if any) pending photo a voice note pairs to.

    Rules, in order:
      1. If the voice message is a reply to a message that matches a pending
         photo → pair to it (strongest signal).
      2. Else, if the candidate (most recent pending photo from the same sender
         in the same chat) exists and was sent within the pairing window → pair.
      3. Else → no pairing.

    `reply_target_photo` should be whatever the caller looked up using
    `voice_reply_to_message_id`; it may be None if the reply target wasn't a
    pending photo (e.g., the rep replied to a different message).
    """
    # Rule 1: explicit reply
    if voice_reply_to_message_id is not None and reply_target_photo is not None:
        if reply_target_photo.chat_id == voice_chat_id:
            return PairingDecision(photo=reply_target_photo, reason="reply")

    # Rule 2: same sender, within window
    if candidate_photo is not None and candidate_photo.chat_id == voice_chat_id:
        if candidate_photo.sender_user_id == voice_sender_user_id:
            window = timedelta(seconds=pairing_window_seconds)
            if voice_sent_at - candidate_photo.created_at <= window:
                return PairingDecision(photo=candidate_photo, reason="same_sender")

    return PairingDecision(photo=None, reason="none")
