"""Pure rules — no fixtures needed beyond constructing PendingPhoto values."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from prototype.pairing import pair_voice
from prototype.storage import PendingPhoto


def _photo(
    *,
    photo_id: int = 1,
    chat_id: int = -100,
    message_id: int = 10,
    sender_user_id: int = 42,
    created_at: datetime | None = None,
) -> PendingPhoto:
    return PendingPhoto(
        id=photo_id,
        chat_id=chat_id,
        message_id=message_id,
        sender_user_id=sender_user_id,
        sender_username="alice",
        file_id="abc",
        local_path="/tmp/badge.jpg",
        created_at=created_at or datetime.now(timezone.utc),
    )


def test_reply_pairing_wins_over_sender_match():
    """An explicit reply link should always win, even if another sender-match exists."""
    now = datetime.now(timezone.utc)
    reply_target = _photo(photo_id=1, message_id=10, sender_user_id=99)
    sender_candidate = _photo(photo_id=2, message_id=11, sender_user_id=42)

    decision = pair_voice(
        voice_chat_id=-100,
        voice_sender_user_id=42,
        voice_sent_at=now,
        voice_reply_to_message_id=10,
        reply_target_photo=reply_target,
        candidate_photo=sender_candidate,
        pairing_window_seconds=180,
    )

    assert decision.photo is reply_target
    assert decision.reason == "reply"


def test_same_sender_within_window():
    now = datetime.now(timezone.utc)
    candidate = _photo(sender_user_id=42, created_at=now - timedelta(seconds=30))

    decision = pair_voice(
        voice_chat_id=-100,
        voice_sender_user_id=42,
        voice_sent_at=now,
        voice_reply_to_message_id=None,
        reply_target_photo=None,
        candidate_photo=candidate,
        pairing_window_seconds=180,
    )

    assert decision.photo is candidate
    assert decision.reason == "same_sender"


def test_same_sender_outside_window():
    now = datetime.now(timezone.utc)
    candidate = _photo(sender_user_id=42, created_at=now - timedelta(seconds=600))

    decision = pair_voice(
        voice_chat_id=-100,
        voice_sender_user_id=42,
        voice_sent_at=now,
        voice_reply_to_message_id=None,
        reply_target_photo=None,
        candidate_photo=candidate,
        pairing_window_seconds=180,
    )

    assert decision.photo is None
    assert decision.reason == "none"


def test_different_sender_does_not_pair_by_time():
    """Two reps interleaving must not cross-contaminate."""
    now = datetime.now(timezone.utc)
    # Photo from user 42, voice from user 99 — must NOT pair even in window.
    candidate = _photo(sender_user_id=42, created_at=now - timedelta(seconds=10))

    decision = pair_voice(
        voice_chat_id=-100,
        voice_sender_user_id=99,
        voice_sent_at=now,
        voice_reply_to_message_id=None,
        reply_target_photo=None,
        candidate_photo=candidate,
        pairing_window_seconds=180,
    )

    # candidate_photo would only be passed if the store found a match for user 99;
    # the bot's lookup is scoped to sender. Simulating: if wrong-sender candidate
    # *were* passed, the function must still refuse.
    assert decision.photo is None


def test_no_candidate_no_reply():
    decision = pair_voice(
        voice_chat_id=-100,
        voice_sender_user_id=42,
        voice_sent_at=datetime.now(timezone.utc),
        voice_reply_to_message_id=None,
        reply_target_photo=None,
        candidate_photo=None,
        pairing_window_seconds=180,
    )

    assert decision.photo is None
    assert decision.reason == "none"


def test_reply_to_different_chat_ignored():
    """A reply target from another chat should not pair (defensive)."""
    now = datetime.now(timezone.utc)
    wrong_chat_photo = _photo(chat_id=-999)

    decision = pair_voice(
        voice_chat_id=-100,
        voice_sender_user_id=42,
        voice_sent_at=now,
        voice_reply_to_message_id=10,
        reply_target_photo=wrong_chat_photo,
        candidate_photo=None,
        pairing_window_seconds=180,
    )

    assert decision.photo is None
