"""CSV export of captured leads. Matches the schema in PRD §5."""

from __future__ import annotations

import csv
import io
from typing import Iterable

from .storage import Lead

COLUMNS = [
    "ID",
    "Name",
    "Email",
    "Company",
    "Title",
    "Notes",
    "Lead Temp",
    "Next Step",
    "Captured At",
    "Captured By",
    "Event",
    "Needs Review",
    "Transcript",
]


def build_csv(leads: Iterable[Lead]) -> bytes:
    """Return CSV bytes with a UTF-8 BOM so Excel detects encoding correctly."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    for lead in leads:
        writer.writerow(_lead_to_row(lead))
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


def _lead_to_row(lead: Lead) -> dict[str, str]:
    # needs_voice is reflected through needs_review in the export: a lead
    # without a voice note is always needs_review=True, so event lead sees it.
    review_flags = []
    if lead.needs_review:
        review_flags.append("ocr/ambiguous")
    if lead.needs_voice:
        review_flags.append("no voice note")

    return {
        "ID": str(lead.id),
        "Name": lead.name or "",
        "Email": lead.email or "",
        "Company": lead.company or "",
        "Title": lead.title or "",
        "Notes": lead.notes or "",
        "Lead Temp": lead.lead_temp or "",
        "Next Step": lead.next_step or "",
        "Captured At": lead.captured_at.isoformat() if lead.captured_at else "",
        "Captured By": f"@{lead.sender_username}" if lead.sender_username else "",
        "Event": lead.event,
        "Needs Review": "; ".join(review_flags) if review_flags else "",
        "Transcript": lead.transcript or "",
    }
