"""Voice transcription wrapper. Currently OpenAI Whisper; pluggable by design."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> str: ...


class WhisperTranscriber:
    """OpenAI Whisper API wrapper.

    Kept deliberately minimal: one call, returns a string. The structured
    extraction happens in ai.py — this module's only job is audio → text.
    """

    def __init__(self, api_key: str, model: str = "whisper-1") -> None:
        # Imported lazily so the rest of the bot doesn't require the openai
        # package if someone swaps in a local transcriber.
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError(
                "openai package is required for WhisperTranscriber. "
                "Install with: pip install openai"
            ) from e

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def transcribe(self, audio_path: Path) -> str:
        with audio_path.open("rb") as f:
            result = self.client.audio.transcriptions.create(
                model=self.model,
                file=f,
            )
        text = (result.text or "").strip()
        logger.debug("transcribed %s -> %d chars", audio_path.name, len(text))
        return text
