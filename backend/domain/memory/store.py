"""Long-term memory: facts that outlive a conversation and a restart.

Deliberately simple -- a JSON file of short facts, with keyword matching for
recall. A voice assistant's memory is "my wife is called Priya" and "I take
my coffee black", tens of entries, not a document corpus; embeddings would
add a model load and a dependency to answer questions a substring can.

The most recent facts are also folded into the system prompt (see
AgentRuntime.system_prompt), so Jarvis knows them without having to think to
look them up.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("jarvis.memory")

_WORD = re.compile(r"\w+", re.UNICODE)
_STOP = {"the", "a", "an", "is", "are", "my", "i", "me", "what", "do", "you",
         "of", "to", "and", "in", "on", "for", "about", "remember", "know"}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


@dataclass
class Fact:
    id: str
    text: str
    ts: float


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._facts: list[Fact] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            self._facts = [Fact(**f) for f in raw.get("facts", [])]
        except (OSError, ValueError, TypeError) as exc:
            # A corrupt memory file must not stop Jarvis starting. Keep the
            # bad file for inspection rather than overwriting it.
            log.error("memory file unreadable (%s); starting empty", exc)
            self.path.rename(self.path.with_suffix(".corrupt.json"))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"facts": [asdict(f) for f in self._facts]},
                                  ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.path)  # atomic: a crash mid-write loses nothing

    def add(self, text: str) -> Fact:
        text = text.strip()
        for f in self._facts:
            if f.text.lower() == text.lower():
                return f
        fact = Fact(id=uuid.uuid4().hex[:8], text=text, ts=time.time())
        self._facts.append(fact)
        self._save()
        return fact

    def search(self, query: str, limit: int = 5) -> list[Fact]:
        q = _words(query)
        if not q:
            return self.recent(limit)
        scored = [(len(q & _words(f.text)), f) for f in self._facts]
        hits = [f for score, f in sorted(scored, key=lambda s: (-s[0], -s[1].ts))
                if score > 0]
        return hits[:limit]

    def forget(self, query: str) -> list[Fact]:
        """Remove facts matching `query` best. Returns what was removed."""
        hits = self.search(query, limit=len(self._facts))
        if not hits:
            return []
        best = len(_words(query) & _words(hits[0].text))
        gone = [f for f in hits if len(_words(query) & _words(f.text)) == best]
        ids = {f.id for f in gone}
        self._facts = [f for f in self._facts if f.id not in ids]
        self._save()
        return gone

    def recent(self, limit: int = 20) -> list[Fact]:
        return sorted(self._facts, key=lambda f: -f.ts)[:limit]

    def __len__(self) -> int:
        return len(self._facts)
