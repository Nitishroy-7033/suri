"""The forge's service adapters (domain/forge/providers) against fake HTTP
servers: the request each one sends, how it polls, where it finds the model,
and how failures come out. Shapes follow each service's docs (Sept 2026).

No network: httpx.MockTransport answers every call.

Run: .venv/Scripts/python.exe tests/test_forge_providers.py
"""

import asyncio
import base64
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import Settings
from backend.core.runtime import AgentRuntime
from backend.domain.forge.providers.local import LocalServer
from backend.domain.forge.providers.meshy import Meshy
from backend.domain.forge.providers.polypizza import PolyPizza
from backend.domain.forge.providers.tripo import Tripo
from backend.domain.forge.service import ForgeError

GLB = b"glTF\x02\x00\x00\x00" + b"\x00" * 32


def run(coro):
    return asyncio.run(coro)


def rig(cls, handler, **kw):
    s = Settings(_env_file=None, gemini_api_key="g", groq_api_key="x", diag_enabled=False,
                 poly_pizza_api_key="pp", tripo_api_key="tk", meshy_api_key="mk",
                 forge_local_url="http://127.0.0.1:8081/", **kw)
    rt = AgentRuntime(s)
    rt.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = cls(s, rt)
    p.poll = staticmethod(lambda check, interval=0: _fast_poll(check))
    return p


async def _fast_poll(check):
    while True:
        out = await check()
        if out is not None:
            return out


def noprog(*a):
    pass


def test_poly_pizza_search():
    seen = {}

    def handler(req):
        seen["url"], seen["key"] = str(req.url), req.headers.get("x-auth-token")
        return httpx.Response(200, json={"total": 2, "results": [
            {"ID": "a", "Title": "Police Car", "Download": "https://static.poly.pizza/a.glb",
             "Attribution": "Police Car by Quaternius, https://poly.pizza/m/a. Licence at cc0",
             "Creator": {"Username": "Quaternius"}, "Licence": "CC0 1.0"},
            {"ID": "b", "Title": "No file"}]})

    hits = run(rig(PolyPizza, handler).search("police car"))
    assert seen["url"].startswith("https://api.poly.pizza/v1.1/search/police%20car") and seen["key"] == "pp", seen
    assert len(hits) == 1, "results without a download are skipped"
    h = hits[0]
    assert h["url"].endswith("a.glb") and h["creator"] == "Quaternius" and h["license"] == "CC0 1.0"
    assert "Quaternius" in h["credit"]


def test_tripo_text_flow():
    calls = []
    polls = iter([{"status": "queued", "progress": 0}, {"status": "running", "progress": 50},
                  {"status": "success", "progress": 100, "output": {"model_url": "https://cdn.tripo3d.ai/m.glb"}}])

    def handler(req):
        calls.append((req.method, req.url.path, req.headers.get("authorization")))
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["prompt"] == "an arc reactor" and body["texture"] is False and body["model"], body
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "t1"}})
        return httpx.Response(200, json={"code": 0, "data": {"task_id": "t1", **next(polls)}})

    got = run(rig(Tripo, handler).generate("an arc reactor", None, noprog))
    assert got == "https://cdn.tripo3d.ai/m.glb", got
    assert calls[0] == ("POST", "/v3/generation/text-to-model", "Bearer tk"), calls[0]
    assert all(c[1] == "/v3/tasks/t1" for c in calls[1:])


def test_tripo_image_uploads_first_and_maps_errors():
    def handler(req):
        if req.url.path == "/v3/files":
            assert b"snapshot.jpg" in req.content
            return httpx.Response(200, json={"code": 0, "data": {"file_token": "file_1"}})
        if req.url.path == "/v3/generation/image-to-model":
            assert json.loads(req.content)["input"] == "file_1"
            return httpx.Response(200, json={"code": 2010, "message": "Insufficient credits"})
        raise AssertionError(req.url)

    try:
        run(rig(Tripo, handler).generate("this", b"\xff\xd8\xff", noprog))
        raise AssertionError("should fail")
    except ForgeError as exc:
        assert "not enough Tripo credits" in str(exc), exc


def test_tripo_failed_task():
    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "t"}})
        return httpx.Response(200, json={"code": 0, "data": {"status": "banned"}})
    try:
        run(rig(Tripo, handler).generate("x", None, noprog))
        raise AssertionError("should fail")
    except ForgeError as exc:
        assert "banned" in str(exc)


def test_meshy_preview_only_and_image_data_uri():
    posts = []
    states = iter(["PENDING", "IN_PROGRESS", "SUCCEEDED"])

    def handler(req):
        assert req.headers["authorization"] == "Bearer mk"
        if req.method == "POST":
            posts.append((req.url.path, json.loads(req.content)))
            return httpx.Response(200, json={"result": "m1"})
        st = next(states)
        return httpx.Response(200, json={"status": st, "progress": 100 if st == "SUCCEEDED" else 30,
                                         "model_urls": {"glb": "https://assets.meshy.ai/m.glb"} if st == "SUCCEEDED" else {}})

    url = run(rig(Meshy, handler).generate("a monster mask", None, noprog))
    assert url.endswith("m.glb")
    path, body = posts[0]
    assert path == "/openapi/v2/text-to-3d" and body["mode"] == "preview", posts
    assert len(posts) == 1, "no refine stage: the hologram ignores textures"

    posts.clear()
    states = iter(["SUCCEEDED"])
    run(rig(Meshy, handler).generate("this", b"\xff\xd8\xffJPEG", noprog))
    path, body = posts[0]
    assert path == "/openapi/v1/image-to-3d" and body["image_url"].startswith("data:image/jpeg;base64,")


def test_meshy_refused_key():
    try:
        run(rig(Meshy, lambda req: httpx.Response(401, json={"message": "bad key"})).generate("x", None, noprog))
        raise AssertionError("should fail")
    except ForgeError as exc:
        assert "refused the API key" in str(exc)


def test_local_server_protocol():
    seen = {}
    states = iter([{"status": "processing"}, {"status": "texturing"},
                   {"status": "completed", "model_base64": base64.b64encode(GLB).decode()}])

    def handler(req):
        if req.method == "POST":
            assert str(req.url) == "http://127.0.0.1:8081/send"
            seen["image"] = json.loads(req.content)["image"]
            return httpx.Response(200, json={"uid": "u1"})
        assert req.url.path == "/status/u1"
        return httpx.Response(200, json=next(states))

    p = rig(LocalServer, handler)
    assert p.can(True) and not p.can(False), "text needs FORGE_IMAGE_MODEL"
    got = run(p.generate("this", b"\xff\xd8\xffJPEG", noprog))
    assert got == GLB
    assert base64.b64decode(seen["image"]) == b"\xff\xd8\xffJPEG" and not seen["image"].startswith("data:"), \
        "Hunyuan3D wants bare base64, not a data URI"
    assert rig(LocalServer, handler, forge_image_model="some-image-model").can(False)


def test_unreachable_server_is_a_forge_error():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)
    try:
        run(rig(LocalServer, handler).generate("x", b"\xff\xd8\xff", noprog))
        raise AssertionError("should fail")
    except ForgeError as exc:
        assert "can't be reached" in str(exc)


def test_availability_follows_keys():
    s = Settings(_env_file=None, gemini_api_key="", groq_api_key="x", diag_enabled=False)
    rt = AgentRuntime(s)
    for cls in (PolyPizza, Tripo, Meshy, LocalServer):
        assert cls(s, rt).unavailable(), cls.name
    assert Tripo.paid and Meshy.paid and not LocalServer.paid and not PolyPizza.paid


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
