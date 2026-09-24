"""PC controls (domain/pc): volume, brightness, media keys, and the tools.

The Windows audio endpoint and the brightness library are faked, so this
never changes your real volume or screen.

Run: .venv/Scripts/python.exe tests/test_pc.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import Settings
from backend.core.runtime import AgentRuntime
from backend.core.tools.catalog import build_registry
from backend.domain.pc import controls


class FakeEndpoint:
    def __init__(self):
        self.level, self.muted = 0.5, 0

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, v, ctx):
        self.level = v

    def GetMute(self):
        return self.muted

    def SetMute(self, m, ctx):
        self.muted = m


class FakeSbc:
    def __init__(self):
        self.levels = [60, 30]

    def get_brightness(self):
        return list(self.levels)

    def set_brightness(self, v):
        self.levels = [v] * len(self.levels)


def fakes():
    ep, sbc = FakeEndpoint(), FakeSbc()
    controls._endpoint = lambda: ep
    controls._sbc = lambda: sbc
    controls.wifi = lambda: {"wifi": {"connected": True, "ssid": "home", "signal": 80}}
    return ep, sbc


def test_volume_is_clamped_and_unmutes_when_raised():
    ep, _ = fakes()
    assert controls.control("volume", value=150)["volume"] == 100
    assert controls.control("volume", change=-500)["volume"] == 0
    ep.muted = 1
    st = controls.control("volume", value=40)
    assert st == {"volume": 40, "muted": False}, st


def test_mute_toggles_and_sets():
    ep, _ = fakes()
    assert controls.control("mute", action="on")["muted"] is True
    assert controls.control("mute", action="toggle")["muted"] is False
    assert controls.control("volume", action="mute")["muted"] is True


def test_brightness_all_displays():
    _, sbc = fakes()
    st = controls.control("brightness", change=-80)
    assert st["brightness"] == 0 and sbc.levels == [0, 0], st
    assert controls.control("brightness", value="55")["brightness"] == 55


def test_bad_requests_are_errors_not_crashes():
    fakes()
    for bad in [("media", "reboot"), ("warp_drive", None)]:
        try:
            controls.control(bad[0], action=bad[1])
            raise AssertionError(bad)
        except ValueError:
            pass


def test_status_has_everything():
    fakes()
    st = controls.status()
    assert st["volume"] == 50 and st["brightness"] == 60 and st["displays"] == 2 and st["wifi"]["ssid"] == "home"


def test_tools_gated_and_working():
    fakes()
    s = Settings(_env_file=None, gemini_api_key="", groq_api_key="x", diag_enabled=False)
    names = build_registry(s, AgentRuntime(s)).names
    assert "pc_control" in names and "pc_status" in names
    off = Settings(_env_file=None, gemini_api_key="", groq_api_key="x", diag_enabled=False,
                   pc_controls_enabled=False)
    assert not any(n.startswith("pc_") for n in build_registry(off, AgentRuntime(off)).names)
    no_actions = Settings(_env_file=None, gemini_api_key="", groq_api_key="x", diag_enabled=False,
                          tools_allow_actions=False)
    names = build_registry(no_actions, AgentRuntime(no_actions)).names
    assert "pc_control" not in names and "pc_status" in names, "changing the PC is an action"

    async def go():
        rt = AgentRuntime(s)
        reg = build_registry(s, rt)
        out = json.loads((await reg.execute("pc_control", {"control": "volume", "value": 30})).output)
        assert out["volume"] == 30, out
        err = json.loads((await reg.execute("pc_control", {"control": "media", "action": "explode"})).output)
        assert "error" in err, err
    asyncio.run(go())


def test_http_route_needs_the_token():
    from starlette.testclient import TestClient

    from backend.core import security
    from backend.main import app

    fakes()
    c = TestClient(app, base_url="http://127.0.0.1:8080")
    assert c.get("/api/pc").status_code == 401
    r = c.post("/api/pc", json={"control": "volume", "value": 20}, headers={"x-jarvis-token": security.TOKEN})
    assert r.status_code == 200 and r.json()["volume"] == 20, r.text
    bad = c.post("/api/pc", json={"control": "nope"}, headers={"x-jarvis-token": security.TOKEN})
    assert bad.status_code == 400


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
