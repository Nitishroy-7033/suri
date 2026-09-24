"""The holographic workshop's backend: model library, tools, reported state,
and which tab its events reach.

No network, no browser: tools run against a real AgentRuntime with a temp
model library, and the page is a fake WebSocket.

Run: .venv/Scripts/python.exe tests/test_holo.py
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import Settings
from backend.core.events import Event
from backend.core.runtime import AgentRuntime
from backend.core.tools.catalog import build_registry
from backend.domain.holo.catalog import SEED, ModelCatalog


def run(coro):
    return asyncio.run(coro)


def settings(**kw) -> Settings:
    base = dict(gemini_api_key="", groq_api_key="x", camera_enabled=False, diag_enabled=False)
    base.update(kw)
    return Settings(_env_file=None, **base)


def runtime(**kw) -> AgentRuntime:
    rt = AgentRuntime(settings(**kw))
    rt.holo_models = ModelCatalog(user_dir=Path(tempfile.mkdtemp()) / "models")
    return rt


async def call(reg, name, args):
    """A tool's text, as the model would see it."""
    return (await reg.execute(name, args)).output


def events_of(rt):
    q = rt.events.subscribe()
    got = []

    def drain():
        while not q.empty():
            got.append(q.get_nowait())
        return got
    return drain


# -- the library ---------------------------------------------------------------

def test_seed_library_is_valid():
    data = json.loads(SEED.read_text(encoding="utf-8"))
    ids = [m["id"] for m in data["models"]]
    assert len(ids) == len(set(ids)), "duplicate ids"
    for m in data["models"]:
        assert m["src"].startswith("https://"), m
        assert m.get("license"), f"{m['id']} has no license note"


def test_search_matches_spoken_names():
    cat = ModelCatalog(user_dir=Path(tempfile.mkdtemp()))
    assert cat.search("the engine")["id"] == "engine"
    assert cat.search("show me the gear box model")["id"] == "gearbox"
    assert cat.search("dune buggy")["id"] == "buggy"
    assert cat.search("helmit")["id"] == "helmet"  # a mis-hearing
    assert cat.search("toaster") is None
    assert cat.search("") is None


def test_your_models_come_first_and_win_clashes():
    cat = ModelCatalog(user_dir=Path(tempfile.mkdtemp()))
    cat.add({"id": "engine", "name": "My V8", "aliases": ["engine", "v8"], "src": "/models/engine.glb"})
    assert cat.all()[0]["name"] == "My V8"
    assert cat.search("engine")["name"] == "My V8"
    assert len([m for m in cat.all() if m["id"] == "engine"]) == 1


# -- tools ---------------------------------------------------------------------

def test_holo_tools_offered_and_gated():
    names = build_registry(settings(), AgentRuntime(settings())).names
    for t in ("holo_show", "holo_view", "holo_highlight", "holo_panel", "holo_status"):
        assert t in names, t
    off = build_registry(settings(holo_enabled=False), AgentRuntime(settings()))
    assert not any(n.startswith("holo_") for n in off.names)


def test_show_publishes_the_entry():
    async def go():
        rt = runtime()
        drain = events_of(rt)
        reg = build_registry(rt.settings, rt)
        out = await call(reg, "holo_show", {"name": "the engine"})
        assert out.startswith("showing Two-cylinder engine"), out
        ev = drain()[-1]
        assert ev.kind == "holo_show" and ev.data["entry"]["id"] == "engine"
        assert ev.say is None  # shown, never spoken
        miss = await call(reg, "holo_show", {"name": "toaster"})
        assert "no model called" in miss and "Two-cylinder engine" in miss, miss
        assert "forge_find" not in miss  # nothing set up to find one
    run(go())


def test_view_rejects_unknown_actions():
    async def go():
        rt = runtime()
        drain = events_of(rt)
        reg = build_registry(rt.settings, rt)
        assert (await call(reg, "holo_view", {"action": "explode"})) == "done: explode"
        assert drain()[-1].data == {"action": "explode"}
        assert "error" in await call(reg, "holo_view", {"action": "self_destruct"})
    run(go())


def test_highlight_matches_reported_parts():
    async def go():
        rt = runtime()
        rt.holo.update({"open": True, "parts": ["Piston", "Spring Link", "Part 3"]})
        drain = events_of(rt)
        reg = build_registry(rt.settings, rt)
        assert await call(reg, "holo_highlight", {"part": "the piston"}) != ""
        assert drain()[-1].data["part"] == "Piston"
        await call(reg, "holo_highlight", {"part": "spring"})
        assert drain()[-1].data["part"] == "Spring Link"
        out = await call(reg, "holo_highlight", {"part": "flux capacitor"})
        assert "no part clearly named" in out and "Piston" in out, out
    run(go())


def test_panel_opens_known_panels_only():
    async def go():
        rt = runtime()
        drain = events_of(rt)
        reg = build_registry(rt.settings, rt)
        await call(reg, "holo_panel", {"action": "open", "target": "settings"})
        assert drain()[-1].data == {"action": "open", "target": "settings"}
        assert "error" in await call(reg, "holo_panel", {"action": "open", "target": "cockpit"})
        for target in ("guide", "tutorial"):
            await call(reg, "holo_panel", {"action": "open", "target": target})
            assert drain()[-1].data == {"action": "open", "target": target}
        # Files are off unless FS_ENABLED.
        assert "off" in await call(reg, "holo_panel", {"action": "open", "target": "folder:downloads"})
    run(go())


def test_status_reports_what_the_page_said():
    async def go():
        rt = runtime()
        reg = build_registry(rt.settings, rt)
        before = await call(reg, "holo_status", {})
        assert "not been opened" in before, before
        rt.holo.update({"open": True, "model": {"id": "engine", "name": "Two-cylinder engine"},
                        "parts": ["Piston"] * 100, "selected": "Piston", "hands": 2,
                        "panels": [{"id": "chat", "kind": "chat", "title": "Conversation", "junk": "x"}],
                        "evil": "ignored"})
        s = json.loads(await call(reg, "holo_status", {}))
        assert s["open"] and s["selected"] == "Piston" and s["hands"] == 2
        assert len(s["parts"]) == 40  # capped: this goes into a model's context
        assert "evil" not in s and "junk" not in s["panels"][0]
    run(go())


def test_prompt_mentions_the_workshop_only_when_on():
    assert "holographic workshop" in AgentRuntime(settings()).system_prompt()
    assert "holographic workshop" not in AgentRuntime(settings(holo_enabled=False)).system_prompt()


# -- events reach only the tab you are talking to -------------------------------

def test_holo_events_go_to_the_active_tab_only():
    from starlette.websockets import WebSocketState

    from backend.domain.voice.session import Session

    class FakeWs:
        client_state = WebSocketState.CONNECTED

        def __init__(self):
            self.sent = []

        async def send_json(self, m):
            self.sent.append(m)

    async def go():
        rt = runtime()
        a, b = FakeWs(), FakeWs()
        sa, sb = Session(a, rt.settings, rt), Session(b, rt.settings, rt)
        tasks = [asyncio.create_task(s._watch_events()) for s in (sa, sb)]
        await asyncio.sleep(0)
        rt.active_session = sa
        rt.events.publish(Event("holo_show", data={"entry": {"id": "engine"}}))
        rt.events.publish(Event("timer", data={}))
        await asyncio.sleep(0.02)
        kinds = lambda ws: [m["kind"] for m in ws.sent if m["t"] == "event"]
        assert kinds(a) == ["holo_show", "timer"], kinds(a)
        assert kinds(b) == ["timer"], kinds(b)  # the other tab still gets timers
        # And the page's reports land in HoloState.
        await sa._on_control(json.dumps({"t": "holo_state", "state": {"open": True, "selected": "Piston"}}))
        assert rt.holo.open and rt.holo.view["selected"] == "Piston"
        for t in tasks:
            t.cancel()

    run(go())


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception as exc:
                print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
