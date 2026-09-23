"""Conversation memory, and the awkward business of interruptions.

The interesting part is `truncate_to_heard`. When you cut Jarvis off, the
model has generated more text than reached your ears. If we store the whole
thing, it believes it said things you never heard, and the next turn drifts:
it will not repeat the part it thinks you got, and "sorry, carry on" produces
nonsense. So the stored message is trimmed to what actually played, with an
explicit marker that it was cut off.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

INTERRUPTED_SUFFIX = " - [interrupted by user]"


@dataclass
class SpokenSpan:
    """A clause of TTS audio and where it sat in the turn's timeline."""

    start_sample: int
    end_sample: int
    text: str

    @property
    def samples(self) -> int:
        return max(1, self.end_sample - self.start_sample)


def truncate_to_heard(spans: list[SpokenSpan], played_samples: int) -> str:
    """Rebuild only the words that actually reached the speaker.

    Word-level within the interrupted clause: sample-accurate truncation
    mid-word would read strangely in the transcript, and the model only needs
    to know roughly where it got to.
    """
    if played_samples <= 0 or not spans:
        return ""

    out: list[str] = []
    for span in spans:
        if span.end_sample <= played_samples:
            out.append(span.text)
            continue
        if span.start_sample >= played_samples:
            break
        fraction = (played_samples - span.start_sample) / span.samples
        words = span.text.split()
        if words:
            keep = max(1, math.ceil(fraction * len(words)))
            out.append(" ".join(words[:keep]))
        break
    return " ".join(out).strip()


@dataclass
class Conversation:
    """Chat history with a turn cap and an idle reset."""

    system_prompt: str
    max_turns: int = 12
    ttl_seconds: int = 300
    messages: list[dict] = field(default_factory=list)
    last_activity: float = field(default_factory=time.monotonic)

    def _expire_if_stale(self) -> None:
        """Forget an old conversation rather than half-remembering it.

        Asking "what about Java?" an hour after a Python chat should not
        silently inherit that context.
        """
        if self.messages and time.monotonic() - self.last_activity > self.ttl_seconds:
            self.messages.clear()

    def add_user(self, text: str) -> None:
        self._expire_if_stale()
        text = text.strip()
        if not text:
            return
        # Two user messages in a row happen when you interrupt before Jarvis
        # said anything. Some APIs reject that, so merge instead.
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages[-1]["content"] += "\n" + text
        else:
            self.messages.append({"role": "user", "content": text})
        self.last_activity = time.monotonic()
        self._trim()

    def add_assistant(self, text: str, *, interrupted: bool = False) -> None:
        text = text.strip()
        if not text:
            return
        if interrupted:
            text += INTERRUPTED_SUFFIX
        self.messages.append({"role": "assistant", "content": text})
        self.last_activity = time.monotonic()
        self._trim()

    def add_tool_exchange(self, assistant: dict, results: list[dict]) -> None:
        """An assistant message carrying tool_calls, and the tool replies.

        Stored so a follow-up ("and tomorrow?") can build on what a search
        already found instead of searching again.
        """
        self.messages.append(assistant)
        self.messages.extend(results)
        self.last_activity = time.monotonic()
        self._trim()

    def _trim(self) -> None:
        limit = self.max_turns * 2
        if len(self.messages) > limit:
            del self.messages[: len(self.messages) - limit]
        # Never start mid-exchange. A leading tool result whose call was
        # trimmed away is rejected outright by OpenAI-style APIs.
        while self.messages and self.messages[0]["role"] != "user":
            self.messages.pop(0)

    def for_llm(self) -> list[dict]:
        self._expire_if_stale()
        return [{"role": "system", "content": self.system_prompt}, *self.messages]

    def reset(self) -> None:
        self.messages.clear()
        self.last_activity = time.monotonic()
