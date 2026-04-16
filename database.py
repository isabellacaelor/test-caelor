"""
Simple JSON file storage — no external database needed.
Leads are saved to leads.json in the same folder as the bot.
"""
import json
import os
from datetime import datetime
from pathlib import Path

_FILE = Path(os.getenv("LEADS_FILE", "leads.json"))


def _read() -> list[dict]:
    if not _FILE.exists():
        return []
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write(leads: list[dict]):
    _FILE.write_text(json.dumps(leads, indent=2, default=str), encoding="utf-8")


def init():
    if not _FILE.exists():
        _write([])
    print(f"Storage: {_FILE.absolute()}")


def save(lead: dict) -> int:
    leads = _read()
    lead["id"] = len(leads) + 1
    lead["captured_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    leads.append(lead)
    _write(leads)
    return lead["id"]


def all_leads() -> list[dict]:
    return _read()


def count() -> int:
    return len(_read())
