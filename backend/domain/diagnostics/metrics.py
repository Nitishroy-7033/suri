"""Jarvis's own vital signs: voice-turn latency and recent warnings/errors.

Per-agent model stats (tokens/s, first-token latency, errors) live in
core/models/hub.py, where every model call already passes through.
"""

from __future__ import annotations

import logging
import time
from collections import deque


class TurnMetrics:
    """The last few spoken replies: how long until Jarvis made a sound."""

    def __init__(self, keep: int = 20) -> None:
        self.turns: deque[dict] = deque(maxlen=keep)

    def record_turn(self, *, first_audio_ms: float, llm_first_ms: float | None = None,
                    spoken_s: float = 0.0, brain: str = "") -> None:
        self.turns.append({"ts": time.time(), "brain": brain,
                           "first_audio_ms": round(first_audio_ms),
                           "llm_first_ms": round(llm_first_ms) if llm_first_ms is not None else None,
                           "spoken_s": round(spoken_s, 1)})

    def summary(self) -> dict:
        if not self.turns:
            return {"turns": 0, "last": None, "avg_first_audio_ms": None}
        vals = [t["first_audio_ms"] for t in self.turns]
        return {"turns": len(self.turns), "last": self.turns[-1],
                "avg_first_audio_ms": round(sum(vals) / len(vals))}


class LogRing(logging.Handler):
    """Keeps the last warnings and errors in memory for the HUD's log panel."""

    def __init__(self, keep: int = 50) -> None:
        super().__init__(level=logging.WARNING)
        self.records: deque[dict] = deque(maxlen=keep)
        self.warnings = 0
        self.errors = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # a bad format string must not break logging
            msg = str(record.msg)
        if record.levelno >= logging.ERROR:
            self.errors += 1
        else:
            self.warnings += 1
        self.records.append({"ts": record.created, "level": record.levelname.lower(),
                             "logger": record.name, "msg": msg[:300]})

    def summary(self, last: int = 10) -> dict:
        return {"warnings": self.warnings, "errors": self.errors,
                "recent": list(self.records)[-last:]}


_RING: LogRing | None = None


def log_ring() -> LogRing:
    """The process-wide ring, attached to the root logger on first use."""
    global _RING
    if _RING is None:
        _RING = LogRing()
        logging.getLogger().addHandler(_RING)
    return _RING
