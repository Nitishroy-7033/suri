"""Deleting to the Recycle Bin (domain/fs/trash.py): nothing moves without
the user's own yes -- a click, a thumbs up, or their spoken words -- and
never on the model's say-so.

send2trash is faked: nothing is really deleted.

Run: .venv/Scripts/python.exe tests/test_trash.py
"""

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import Settings
from backend.core.runtime import AgentRuntime
from backend.core.tools.catalog import build_registry
from backend.domain.fs import trash as trash_mod
from backend.domain.fs.access import Refused
from backend.domain.fs.trash import Trash, said_no, said_yes

SANDBOX = Path(__file__).resolve().parent / "_trash_sandbox"


def run(coro):
    return asyncio.run(coro)


def setup():
    shutil.rmtree(SANDBOX, ignore_errors=True)
    SANDBOX.mkdir()
    rt = AgentRuntime(Settings(_env_file=None, gemini_api_key="", groq_api_key="x",
                               diag_enabled=False, fs_enabled=True))
    binned = []
    rt._trash = Trash(rt, send2trash=binned.append)
    q = rt.events.subscribe()
    events = lambda: [q.get_nowait() for _ in range(q.qsize())]
    return rt, binned, events


def file(name="old.txt") -> str:
    p = SANDBOX / name
    p.write_text("x")
    return str(p)


def test_a_request_only_asks():
    rt, binned, events = setup()
    req = rt.trash.request([file()])
    assert binned == []
    ev = events()[-1]
    assert ev.kind == "fs_confirm" and ev.data["items"][0]["name"] == "old.txt" and ev.say is None


def test_the_model_cannot_confirm():
    rt, binned, _ = setup()
    req = rt.trash.request([file()])
    assert "refused" in rt.trash.confirm(req.id, "model")
    assert binned == [] and req.id in rt.trash.pending


def test_the_user_confirms_from_the_panel():
    rt, binned, events = setup()
    req = rt.trash.request([file("a.txt"), file("b.txt")])
    out = rt.trash.confirm(req.id, "ui")
    assert out == "moved 2 to the Recycle Bin", out
    assert [Path(p).name for p in binned] == ["a.txt", "b.txt"]
    done = events()[-1]
    assert done.kind == "fs_confirm_done" and done.data["ok"] and done.say is None
    assert "expired" in rt.trash.confirm(req.id, "ui"), "a yes only works once"


def test_limits():
    rt, _, _ = setup()
    for bad in ([], [file()] * 21, ["C:\\"], ["C:\\Windows\\notepad.exe"], [str(SANDBOX / "ghost.txt")]):
        try:
            rt.trash.request(bad)
            raise AssertionError(f"should refuse {bad[:1]}")
        except Refused:
            pass


def test_requests_expire():
    rt, binned, _ = setup()
    req = rt.trash.request([file()])
    req.created -= trash_mod.TTL_S + 1
    assert "expired" in rt.trash.confirm(req.id, "ui")
    assert binned == []


def test_yes_and_no_words():
    for t in ["yes", "Yes please", "haan kar do", "okay do it", "go ahead", "हाँ", "हां कर दो"]:
        assert said_yes(t), t
    for t in ["no", "no wait", "don't", "yes, no wait", "nahi", "cancel that", "नहीं", "ha ha", "haha"]:
        assert not said_yes(t), t
    assert said_no("no keep it") and not said_no("yes")


def test_fs_delete_tool_only_asks():
    async def go():
        rt, binned, events = setup()
        reg = build_registry(rt.settings, rt)
        out = (await reg.execute("fs_delete", {"paths": [file()]})).output
        assert "waiting for the user's yes" in out, out
        assert binned == [] and events()[-1].kind == "fs_confirm"
    run(go())


# -- through a session: the panel's buttons and the user's voice --------------------

class FakeWs:
    from starlette.websockets import WebSocketState
    client_state = WebSocketState.CONNECTED

    def __init__(self):
        self.sent = []

    async def send_json(self, m):
        self.sent.append(m)


def session(rt):
    from backend.domain.voice.session import Session

    return Session(FakeWs(), rt.settings, rt)


def test_panel_buttons_over_the_socket():
    async def go():
        rt, binned, _ = setup()
        s = session(rt)
        req = rt.trash.request([file()])
        await s._on_control(json.dumps({"t": "holo_confirm", "id": req.id, "ok": False}))
        assert binned == [] and req.id not in rt.trash.pending, "No keeps the file"
        req = rt.trash.request([file()])
        await s._on_control(json.dumps({"t": "holo_confirm", "id": req.id, "ok": True}))
        assert len(binned) == 1
    run(go())


def test_spoken_yes_confirms_only_after_the_request():
    async def go():
        rt, binned, _ = setup()
        s = session(rt)
        # A "yes" in a turn that started before the question does not count.
        await s._brain_send({"t": "transcript", "role": "user", "text": "okay so", "turn_id": 1})
        req = rt.trash.request([file()])
        await s._brain_send({"t": "transcript", "role": "user", "text": " yes", "turn_id": 1})
        assert binned == [], "a yes from before the question"
        # The next turn: pieces join up, then the yes lands.
        time.sleep(0.01)
        await s._brain_send({"t": "transcript", "role": "user", "text": "ha", "turn_id": 2})
        assert binned == [], "a bare 'ha' is a laugh, not a yes"
        await s._brain_send({"t": "transcript", "role": "user", "text": "an", "turn_id": 2})
        assert len(binned) == 1, "haan should confirm"
        assert req.id not in rt.trash.pending
        # Jarvis's own words never confirm anything.
        req2 = rt.trash.request([file("c.txt")])
        time.sleep(0.01)
        await s._brain_send({"t": "transcript", "role": "assistant", "text": "yes", "turn_id": 3})
        await s._brain_send({"t": "assistant_delta", "delta": "Yes, deleting", "turn_id": 3})
        assert len(binned) == 1 and req2.id in rt.trash.pending
        # And a spoken no drops it.
        await s._brain_send({"t": "transcript", "role": "user", "text": "no, keep it", "turn_id": 4})
        assert req2.id not in rt.trash.pending and len(binned) == 1
    run(go())


if __name__ == "__main__":
    passed = failed = 0
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                try:
                    fn()
                    print(f"  PASS  {name}")
                    passed += 1
                except Exception as exc:
                    print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
                    failed += 1
    finally:
        shutil.rmtree(SANDBOX, ignore_errors=True)
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
