"""Pick a brain for a session and connect it, falling back if that fails.

To add a brain (a local speech-to-speech model, say), subclass Brain and add
a branch to `_make`. The session never needs to know it exists.
"""

from __future__ import annotations

import logging

from ...core.runtime import AgentRuntime
from ...config import Settings
from .base import Brain, SendAudio, SendJson

log = logging.getLogger("jarvis.brains")


def _make(name: str, settings: Settings, send_json: SendJson,
          send_audio: SendAudio, agent: AgentRuntime | None) -> Brain:
    if name == "gemini_live":
        from .gemini_live import GeminiLiveBrain

        return GeminiLiveBrain(settings, send_json, send_audio, agent)
    if name in ("pipeline", "offline"):
        from .pipeline import PipelineBrain

        return PipelineBrain(settings, send_json, send_audio, agent)
    raise RuntimeError(f"unknown brain {name!r}")


async def build_brain(settings: Settings, send_json: SendJson,
                      send_audio: SendAudio,
                      agent: AgentRuntime | None = None) -> tuple[Brain | None, str | None]:
    """Returns (brain, None) or (None, last error)."""
    mode = settings.jarvis_mode
    # In auto, prefer the mode that sounds human and fall back to the one
    # that keeps working when its quota runs out.
    order = ["gemini_live", "pipeline"] if mode == "auto" else [mode]

    error = None
    for name in order:
        try:
            brain = _make(name, settings, send_json, send_audio, agent)
            await brain.start()
            log.info("brain: %s", brain.name)
            return brain, None
        except Exception as exc:
            log.warning("brain %s unavailable: %s", name, exc)
            error = str(exc)
    return None, error or "no brain available"
