"""Describe an image in words.

Used by the `look` tool. It turns a camera frame into text, so it works the
same whichever brain asked. The pipeline's Groq LLM cannot see, and handing
Gemini Live a frame mid-turn is less reliable than asking a vision model a
direct question and passing the answer back as a tool result.

Uses the ordinary (non-live) Gemini endpoint, so it shares the free key the
Live brain already uses.
"""

from __future__ import annotations

import logging

from ...config import Settings

log = logging.getLogger("jarvis.vision")


class GeminiVision:
    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        from google import genai

        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self.settings = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)

    async def describe(self, jpeg: bytes, question: str) -> str:
        from google.genai import types

        prompt = (
            f"{question.strip() or 'Describe what you see.'}\n"
            "Answer in two or three plain sentences, as if telling someone who "
            "cannot see the image. No markdown."
        )
        resp = await self._client.aio.models.generate_content(
            model=self.settings.vision_model,
            contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
                      prompt],
        )
        return (resp.text or "").strip() or "I could not make anything out."
