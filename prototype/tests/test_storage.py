"""Storage CRUD — uses a per-test temp SQLite file."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from prototype.storage import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def test_add_and_find_pending_photo(store: Store):
    photo = store.add_pending_photo(
        chat_id=-100,
        message_id=10,
        sender_user_id=42,
        sender_username="alice",
        file_id="file_abc",
        local_path="/tmp/badge.jpg",
    )
    assert photo.id > 0

    found = store.get_pending_photo_by_message(chat_id=-100, message_id=10)
    assert found is not None
    assert found.sender_username == "alice"


def test_find_pending_photo_for_sender_returns_most_recent(store: Store):
    store.add_pending_photo(
        chat_id=-100,
        message_id=10,
        sender_user_id=42,
        sender_username="alice",
        file_id="f1",
        local_path="/tmp/1.jpg",
    )
    latest = store.add_pending_photo(
        chat_id=-100,
        message_id=11,
        sender_user_id=42,
        sender_username="alice",
        file_id="f2",
        local_path="/tmp/2.jpg",
    )
    found = store.find_pending_photo_for_sender(chat_id=-100, sender_user_id=42)
    assert found is not None
    assert found.id == latest.id


def test_remove_pending_photo(store: Store):
    photo = store.add_pending_photo(
        chat_id=-100,
        message_id=10,
        sender_user_id=42,
        sender_username="alice",
        file_id="f1",
        local_path="/tmp/1.jpg",
    )
    store.remove_pending_photo(photo.id)
    assert store.get_pending_photo_by_message(chat_id=-100, message_id=10) is None


def test_insert_and_list_leads(store: Store):
    lead_id = store.insert_lead(
        chat_id=-100,
        event="TestCon",
        badge_message_id=10,
        voice_message_id=11,
        sender_username="alice",
        name="Ada Lovelace",
        email="ada@example.com",
        company="Analytical Engines",
        title="Head of Research",
        notes="hot lead",
        transcript="hot lead wants demo",
        lead_temp="hot",
        next_step="demo friday",
        needs_review=False,
        needs_voice=False,
    )
    assert lead_id > 0

    leads = store.list_leads_for_export(chat_id=-100, event="TestCon")
    assert len(leads) == 1
    assert leads[0].name == "Ada Lovelace"
    assert leads[0].lead_temp == "hot"


def test_soft_delete_excludes_from_export(store: Store):
    lead_id = store.insert_lead(
        chat_id=-100,
        event="TestCon",
        badge_message_id=10,
        voice_message_id=11,
        sender_username="alice",
        name="Ada",
        email=None,
        company=None,
        title=None,
        notes="",
        transcript=None,
        lead_temp=None,
        next_step=None,
        needs_review=False,
        needs_voice=False,
    )
    assert store.soft_delete_lead(lead_id) is True
    assert store.list_leads_for_export(chat_id=-100, event="TestCon") == []
    # Second delete is a no-op and returns False.
    assert store.soft_delete_lead(lead_id) is False


def test_event_scoping(store: Store):
    store.insert_lead(
        chat_id=-100, event="EventA", badge_message_id=1, voice_message_id=None,
        sender_username="a", name="A", email=None, company=None, title=None,
        notes="", transcript=None, lead_temp=None, next_step=None,
        needs_review=False, needs_voice=False,
    )
    store.insert_lead(
        chat_id=-100, event="EventB", badge_message_id=2, voice_message_id=None,
        sender_username="b", name="B", email=None, company=None, title=None,
        notes="", transcript=None, lead_temp=None, next_step=None,
        needs_review=False, needs_voice=False,
    )
    a_leads = store.list_leads_for_export(chat_id=-100, event="EventA")
    b_leads = store.list_leads_for_export(chat_id=-100, event="EventB")
    assert [l.name for l in a_leads] == ["A"]
    assert [l.name for l in b_leads] == ["B"]


def test_list_expired_pending_photos(store: Store):
    old = store.add_pending_photo(
        chat_id=-100, message_id=10, sender_user_id=42,
        sender_username="alice", file_id="f1", local_path="/tmp/1.jpg",
    )
    # cutoff in the future so the photo looks expired
    cutoff = datetime.now(timezone.utc) + timedelta(seconds=1)
    expired = store.list_expired_pending_photos(older_than=cutoff)
    assert any(p.id == old.id for p in expired)
