import io
import json
import os

import google.generativeai as genai
from pydub import AudioSegment

genai.configure(api_key=os.getenv("GEMINI_API_KEY", ""))

_PROMPT = """You are processing a sales lead captured at an event booth.

Inputs:
1. A photo of a conference/event badge
2. Either a voice note OR typed notes from a team member

Extract ALL contact info visible on the badge (name, email, company, job title, phone).
Use the notes/voice to determine lead temperature and next steps.

Temperature:
- HOT = ready to buy, urgent need, asked for demo/proposal
- WARM = interested, wants follow-up
- COLD = early stage, just browsing

Return ONLY valid JSON — no extra text:
{
  "name": "full name",
  "email": "email or empty string",
  "company": "company or empty string",
  "job_title": "title or empty string",
  "phone": "phone or empty string",
  "notes": "summary of the interaction",
  "temperature": "HOT or WARM or COLD",
  "next_step": "specific action e.g. Send pricing deck by Friday"
}"""


async def process(image_bytes: bytes, voice_or_text: bytes | str) -> dict:
    """
    image_bytes     — raw bytes of the badge photo
    voice_or_text   — OGG bytes from a voice note, OR a plain text string
    """
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    parts = [_PROMPT, {"mime_type": "image/jpeg", "data": image_bytes}]

    if isinstance(voice_or_text, bytes):
        # Convert OGG Opus (Telegram format) → MP3 for Gemini
        try:
            audio = AudioSegment.from_ogg(io.BytesIO(voice_or_text))
            mp3_buf = io.BytesIO()
            audio.export(mp3_buf, format="mp3")
            parts.append({"mime_type": "audio/mp3", "data": mp3_buf.getvalue()})
        except Exception:
            pass  # process badge only if audio fails
    elif isinstance(voice_or_text, str) and voice_or_text.strip():
        parts.append(f"Team member notes: {voice_or_text.strip()}")

    response = await model.generate_content_async(parts)
    return json.loads(response.text)
