"""CSV export — column order, BOM, escaping of notes with commas/newlines."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from prototype.export import COLUMNS, build_csv
from prototype.storage import Lead


def _lead(**overrides) -> Lead:
    defaults = dict(
        id=1,
        chat_id=-100,
        event="KubeCon EU 2026",
        badge_message_id=10,
        voice_message_id=11,
        sender_username="alice",
        name="Ada Lovelace",
        email="ada@analyticalengines.com",
        company="Analytical Engines Ltd",
        title="Head of Research",
        notes="Wants demo Friday; asked about self-hosting.",
        transcript="Hot lead, Ada from Analytical Engines, wants a demo Friday",
        lead_temp="hot",
        next_step="Send demo link Friday",
        captured_at=datetime(2026, 4, 16, 14, 32, 11, tzinfo=timezone.utc),
        needs_review=False,
        needs_voice=False,
        deleted_at=None,
    )
    defaults.update(overrides)
    return Lead(**defaults)


def _read_rows(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    # Strip BOM if present, decode, parse.
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    text = data.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return reader.fieldnames or [], list(reader)


def test_csv_has_bom_for_excel():
    data = build_csv([_lead()])
    assert data.startswith(b"\xef\xbb\xbf")


def test_column_order_matches_spec():
    data = build_csv([_lead()])
    fieldnames, _ = _read_rows(data)
    assert fieldnames == COLUMNS


def test_basic_row_populated():
    data = build_csv([_lead()])
    _, rows = _read_rows(data)
    assert len(rows) == 1
    row = rows[0]
    assert row["Name"] == "Ada Lovelace"
    assert row["Email"] == "ada@analyticalengines.com"
    assert row["Company"] == "Analytical Engines Ltd"
    assert row["Lead Temp"] == "hot"
    assert row["Captured By"] == "@alice"
    assert row["Needs Review"] == ""


def test_notes_with_comma_are_quoted_not_split():
    notes = "Wants demo Friday, asked about self-hosting, approved budget."
    data = build_csv([_lead(notes=notes)])
    _, rows = _read_rows(data)
    assert rows[0]["Notes"] == notes


def test_notes_with_newline_preserved():
    notes = "Line one.\nLine two."
    data = build_csv([_lead(notes=notes)])
    _, rows = _read_rows(data)
    assert rows[0]["Notes"] == notes


def test_null_fields_become_empty_string():
    data = build_csv(
        [_lead(name=None, email=None, company=None, title=None, lead_temp=None, next_step=None)]
    )
    _, rows = _read_rows(data)
    row = rows[0]
    assert row["Name"] == ""
    assert row["Email"] == ""
    assert row["Company"] == ""
    assert row["Lead Temp"] == ""
    assert row["Next Step"] == ""


def test_needs_review_and_needs_voice_flags_concatenated():
    data = build_csv([_lead(needs_review=True, needs_voice=True)])
    _, rows = _read_rows(data)
    flag = rows[0]["Needs Review"]
    assert "ocr/ambiguous" in flag
    assert "no voice note" in flag


def test_empty_list_still_emits_header():
    data = build_csv([])
    fieldnames, rows = _read_rows(data)
    assert fieldnames == COLUMNS
    assert rows == []


def test_no_sender_no_at_prefix():
    data = build_csv([_lead(sender_username=None)])
    _, rows = _read_rows(data)
    assert rows[0]["Captured By"] == ""
