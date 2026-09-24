"""Describe an image in words.

Used by the `look` tool and the web agent's screenshots. It turns a picture
into text, so it works the same whichever brain asked. The voice LLM may not
see, and handing Gemini Live a frame mid-turn is less reliable than asking a
vision model a direct question and passing the answer back as a tool result.

Which model is VISION_AGENT__PROVIDER / __MODEL (Gemini Flash by default);
any provider in core/models that accepts images works.
"""

from __future__ import annotations

import logging

log = logging.getLogger("jarvis.vision")


class Vision:
    def __init__(self, model) -> None:
        self.model = model  # a FallbackModel from runtime.models

    @property
    def name(self) -> str:
        return self.model.name

    async def describe(self, jpeg: bytes, question: str) -> str:
        prompt = (
            f"{question.strip() or 'Describe what you see.'}\n"
            "Answer in two or three plain sentences, as if telling someone who "
            "cannot see the image. No markdown."
        )
        result = await self.model.complete(
            [{"role": "user", "content": [
                {"type": "image", "data": jpeg, "mime": "image/jpeg"},
                {"type": "text", "text": prompt}]}],
            max_tokens=400)
        return result.text or "I could not make anything out."
