"""SQLite-backed storage for pending photos and captured leads.

Single-writer by design — the bot is a single-process long-poller, so we don't
need connection pooling or WAL. One connection, serialized writes.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_photos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL,
    message_id      INTEGER NOT NULL,
    sender_user_id  INTEGER NOT NULL,
    sender_username TEXT,
    file_id         TEXT NOT NULL,
    local_path      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS leads (
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
    lead_temp          TEXT,
    next_step          TEXT,
    captured_at        TEXT NOT NULL,
    needs_review       INTEGER NOT NULL DEFAULT 0,
    needs_voice        INTEGER NOT NULL DEFAULT 0,
    deleted_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_leads_chat_event
    ON leads(chat_id, event);
"""


@dataclass
class PendingPhoto:
    id: int
    chat_id: int
    message_id: int
    sender_user_id: int
    sender_username: Optional[str]
    file_id: str
    local_path: str
    created_at: datetime


@dataclass
class Lead:
    id: int
    chat_id: int
    event: str
    badge_message_id: Optional[int]
    voice_message_id: Optional[int]
    sender_username: Optional[str]
    name: Optional[str]
    email: Optional[str]
    company: Optional[str]
    title: Optional[str]
    notes: Optional[str]
    transcript: Optional[str]
    lead_temp: Optional[str]
    next_step: Optional[str]
    captured_at: datetime
    needs_review: bool
    needs_voice: bool
    deleted_at: Optional[datetime]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime) -> str:
    return dt.isoformat()


def _from_iso(s: Optional[str]) -> Optional[datetime]:
    if s is None:
        return None
    return datetime.fromisoformat(s)


class Store:
    """Thin wrapper around a sqlite3.Connection. Not thread-safe."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---------- pending photos ----------

    def add_pending_photo(
        self,
        *,
        chat_id: int,
        message_id: int,
        sender_user_id: int,
        sender_username: Optional[str],
        file_id: str,
        local_path: str,
    ) -> PendingPhoto:
        created_at = _now()
        with self._tx() as conn:
            cur = conn.execute(
                """
                INSERT INTO pending_photos
                    (chat_id, message_id, sender_user_id, sender_username,
                     file_id, local_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    message_id,
                    sender_user_id,
                    sender_username,
                    file_id,
                    local_path,
                    _to_iso(created_at),
                ),
            )
            photo_id = cur.lastrowid
        return PendingPhoto(
            id=photo_id,
            chat_id=chat_id,
            message_id=message_id,
            sender_user_id=sender_user_id,
            sender_username=sender_username,
            file_id=file_id,
            local_path=local_path,
            created_at=created_at,
        )

    def remove_pending_photo(self, photo_id: int) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM pending_photos WHERE id = ?", (photo_id,))

    def get_pending_photo_by_message(
        self, *, chat_id: int, message_id: int
    ) -> Optional[PendingPhoto]:
        row = self.conn.execute(
            "SELECT * FROM pending_photos WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ).fetchone()
        return _row_to_pending(row) if row else None

    def find_pending_photo_for_sender(
        self, *, chat_id: int, sender_user_id: int
    ) -> Optional[PendingPhoto]:
        """Return the most recent pending photo from this sender in this chat, if any."""
        row = self.conn.execute(
            """
            SELECT * FROM pending_photos
            WHERE chat_id = ? AND sender_user_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (chat_id, sender_user_id),
        ).fetchone()
        return _row_to_pending(row) if row else None

    def list_expired_pending_photos(self, *, older_than: datetime) -> list[PendingPhoto]:
        rows = self.conn.execute(
            "SELECT * FROM pending_photos WHERE created_at < ?",
            (_to_iso(older_than),),
        ).fetchall()
        return [_row_to_pending(r) for r in rows]

    # ---------- leads ----------

    def insert_lead(
        self,
        *,
        chat_id: int,
        event: str,
        badge_message_id: Optional[int],
        voice_message_id: Optional[int],
        sender_username: Optional[str],
        name: Optional[str],
        email: Optional[str],
        company: Optional[str],
        title: Optional[str],
        notes: Optional[str],
        transcript: Optional[str],
        lead_temp: Optional[str],
        next_step: Optional[str],
        needs_review: bool,
        needs_voice: bool,
    ) -> int:
        captured_at = _now()
        with self._tx() as conn:
            cur = conn.execute(
                """
                INSERT INTO leads (
                    chat_id, event, badge_message_id, voice_message_id,
                    sender_username, name, email, company, title, notes,
                    transcript, lead_temp, next_step, captured_at,
                    needs_review, needs_voice
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    event,
                    badge_message_id,
                    voice_message_id,
                    sender_username,
                    name,
                    email,
                    company,
                    title,
                    notes,
                    transcript,
                    lead_temp,
                    next_step,
                    _to_iso(captured_at),
                    int(needs_review),
                    int(needs_voice),
                ),
            )
            return cur.lastrowid

    def soft_delete_lead(self, lead_id: int) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE leads SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                (_to_iso(_now()), lead_id),
            )
            return cur.rowcount > 0

    def list_leads_for_export(
        self, *, chat_id: int, event: str
    ) -> list[Lead]:
        rows = self.conn.execute(
            """
            SELECT * FROM leads
            WHERE chat_id = ? AND event = ? AND deleted_at IS NULL
            ORDER BY captured_at ASC
            """,
            (chat_id, event),
        ).fetchall()
        return [_row_to_lead(r) for r in rows]

    def list_recent_leads(
        self, *, chat_id: int, event: str, limit: int = 10
    ) -> list[Lead]:
        rows = self.conn.execute(
            """
            SELECT * FROM leads
            WHERE chat_id = ? AND event = ? AND deleted_at IS NULL
            ORDER BY captured_at DESC
            LIMIT ?
            """,
            (chat_id, event, limit),
        ).fetchall()
        return [_row_to_lead(r) for r in rows]


def _row_to_pending(row: sqlite3.Row) -> PendingPhoto:
    return PendingPhoto(
        id=row["id"],
        chat_id=row["chat_id"],
        message_id=row["message_id"],
        sender_user_id=row["sender_user_id"],
        sender_username=row["sender_username"],
        file_id=row["file_id"],
        local_path=row["local_path"],
        created_at=_from_iso(row["created_at"]),
    )


def _row_to_lead(row: sqlite3.Row) -> Lead:
    return Lead(
        id=row["id"],
        chat_id=row["chat_id"],
        event=row["event"],
        badge_message_id=row["badge_message_id"],
        voice_message_id=row["voice_message_id"],
        sender_username=row["sender_username"],
        name=row["name"],
        email=row["email"],
        company=row["company"],
        title=row["title"],
        notes=row["notes"],
        transcript=row["transcript"],
        lead_temp=row["lead_temp"],
        next_step=row["next_step"],
        captured_at=_from_iso(row["captured_at"]),
        needs_review=bool(row["needs_review"]),
        needs_voice=bool(row["needs_voice"]),
        deleted_at=_from_iso(row["deleted_at"]),
    )
