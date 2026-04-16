"""Pydantic models — used both for Claude structured output and internal typing."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

LeadTemp = Literal["hot", "warm", "cold"]


class ExtractedLead(BaseModel):
    """The schema Claude fills in from (badge image, voice transcript)."""

    name: Optional[str] = Field(
        default=None,
        description="Full name exactly as printed on the badge. Null if not legible.",
    )
    email: Optional[str] = Field(
        default=None,
        description=(
            "Attendee's email address from the badge. Null if not printed "
            "or not legible. Do not guess."
        ),
    )
    company: Optional[str] = Field(
        default=None,
        description=(
            "The attendee's employer as printed on the badge, not the conference name. "
            "Null if not printed."
        ),
    )
    title: Optional[str] = Field(
        default=None,
        description="Job title if printed on the badge. Null otherwise.",
    )
    notes: str = Field(
        description=(
            "One-paragraph summary of the voice note covering product interest, "
            "objections, commitments, anything worth remembering on first follow-up. "
            "If no voice note was provided, set to empty string."
        ),
    )
    lead_temp: Optional[LeadTemp] = Field(
        default=None,
        description=(
            "Temperature from the voice note. 'hot' = ready to buy or wants a demo soon; "
            "'warm' = interested but not urgent; 'cold' = polite browser. "
            "Default to 'warm' if mentioned but unclear. Null if no voice note."
        ),
    )
    next_step: Optional[str] = Field(
        default=None,
        description=(
            "One short sentence describing the next action implied by the voice note. "
            "Null if none is clearly implied."
        ),
    )
    is_badge: bool = Field(
        description=(
            "True if the image is a conference badge or business card. "
            "False if it is something else (e.g. landscape photo, selfie, swag)."
        ),
    )
    confidence_low: bool = Field(
        description=(
            "True if any field was difficult to read (blurry, cropped, glare) "
            "or if the transcript was ambiguous about lead_temp. "
            "Set to true when a human should review before follow-up."
        ),
    )
