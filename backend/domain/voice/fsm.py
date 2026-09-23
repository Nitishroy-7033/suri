"""Conversation state machine.

This is what separates a conversation from a walkie-talkie. The two states
that do the heavy lifting are:

* IDLE -- the wake word runs and nothing else does. No API calls, no quota
  burned, no audio leaving the machine. This is the "always listening but
  silent" behaviour.
* FOLLOW_UP_WINDOW -- for a few seconds after Jarvis stops talking you can
  just keep speaking, no wake word needed.

Wake word detection is deliberately OFF while listening, thinking and
speaking. Three reasons: you may say "Jarvis" inside a sentence; the assistant
saying its own name would re-trigger itself through the speakers; and skipping
that inference frees CPU exactly when STT and TTS need it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Literal

log = logging.getLogger("jarvis.fsm")


class State(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    FOLLOW_UP_WINDOW = "FOLLOW_UP_WINDOW"


VadMode = Literal["idle", "endpoint", "barge", "trigger"]

# Which states let the wake word fire.
_WAKE_ENABLED = {State.IDLE, State.FOLLOW_UP_WINDOW}

# What the VAD is being used for in each state.
_VAD_MODE: dict[State, VadMode] = {
    State.IDLE: "idle",  # only to gate wake-word inference
    State.LISTENING: "endpoint",  # deciding when the user stopped
    State.THINKING: "barge",  # user changing their mind mid-compute
    State.SPEAKING: "barge",  # interruption
    State.FOLLOW_UP_WINDOW: "trigger",  # continue without the wake word
}


class Fsm:
    def __init__(self, emit: Callable[[dict], Awaitable[None]]) -> None:
        self._emit = emit
        self.state = State.IDLE
        self.entered_at = time.monotonic()
        self.transitions = 0

    @property
    def wake_enabled(self) -> bool:
        return self.state in _WAKE_ENABLED

    @property
    def vad_mode(self) -> VadMode:
        return _VAD_MODE[self.state]

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.entered_at) * 1000

    def touch(self) -> None:
        """Restart the clock for the current state without leaving it."""
        self.entered_at = time.monotonic()

    def is_(self, *states: State) -> bool:
        return self.state in states

    async def to(self, new: State, reason: str = "") -> None:
        if new is self.state:
            return
        old = self.state
        self.state = new
        self.entered_at = time.monotonic()
        self.transitions += 1
        log.info("%s -> %s (%s)", old, new, reason or "-")
        await self._emit(
            {
                "t": "state",
                "from": str(old),
                "to": str(new),
                "reason": reason,
                "ts": time.time(),
            }
        )
