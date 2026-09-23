"""App-wide pub/sub for things that happen without anyone asking.

A timer going off or the camera seeing movement is not a reply to anything,
so it cannot ride the request/response path of a voice turn. Producers
publish an Event here; every connected session gets its own queue and decides
whether to speak it (only when idle -- an alert must never talk over the user)
or just show it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.events")


@dataclass
class Event:
    kind: str  # "motion", "timer", ...
    #: What Jarvis should tell the user, phrased for the model, e.g.
    #: "Your 5 minute timer for tea is done." None = show it, don't say it.
    say: str | None = None
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_wire(self) -> dict:
        return {"t": "event", "kind": self.kind, "say": self.say,
                "data": self.data, "ts": self.ts}


class EventBus:
    def __init__(self, maxsize: int = 32) -> None:
        self._subs: set[asyncio.Queue[Event]] = set()
        self._maxsize = maxsize

    def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(self._maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        self._subs.discard(q)

    def publish(self, event: Event) -> None:
        log.info("event %s: %s", event.kind, event.say or event.data)
        for q in self._subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A session that is not draining its queue should lose
                # alerts, not stall every other producer.
                log.warning("event queue full, dropping %s", event.kind)
