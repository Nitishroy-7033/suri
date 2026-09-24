"""What the workshop is showing, as the page last reported it.

The page is the only thing that knows what you are pointing at, which model
is loaded and which panels are open. It sends {"t":"holo_state"} whenever
that changes; tools read it back, so "what's this?" and "close that" mean
something. Only the fields below are kept, and lists are capped: this ends up
in a model's context.
"""

from __future__ import annotations

import asyncio
import secrets
import time

from ...core.events import Event

MAX_PARTS = 40
MAX_PANELS = 16


def _s(v, n: int = 160) -> str | None:
    return str(v)[:n] if v not in (None, "") else None


class HoloState:
    def __init__(self) -> None:
        self.view: dict = {}
        self.updated = 0.0
        self._shots: dict[str, asyncio.Future] = {}

    def update(self, data: dict) -> None:
        panels = []
        for p in (data.get("panels") or [])[:MAX_PANELS]:
            if isinstance(p, dict):
                panels.append({k: _s(p.get(k)) for k in ("id", "kind", "title", "path")
                               if p.get(k) not in (None, "")})
        model = data.get("model") if isinstance(data.get("model"), dict) else None
        self.view = {
            "open": bool(data.get("open")),
            "model": {k: _s(model.get(k)) for k in ("id", "name") if model.get(k)} if model else None,
            "parts": [_s(p, 80) for p in (data.get("parts") or [])[:MAX_PARTS] if p],
            "part_count": int(data.get("part_count") or len(data.get("parts") or [])),
            "selected": _s(data.get("selected"), 200),
            "hovered": _s(data.get("hovered"), 200),
            "exploded": bool(data.get("exploded")),
            "panels": panels,
            "focused": _s(data.get("focused"), 400),  # a file path or panel id
            "hands": int(data.get("hands") or 0),  # how many the camera sees
        }
        self.updated = time.time()

    @property
    def open(self) -> bool:
        return bool(self.view.get("open"))

    async def snapshot(self, events, timeout: float = 12.0) -> bytes | None:
        """A camera picture from the workshop page (for "make a 3D model of
        this"). The page gets a holo_snapshot_request and posts a JPEG to
        /api/snapshot; None if the workshop is closed or nothing comes."""
        if not self.open:
            return None
        sid = secrets.token_hex(4)
        fut = asyncio.get_running_loop().create_future()
        self._shots[sid] = fut
        events.publish(Event("holo_snapshot_request", data={"id": sid}))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._shots.pop(sid, None)

    def deliver(self, sid: str, jpeg: bytes) -> bool:
        fut = self._shots.get(sid)
        if fut is None or fut.done():
            return False
        fut.set_result(jpeg)
        return True

    def summary(self) -> dict:
        if not self.updated:
            return {"open": False, "note": "the workshop has not been opened yet"}
        view = {k: v for k, v in self.view.items() if v not in (None, [], "", 0, False)}
        view["open"] = self.open
        return view
