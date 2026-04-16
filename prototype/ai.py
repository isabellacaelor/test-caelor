"""Claude-based lead extraction from (badge image, voice transcript).

One call per captured pair. Uses structured outputs so we get a validated
ExtractedLead back without parsing. Prompt caches the system prompt — same
prefix across every lead in a run, so we hit cache after the first call.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import Optional

import anthropic

from .models import ExtractedLead

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You extract structured lead data from a conference badge photo and an optional voice note transcript recorded by a booth rep.

From the badge photo, extract:
- name: full name exactly as printed
- company: the attendee's employer (NOT the conference name, NOT the sponsor the booth belongs to)
- email: the attendee's personal work email; null if not printed or not legible
- title: job title if printed

From the voice transcript (if provided), extract:
- notes: one-paragraph summary covering product interest, objections, commitments made, anything worth remembering on first follow-up. Keep the rep's original phrasing where it matters ("wants self-hosting", "budget approved Q3").
- lead_temp: 'hot' = ready to buy / wants a demo / asked to be contacted soon. 'warm' = interested but not urgent, learning. 'cold' = polite browser, no real intent. If the rep mentioned a temperature but was unclear, default to 'warm'. If there was no voice note, set to null.
- next_step: one short sentence describing the next action implied by the voice ("send demo link Friday", "follow up in Q3"). Null if none is clearly implied.

Rules:
- If a badge field is not legible, return null — never guess an email or name.
- If the image is not a conference badge or business card, set is_badge=false and all OCR fields to null.
- Set confidence_low=true if any field was difficult to read (blur, glare, cropping) or the transcript was ambiguous. This is a human-review flag, not a hard error.
- Keep notes factual. Don't invent context not present in the transcript.
"""


class LeadExtractor:
    def __init__(self, api_key: str, model: str = "claude-opus-4-6") -> None:
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def extract(
        self,
        *,
        badge_image_path: Path,
        transcript: Optional[str],
    ) -> ExtractedLead:
        """Extract lead fields. Raises on API errors — caller decides fallback."""
        image_data = base64.standard_b64encode(badge_image_path.read_bytes()).decode("ascii")
        media_type = _guess_media_type(badge_image_path)

        user_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_data,
                },
            }
        ]
        if transcript:
            user_content.append(
                {
                    "type": "text",
                    "text": f"Voice note transcript:\n\n{transcript}",
                }
            )
        else:
            user_content.append(
                {
                    "type": "text",
                    "text": "No voice note was recorded. Extract only the badge fields.",
                }
            )

        # structured outputs via messages.parse — validates against ExtractedLead
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=2048,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
            output_format=ExtractedLead,
        )

        if response.parsed_output is None:
            # happens on refusal or schema validation failure
            raise RuntimeError(
                f"Extraction returned no parsed output (stop_reason={response.stop_reason})"
            )

        logger.debug(
            "extraction cache: read=%s created=%s input=%s output=%s",
            response.usage.cache_read_input_tokens,
            response.usage.cache_creation_input_tokens,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        return response.parsed_output


def _guess_media_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    # Anthropic vision accepts image/jpeg, image/png, image/gif, image/webp.
    # Telegram photos are typically JPEG; default to JPEG on unknown.
    if guessed in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        return guessed
    return "image/jpeg"
