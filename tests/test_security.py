"""Who may talk to the server: Host, Origin and token checks (core/security.py).

Drives the real app with Starlette's TestClient, without starting it (no
lifespan, so no models load): every refusal happens before a Session exists.

Run: .venv/Scripts/python.exe tests/test_security.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.core import security
from backend.main import app, require_token

LOCAL = "http://127.0.0.1:8080"


def client(base: str = LOCAL) -> TestClient:
    return TestClient(app, base_url=base)


def test_origin_rules():
    host = "127.0.0.1:8080"
    assert security.origin_ok(None, host)  # not a browser
    assert security.origin_ok("http://127.0.0.1:8080", host)
    assert not security.origin_ok("http://evil.com", host)
    assert not security.origin_ok("http://127.0.0.1:3000", host)  # another local app
    assert not security.origin_ok("http://localhost:8080", host)  # not the same origin
    assert not security.origin_ok("null", host)  # sandboxed iframe, file://
    assert not security.origin_ok("http://127.0.0.1:8080", None)


def test_hostnames():
    names = security.allowed_hostnames("0.0.0.0", "jarvis.lan, Other")
    assert {"127.0.0.1", "localhost", "::1", "jarvis.lan", "other"} <= names
    assert "0.0.0.0" not in names
    assert security.hostname_of("[::1]:8080") == "::1"
    assert security.hostname_of("LOCALHOST:8080") == "localhost"


def test_known_host_is_served():
    r = client().get("/healthz")
    assert r.status_code == 200, r.status_code
    assert client("http://localhost:8080").get("/healthz").status_code == 200


def test_rebinding_host_is_refused():
    for base in ("http://evil.com:8080", "http://testserver"):
        r = client(base).get("/healthz")
        assert r.status_code == 403, (base, r.status_code)
        assert client(base).get("/").status_code == 403


def test_foreign_origin_websocket_is_refused():
    for origin in ("http://evil.com", "http://127.0.0.1:3000"):
        try:
            with client().websocket_connect("/ws", headers={"origin": origin}):
                pass
        except WebSocketDisconnect as exc:
            assert exc.code == 1008, exc.code
        else:
            raise AssertionError(f"{origin} was let in")


def test_rebinding_websocket_is_refused():
    try:
        with client("http://evil.com:8080").websocket_connect(
                "/ws", headers={"origin": "http://evil.com:8080"}):
            pass
    except WebSocketDisconnect as exc:
        assert exc.code == 1008, exc.code
    else:
        raise AssertionError("rebinding host was let in")


def test_token_required():
    mini = FastAPI()

    @mini.get("/secret", dependencies=[Depends(require_token)])
    async def secret():
        return {"ok": True}

    c = TestClient(mini)
    assert c.get("/secret").status_code == 401
    assert c.get("/secret", headers={"x-jarvis-token": "guess"}).status_code == 401
    assert c.get("/secret", headers={"x-jarvis-token": security.TOKEN}).status_code == 200
    assert c.get(f"/secret?token={security.TOKEN}").status_code == 200


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
