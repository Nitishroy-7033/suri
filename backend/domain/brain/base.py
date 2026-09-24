"""The seam between turn-taking and whatever actually thinks.

Two very different things sit behind this interface:

* `GeminiLiveBrain` -- audio in, audio out, one model. It does its own
  transcription, turn detection and interruption.
* `PipelineBrain` -- STT then LLM then TTS, three services we orchestrate
  and cancel ourselves.

Keeping them behind one interface is what lets the session fall back from one
to the other without the frontend noticing: the wire format is identical
either way.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

import numpy as np

SendJson = Callable[[dict], Awaitable[None]]
SendAudio = Callable[[bytes], Awaitable[None]]


class Brain(ABC):
    """One conversation's worth of thinking.

    The session owns wake word, VAD and the state machine; a Brain only ever
    sees audio it is supposed to act on.
    """

    name: str = "brain"

    #: Set by the session before a turn whose wake-word score was marginal.
    #: The brain must drop the turn silently if the transcript turns out not
    #: to address Jarvis at all.
    require_wake: bool = False

    @abstractmethod
    async def start(self) -> None:
        """Prepare (connect, load). Called once per session."""

    @abstractmethod
    async def begin_turn(self) -> None:
        """The user has started talking to us."""

    @abstractmethod
    async def on_audio(self, pcm: np.ndarray) -> None:
        """A 16 kHz int16 frame captured while the user is addressing us."""

    @abstractmethod
    async def end_turn(self) -> None:
        """The user stopped talking. Only meaningful for brains that need an
        explicit endpoint; Gemini detects this itself."""

    @abstractmethod
    async def cancel(self, reason: str) -> None:
        """Stop generating and speaking, now."""

    @abstractmethod
    async def close(self) -> None:
        """Release everything."""

    async def announce(self, prompt: str) -> bool:
        """Speak up unprompted -- a timer finished, the camera saw movement.

        `prompt` tells the model what happened; it phrases the actual words.
        The session only calls this while IDLE, so it never talks over
        anyone. Returns False if this brain cannot, or is busy.
        """
        return False

    async def speak(self, text: str) -> bool:
        """Say these exact words, unprompted.

        For agents that already wrote the sentence with their own model (the
        diagnostics agent's alerts): the voice only speaks it. Same rules as
        announce -- only called while IDLE; False if busy or unable.
        """
        return False

    async def ask(self, text: str) -> bool:
        """Answer a typed message, spoken like any other reply.

        The session only calls this while nothing else is in flight. Returns
        False if this brain cannot take text, or is busy.
        """
        return False

    @property
    def handles_turn_detection(self) -> bool:
        """True if the brain decides when the user stopped talking.

        Gemini Live does its own endpointing and interruption, so the session
        must not also run its own -- two endpointers disagreeing produces
        turns that get cut in half.
        """
        return False
