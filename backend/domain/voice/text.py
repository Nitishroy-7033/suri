"""Turning LLM text into something speakable.

Two jobs:

* `ClauseSplitter` chops the token stream into speakable units so TTS can
  start on the first few words instead of waiting for the whole reply. This
  is the single biggest latency win in the pipeline.
* `text_for_speech` strips the markdown an LLM produces by reflex. Nothing
  sounds worse than a voice reading "asterisk asterisk important".
"""

from __future__ import annotations

import re
from collections.abc import Iterator

# Accept the name on its own or with any of the usual lead-ins. openWakeWord
# fires on all of these, and Whisper will transcribe whichever was said.
WAKE_PREFIX = re.compile(
    r"^\s*(?:hey|hi|ok|okay|yo|hello)?[\s,]*jarvis\b[\s,.!?:-]*", re.IGNORECASE
)

STOP_PHRASES = {
    "stop", "stop it", "stop talking", "be quiet", "quiet", "shut up",
    "cancel", "nevermind", "never mind", "forget it", "that's all",
    "thats all", "thanks jarvis", "thank you jarvis", "goodbye", "bye",
}

# Abbreviations whose full stop is not a sentence end.
_ABBREV = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "eg",
    "ie", "approx", "no", "fig", "inc", "ltd", "co", "al",
}

_MARKDOWN_PATTERNS = [
    (re.compile(r"```[\s\S]*?```"), " code block "),   # fenced code
    (re.compile(r"`([^`]*)`"), r"\1"),                  # inline code
    (re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"),    # links / images
    (re.compile(r"^#{1,6}\s*", re.MULTILINE), ""),      # headings
    (re.compile(r"^\s*[-*+]\s+", re.MULTILINE), ""),    # bullets
    (re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE), ""),  # numbered lists
    (re.compile(r"\*\*([^*]*)\*\*"), r"\1"),            # bold
    (re.compile(r"(?<!\w)[*_]([^*_]+)[*_](?!\w)"), r"\1"),  # italics
    (re.compile(r"^\s*>\s?", re.MULTILINE), ""),        # blockquotes
    (re.compile(r"https?://\S+"), " a link "),
    (re.compile(r"[\U0001F300-\U0001FAFF☀-➿]"), ""),  # emoji
]

_SPOKEN = {
    "&": " and ", "%": " percent ", "$": " dollars ", "#": " number ",
    "@": " at ", "+": " plus ", "=": " equals ", "~": " about ",
    "°": " degrees ", "/": " slash ",
}


def has_wake_word(text: str) -> bool:
    """Does this transcript actually start by addressing Jarvis?

    Used to confirm a marginal wake-word score. openWakeWord was trained
    largely on US/UK speech and scores poorly on some accents -- measured
    here at 0.002-0.355 for Indian-accented voices versus 0.99+ for US ones.
    Lowering the threshold alone cannot fix that, because a 0.002 detection
    is indistinguishable from a distractor. Confirming against the transcript
    can, and costs nothing: both brains transcribe the utterance anyway.
    """
    return bool(WAKE_PREFIX.match(text or ""))


def strip_wake_word(text: str) -> str:
    """Remove a leading "jarvis"/"hey jarvis"/... from a transcript.

    Whisper transcribes the wake word too, because pre-roll deliberately
    includes it. Leaving it in makes the LLM answer as if addressed in the
    third person.
    """
    return WAKE_PREFIX.sub("", text, count=1).strip()


def is_stop_phrase(text: str) -> bool:
    """True for short "shut up" style commands that need no LLM at all."""
    cleaned = re.sub(r"[^\w\s']", "", text).strip().lower()
    return bool(cleaned) and len(cleaned.split()) <= 4 and cleaned in STOP_PHRASES


def text_for_speech(text: str) -> str:
    for pattern, repl in _MARKDOWN_PATTERNS:
        text = pattern.sub(repl, text)
    for char, word in _SPOKEN.items():
        text = text.replace(char, word)
    return re.sub(r"\s+", " ", text).strip()


def _ends_sentence(buf: str) -> bool:
    """Is buf at a real sentence end, rather than a decimal or an abbrev?"""
    stripped = buf.rstrip()
    if not stripped or stripped[-1] not in ".!?":
        return False
    if stripped[-1] == ".":
        tail = re.search(r"([\w.]+)\.$", stripped)
        if tail:
            word = tail.group(1).lower().replace(".", "")
            if word in _ABBREV:
                return False
            if word.isdigit():  # "3." in "3.5"
                return False
        if stripped.endswith(".."):
            return False
    return True


class ClauseSplitter:
    """Chops a streaming reply into units worth sending to TTS.

    The first unit is deliberately tiny. Synthesis time is roughly linear in
    length, so getting six words out of the door lands audio ~300 ms sooner
    than waiting for a full sentence -- and the listener only ever notices
    the first gap.
    """

    def __init__(
        self,
        first_max_chars: int = 60,
        min_chars: int = 80,
        max_chars: int = 200,
    ) -> None:
        self.first_max_chars = first_max_chars
        self.min_chars = min_chars
        self.max_chars = max_chars
        self._buf = ""
        self._emitted = 0

    def feed(self, delta: str) -> Iterator[str]:
        self._buf += delta
        while True:
            cut = self._find_cut()
            if cut is None:
                return
            chunk, self._buf = self._buf[:cut].strip(), self._buf[cut:].lstrip()
            if chunk:
                self._emitted += 1
                yield chunk

    def flush(self) -> Iterator[str]:
        if self._buf.strip():
            self._emitted += 1
            yield self._buf.strip()
        self._buf = ""

    def _find_cut(self) -> int | None:
        buf = self._buf
        first = self._emitted == 0
        limit = self.first_max_chars if first else self.max_chars

        if first:
            # Leave on the earliest decent boundary past a few words.
            for i, ch in enumerate(buf):
                if i >= 12 and ch in ".!?,;:" and self._boundary_ok(buf, i):
                    return i + 1
            if len(buf) >= limit:
                return self._last_space(buf, limit)
            return None

        for i, ch in enumerate(buf):
            if ch in ".!?" and self._boundary_ok(buf, i) and i + 1 >= self.min_chars:
                return i + 1
        for i, ch in enumerate(buf):
            if ch in ",;:" and i + 1 >= self.max_chars * 0.6:
                return i + 1
        if len(buf) >= limit:
            return self._last_space(buf, limit)
        return None

    def _boundary_ok(self, buf: str, i: int) -> bool:
        if buf[i] in ",;:":
            return True
        if not _ends_sentence(buf[: i + 1]):
            return False
        # Require whitespace (or end of buffer) after the mark, so "3.5" and
        # a sentence end are told apart even mid-stream.
        return i + 1 >= len(buf) or buf[i + 1].isspace()

    @staticmethod
    def _last_space(buf: str, limit: int) -> int:
        cut = buf.rfind(" ", 0, limit)
        return cut if cut > 0 else limit
