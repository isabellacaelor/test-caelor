"""Config loaded from environment. Fail fast on missing required vars."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_group_chat_id: int
    anthropic_api_key: str
    openai_api_key: str
    event_name: str
    pairing_window_seconds: int
    data_dir: Path
    anthropic_model: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "leads.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"


def load_config() -> Config:
    missing = []

    def _require(name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            missing.append(name)
        return value

    telegram_bot_token = _require("TELEGRAM_BOT_TOKEN")
    telegram_group_chat_id_str = _require("TELEGRAM_GROUP_CHAT_ID")
    anthropic_api_key = _require("ANTHROPIC_API_KEY")
    openai_api_key = _require("OPENAI_API_KEY")
    event_name = _require("EVENT_NAME")

    if missing:
        sys.stderr.write(
            "Missing required environment variables:\n"
            + "\n".join(f"  - {name}" for name in missing)
            + "\n\nCopy .env.example to .env and fill in the values.\n"
        )
        sys.exit(1)

    try:
        telegram_group_chat_id = int(telegram_group_chat_id_str)
    except ValueError:
        sys.stderr.write(
            f"TELEGRAM_GROUP_CHAT_ID must be an integer, got: {telegram_group_chat_id_str!r}\n"
        )
        sys.exit(1)

    pairing_window_seconds = int(os.environ.get("PAIRING_WINDOW_SECONDS", "180"))
    data_dir = Path(os.environ.get("DATA_DIR", "./data")).resolve()
    anthropic_model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-6").strip()

    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "media").mkdir(parents=True, exist_ok=True)

    return Config(
        telegram_bot_token=telegram_bot_token,
        telegram_group_chat_id=telegram_group_chat_id,
        anthropic_api_key=anthropic_api_key,
        openai_api_key=openai_api_key,
        event_name=event_name,
        pairing_window_seconds=pairing_window_seconds,
        data_dir=data_dir,
        anthropic_model=anthropic_model,
    )
