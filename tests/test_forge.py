"""The forge (domain/forge): finding and building 3D models in the background,
falling back between services, the paid-credit gate, and camera snapshots.

Providers are fakes; nothing is downloaded or generated.

Run: .venv/Scripts/python.exe tests/test_forge.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import Settings
from backend.core.runtime import AgentRuntime
from backend.core.tools.catalog import build_registry
from backend.domain.forge.service import Forge, ForgeError
from backend.domain.holo.catalog import ModelCatalog

GLB = b"glTF" + b"\x02\x00\x00\x00" + b"\x00" * 64


def run(coro):
    return asyncio.run(coro)


class FakeGen:
    def __init__(self, name, paid=False, result=GLB, image_ok=True, delay=0.0):
        self.name = self.label = name
        self.paid, self.result, self.image_ok, self.delay = paid, result, image_ok, delay
        self.cost_hint = "about 20 credits"
        self.license = "yours"
        self.calls = []

    def unavailable(self):
        return None

    def can(self, with_image):
        return self.image_ok or not with_image

    async def generate(self, prompt, image, progress):
        self.calls.append((prompt, image))
        progress(0.5, "halfway")
        await asyncio.sleep(self.delay)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeSearch:
    name = "polypizza"

    def __init__(self, hits):
        self.hits = hits

    def unavailable(self):
        return None

    async def search(self, q):
        return self.hits


def runtime(**kw):
    base = dict(gemini_api_key="", groq_api_key="x", diag_enabled=False, forge_sync_wait_s=0.05,
                forge_providers="local,tripo,meshy")
    base.update(kw)
    rt = AgentRuntime(Settings(_env_file=None, **base))
    rt.holo_models = ModelCatalog(user_dir=Path(tempfile.mkdtemp()) / "models")
    q = rt.events.subscribe()
    rt.drain = lambda: [q.get_nowait() for _ in range(q.qsize())]
    return rt


def forge(rt, *providers, downloads=None):
    f = Forge(rt, providers=list(providers))

    async def download(url):
        if downloads is None or url not in downloads:
            raise ForgeError("no such file")
        return downloads[url]
    f.download = download
    return f


def test_find_saves_and_shows():
    async def go():
        rt = runtime()
        rt.holo.update({"open": True})
        f = forge(rt, FakeSearch([{"title": "Quadcopter", "url": "u1", "credit": "Poly by Google", "license": "CC-BY"},
                                  {"title": "Racing drone", "url": "u2"}]), downloads={"u1": GLB})
        out = await f.find("a drone")
        assert out.startswith("found Quadcopter by Poly by Google"), out
        assert "Racing drone" in out
        entry = rt.holo_models.search("drone")
        assert entry and entry["license"] == "CC-BY"
        assert (rt.holo_models.user_dir / entry["src"].split("/")[-1]).read_bytes() == GLB
        assert [e.kind for e in rt.drain()] == ["holo_show"]
    run(go())


def test_paid_generation_needs_a_yes():
    async def go():
        rt = runtime()
        tripo = FakeGen("tripo", paid=True)
        f = forge(rt, tripo)
        out = await f.build("an arc reactor")
        assert out.startswith("confirm first") and "tripo" in out, out
        assert tripo.calls == [] and f.job is None
        await f.build("an arc reactor", confirmed=True)
        await f.job.task
        assert f.job.state == "done" and tripo.calls
    run(go())


def test_falls_back_but_never_to_paid_without_a_yes():
    async def go():
        rt = runtime()
        local = FakeGen("local", result=ForgeError("server down"))
        tripo = FakeGen("tripo", paid=True)
        f = forge(rt, local, tripo)
        await f.build("a helmet")  # local is free, so no confirmation asked...
        await f.job.task
        assert f.job.state == "failed" and "server down" in f.job.error
        assert tripo.calls == [], "...and the paid one must not run as a fallback"
        await f.build("a helmet", confirmed=True)
        await f.job.task
        assert f.job.state == "done" and f.job.provider == "tripo"
    run(go())


def test_done_is_announced_and_shown():
    async def go():
        rt = runtime()
        rt.holo.update({"open": True})
        f = forge(rt, FakeGen("local", delay=0.2))
        out = await f.build("a steampunk owl")
        assert "building" in out and "don't guess" in out, out
        await f.job.task
        kinds = [e.kind for e in rt.drain()]
        assert kinds[0] == "forge_start" and "forge_progress" in kinds and kinds[-1] == "forge_done", kinds
        assert "holo_show" in kinds
        done = [e for e in rt.drain()] or None
        entry = f.job.entry
        assert entry["name"] == "Steampunk owl" and entry["credit"] == "generated with local"
    run(go())


def test_forge_done_carries_a_say():
    async def go():
        rt = runtime()
        f = forge(rt, FakeGen("local"))
        await f.build("a cube")
        await f.job.task
        done = [e for e in rt.drain() if e.kind == "forge_done"][-1]
        assert done.say and "ready" in done.say and done.data["state"] == "done"
    run(go())


def test_not_a_glb_is_a_failure():
    async def go():
        rt = runtime()
        f = forge(rt, FakeGen("local", result=b"<html>error</html>"))
        await f.build("a thing")
        await f.job.task
        assert f.job.state == "failed" and "isn't a GLB" in f.job.error
    run(go())


def test_one_at_a_time_and_cancel():
    async def go():
        rt = runtime()
        f = forge(rt, FakeGen("local", delay=5))
        await f.build("slow thing")
        assert "already building" in await f.build("another")
        assert "stopped" in await f.cancel()
        assert f.job.state == "cancelled"
        assert "nothing is being built" in await f.cancel()
    run(go())


def test_image_requests_skip_text_only_services():
    async def go():
        rt = runtime()
        text_only = FakeGen("meshy", image_ok=False)
        f = forge(rt, text_only)
        out = await f.build("this", image=b"\xff\xd8\xff")
        assert "no an image-to-3D" not in out and "image-to-3D service" in out, out
    run(go())


def test_tools_appear_only_when_set_up():
    names = lambda **kw: build_registry(runtime(**kw).settings, runtime(**kw)).names
    assert not any(n.startswith("forge_") for n in names())
    with_search = names(poly_pizza_api_key="k")
    assert "forge_find" in with_search and "forge_build" not in with_search
    with_gen = names(tripo_api_key="k")
    assert "forge_build" in with_gen and "forge_job" in with_gen and "forge_find" not in with_gen
    assert "forge_build" in runtime(tripo_api_key="k").system_prompt()


def test_camera_snapshot_round_trip():
    async def go():
        rt = runtime()
        assert await rt.holo.snapshot(rt.events, timeout=0.1) is None, "workshop closed: no picture"
        rt.holo.update({"open": True})
        task = asyncio.create_task(rt.holo.snapshot(rt.events, timeout=1))
        await asyncio.sleep(0.01)
        req = [e for e in rt.drain() if e.kind == "holo_snapshot_request"][-1]
        assert not rt.holo.deliver("wrong-id", b"x")
        assert rt.holo.deliver(req.data["id"], b"\xff\xd8\xffJPEG")
        assert await task == b"\xff\xd8\xffJPEG"
        assert await rt.holo.snapshot(rt.events, timeout=0.05) is None, "nothing sent: times out"
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
