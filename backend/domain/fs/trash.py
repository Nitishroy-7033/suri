"""Deleting files: only to the Recycle Bin, and only on your own yes.

fs_delete (the model's tool) and a drop on the trash only *ask*: they make a
pending request and the workshop shows a confirm panel. The files move when
the yes comes from you -- a click or Enter on that panel, a thumbs up held
over it, or your own voice saying yes (checked against what you actually
said, not what the model claims you said). A file that tells the model to
delete something can therefore at most make the panel appear.

Nothing is ever deleted outright: send2trash moves it to the Recycle Bin, so
it can be restored from there.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from dataclasses import dataclass, field

from ...core.events import Event
from .access import Refused, display, resolve_checked

log = logging.getLogger("jarvis.fs")

MAX_ITEMS = 20
TTL_S = 120
SOURCES = ("ui", "transcript")  # never "model"

# English and Hindi, Latin or Devanagari (Gemini writes Hindi either way).
# Not a bare "ha": that is also a laugh.
_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|ok|okay|confirm|confirmed|do it|go ahead|delete (it|them)|"
                  r"haan|haa|ji haan|theek hai|kar do)\b|हाँ|हां|कर दो|ठीक है", re.I)
_NO = re.compile(r"\b(no|nope|don'?t|do not|cancel|stop|wait|keep|nahi|nahin|mat|ruko)\b|नहीं|नही|मत|रुको", re.I)


def said_yes(text: str) -> bool:
    return bool(_YES.search(text or "")) and not _NO.search(text or "")


def said_no(text: str) -> bool:
    return bool(_NO.search(text or ""))


@dataclass
class Pending:
    id: str
    paths: list[str]
    items: list[dict]
    created: float = field(default_factory=time.time)

    @property
    def expired(self) -> bool:
        return time.time() - self.created > TTL_S


class Trash:
    def __init__(self, runtime, send2trash=None) -> None:
        self.runtime = runtime
        self._send2trash = send2trash
        self.pending: dict[str, Pending] = {}

    def _bin(self):
        if self._send2trash is None:
            from send2trash import send2trash

            self._send2trash = send2trash
        return self._send2trash

    def request(self, paths: list[str]) -> Pending:
        if not paths:
            raise Refused("nothing to delete")
        if len(paths) > MAX_ITEMS:
            raise Refused(f"that's {len(paths)} items; I only delete up to {MAX_ITEMS} at a time")
        items = []
        for raw in paths:
            p = resolve_checked(raw, self.runtime.settings)
            if p.parent == p:
                raise Refused("I won't delete a whole drive")
            items.append({"path": display(p), "name": p.name, "kind": "dir" if p.is_dir() else "file",
                          "size": None if p.is_dir() else p.stat().st_size})
        self._drop_expired()
        req = Pending(id=secrets.token_hex(4), paths=[i["path"] for i in items], items=items)
        self.pending[req.id] = req
        self.runtime.events.publish(Event("fs_confirm", data={"id": req.id, "items": items}))
        log.info("delete requested (%s): %s", req.id, ", ".join(req.paths))
        return req

    def latest(self) -> Pending | None:
        self._drop_expired()
        return max(self.pending.values(), key=lambda r: r.created, default=None)

    def confirm(self, req_id: str, source: str) -> str:
        if source not in SOURCES:
            return "refused: only the user can confirm a delete"
        req = self.pending.pop(req_id, None)
        if req is None or req.expired:
            return "that delete request has expired; ask again"
        moved, failed = [], []
        for raw in req.paths:
            try:
                p = resolve_checked(raw, self.runtime.settings)  # still allowed, still there?
                self._bin()(str(p))
                moved.append(raw)
            except (Refused, OSError, Exception) as exc:  # send2trash raises its own types
                failed.append({"path": raw, "error": str(exc)[:160]})
        log.info("delete %s confirmed by %s: %d moved, %d failed", req_id, source, len(moved), len(failed))
        say = None
        if failed:
            say = (f"Tell the user {len(failed)} of the {len(req.paths)} items could not be moved "
                   f"to the Recycle Bin: {failed[0]['error']}")
        self.runtime.events.publish(Event("fs_confirm_done", say=say, data={
            "id": req_id, "ok": not failed, "moved": moved, "failed": failed, "by": source}))
        return f"moved {len(moved)} to the Recycle Bin" + (f", {len(failed)} failed" if failed else "")

    def cancel(self, req_id: str) -> None:
        if self.pending.pop(req_id, None):
            self.runtime.events.publish(Event("fs_confirm_done", data={"id": req_id, "ok": False, "cancelled": True}))

    def _drop_expired(self) -> None:
        for rid in [r.id for r in self.pending.values() if r.expired]:
            self.cancel(rid)
