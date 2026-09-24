"""The diagnostics agent: alert rules, the sampler, the log ring, and how
alerts and reports reach the voice.

No network and no model: the diagnostics model is faked, and the sampler test
reads this machine's real sensors (psutil) without asserting their values.

Run: .venv/Scripts/python.exe tests/test_diagnostics.py
"""

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import Settings
from backend.core.models.base import ChatResult
from backend.core.runtime import AgentRuntime
from backend.core.tools.catalog import build_registry
from backend.domain.diagnostics.agent import DiagnosticsAgent
from backend.domain.diagnostics.alerts import Alert, AlertRules, Thresholds
from backend.domain.diagnostics.metrics import LogRing
from backend.domain.memory.store import MemoryStore


def run(coro):
    return asyncio.run(coro)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def rules(**kw):
    clock = Clock()
    return AlertRules(Thresholds(**kw), clock), clock


def bat(pct, plugged=False):
    return {"battery": {"pct": pct, "plugged": plugged, "secs_left": 2400}}


# -- alert rules ------------------------------------------------------------------

def test_battery_low_fires_once_then_waits_for_recovery_and_cooldown():
    r, clock = rules(cooldown_s=600)
    first = r.check(bat(18))
    assert [a.rule for a in first] == ["battery_low"] and "40 minutes" in first[0].facts
    assert r.check(bat(17)) == []  # still low: no repeat
    clock.t += 700
    assert r.check(bat(16)) == []  # cooldown passed, but it never recovered
    r.check(bat(30))  # recovered -> re-armed
    assert [a.rule for a in r.check(bat(19))] == ["battery_low"]


def test_battery_ignored_while_plugged_in():
    r, _ = rules()
    assert r.check(bat(5, plugged=True)) == []


def test_battery_critical_is_its_own_rule():
    r, _ = rules()
    assert [a.rule for a in r.check(bat(18))] == ["battery_low"]
    crit = r.check(bat(8))
    assert [(a.rule, a.level) for a in crit] == [("battery_critical", "critical")]


def test_ram_needs_consecutive_samples():
    r, _ = rules(ram_samples=3, ram_high_pct=90)
    snap = {"ram": {"pct": 95}}
    assert r.check(snap) == [] and r.check(snap) == []
    assert [a.rule for a in r.check(snap)] == ["ram_high"]
    r2, _ = rules(ram_samples=3)
    r2.check(snap); r2.check({"ram": {"pct": 50}}); r2.check(snap)
    assert r2.check({"ram": {"pct": 50}}) == []


def test_missing_sensors_are_ignored():
    r, _ = rules()
    assert r.check({"cpu": {"pct": 99, "temp_c": None}, "gpu": None, "battery": None}) == []


def test_gpu_hot_and_vram_full():
    r, _ = rules(temp_hot_c=85)
    out = r.check({"gpu": {"temp_c": 91, "vram_used_mb": 7900, "vram_total_mb": 8192}})
    assert {a.rule for a in out} == {"gpu_hot", "vram_full"}
    assert "Throttling" in next(a for a in out if a.rule == "gpu_hot").template


def test_network_drop_and_return():
    r, _ = rules()
    net = lambda on: {"net": {"online": on}}
    assert r.check(net(True)) == []
    assert [a.rule for a in r.check(net(False))] == ["net_offline"]
    assert [a.rule for a in r.check(net(True))] == ["net_online"]


# -- sampler and log ring ------------------------------------------------------------

def test_sampler_returns_every_section():
    from backend.domain.diagnostics.sampler import SystemSampler

    s = SystemSampler(ping_host="127.0.0.1")
    s.sample()
    snap = s.sample()
    s.close()
    for key in ("cpu", "ram", "gpu", "battery", "disk", "net", "procs"):
        assert key in snap, key
    assert 0 <= snap["cpu"]["pct"] <= 100 and snap["ram"]["total_gb"] > 0
    assert snap["disk"]["free_gb"] >= 0


def test_log_ring_counts_warnings_and_errors():
    ring = LogRing(keep=3)
    log = logging.getLogger("jarvis.test.ring")
    log.addHandler(ring)
    log.propagate = False
    try:
        log.info("ignored")
        log.warning("w1")
        for i in range(3):
            log.error("e%d", i)
    finally:
        log.removeHandler(ring)
    s = ring.summary()
    assert s["warnings"] == 1 and s["errors"] == 3
    assert [r["msg"] for r in s["recent"]] == ["e0", "e1", "e2"]


# -- the agent -------------------------------------------------------------------------

class FakeModel:
    def __init__(self, text="Battery at 15 percent, sir.", fail=False):
        self.text, self.fail, self.prompts = text, fail, []
        self.name, self.model = "fake", "fake"
        self.active = self  # FallbackModel's surface, as the hub reads it

    def describe(self):
        return "fake:fake"

    def unavailable(self):
        return None

    async def complete(self, messages, tools=None, **kw):
        self.prompts.append(messages[-1]["content"])
        if self.fail:
            raise RuntimeError("ollama not running")
        return ChatResult(self.text)


def runtime(**kw):
    s = Settings(_env_file=None, gemini_api_key="", groq_api_key="x", camera_enabled=False, **kw)
    rt = AgentRuntime(s)
    rt.memory = MemoryStore(Path(tempfile.mkdtemp()) / "memory.json")
    return rt


def with_model(rt, model):
    rt.models._models[("diagnostics_agent", False)] = model
    return rt


ALERT = Alert("battery_low", "warn", 15, "Battery low at 15% and discharging.",
              "Battery at 15 percent, sir. You may want to plug in soon.")


def test_alert_is_phrased_by_the_diagnostics_model():
    rt = with_model(runtime(), FakeModel())
    agent = DiagnosticsAgent(rt)
    assert run(agent.phrase_alert(ALERT)) == "Battery at 15 percent, sir."


def test_alert_falls_back_to_template_when_model_fails():
    rt = with_model(runtime(), FakeModel(fail=True))
    agent = DiagnosticsAgent(rt)
    assert run(agent.phrase_alert(ALERT)) == ALERT.template


def test_alert_publishes_finished_text_for_the_voice():
    async def go():
        rt = with_model(runtime(), FakeModel())
        q = rt.events.subscribe()
        agent = DiagnosticsAgent(rt)
        await agent._raise(ALERT)
        return q.get_nowait(), agent

    event, agent = run(go())
    assert event.kind == "diag_alert" and event.text == "Battery at 15 percent, sir."
    assert event.say is None  # the voice must not reword it
    assert agent.alerts[-1]["rule"] == "battery_low"


def test_tick_raises_alerts_from_the_rules():
    async def go():
        rt = with_model(runtime(diag_battery_low=101), FakeModel())
        q = rt.events.subscribe()
        agent = DiagnosticsAgent(rt)

        class FixedSampler:
            def sample(self):
                return {"ts": 1.0, "battery": {"pct": 50, "plugged": False, "secs_left": None}}

        agent.sampler = FixedSampler()
        agent._refresh_ollama = _noop
        feed = agent.subscribe()
        snap = await agent.tick()
        await asyncio.sleep(0.05)  # the alert is phrased in a spawned task
        return snap, feed.get_nowait(), q.get_nowait()

    snap, fed, event = run(go())
    assert fed is snap and "jarvis" in snap
    assert event.kind == "diag_alert"


async def _noop():
    return None


def test_system_status_tool_returns_the_report():
    async def go():
        rt = with_model(runtime(), FakeModel("All systems nominal, sir."))
        rt.diag = DiagnosticsAgent(rt)
        rt.diag.latest = {"cpu": {"pct": 12}, "ram": {"pct": 40}}
        reg = build_registry(rt.settings, rt, "pipeline")
        assert "system_status" in reg.names
        return await reg.execute("system_status", {"question": "how's my laptop?"}), rt

    outcome, rt = run(go())
    assert outcome.ok and outcome.output == "All systems nominal, sir."
    prompt = rt.models._models[("diagnostics_agent", False)].prompts[-1]
    assert '"cpu_pct":12' in prompt and "how's my laptop?" in prompt


def test_report_template_when_model_is_down():
    rt = with_model(runtime(), FakeModel(fail=True))
    agent = DiagnosticsAgent(rt)
    agent.latest = {"cpu": {"pct": 12}, "ram": {"pct": 40},
                    "battery": {"pct": 80, "plugged": True}}
    text = run(agent.report())
    assert "CPU at 12 percent" in text and "battery 80 percent and charging" in text


def test_tools_hidden_when_diagnostics_off():
    rt = runtime()
    reg = build_registry(rt.settings, rt, "pipeline")
    assert "system_status" not in reg.names and "unload_model" not in reg.names


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
