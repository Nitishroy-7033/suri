"""The diagnostics agent: watches the laptop and Jarvis, and says what it finds.

It is a separate agent with its own model (DIAGNOSTICS_AGENT__*, a small local
Ollama model by default), and it only ever produces text. Speaking stays in
one place, the voice session: an alert goes out as an Event with finished
`text`, and the voice brain reads it as written.

    every tick   SystemSampler.sample()  -> latest snapshot -> the Systems view
                 AlertRules.check()      -> phrase_alert()  -> Event(diag_alert)
    on request   report(question)        -> "CPU is idle, battery at 80%..."

If the model is not there (Ollama stopped, model not pulled), alerts and
reports fall back to fixed sentences: a warning must never be lost to a
missing model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import TYPE_CHECKING

from ...core.events import Event
from .alerts import Alert, AlertRules, Thresholds
from .metrics import TurnMetrics, log_ring
from .sampler import REPO_ROOT, Every, SystemSampler, dir_size_mb, psutil_missing

if TYPE_CHECKING:
    from ...core.runtime import AgentRuntime

log = logging.getLogger("jarvis.diag")

SYSTEM = (
    "You are the diagnostics module of Jarvis, a voice assistant, reporting on "
    "the user's laptop and on Jarvis itself. You get live readings as JSON. "
    "Answer in one or two short spoken sentences, in the calm, dry style of a "
    "butler AI, addressing the user as sir. Mention only what matters: "
    "anything abnormal first, otherwise a brief all-clear with the key numbers. "
    "Use only the readings given; never invent numbers. Say numbers the way "
    "they are spoken (\"85 percent\", \"72 degrees\"). No markdown, no lists, "
    "no emoji."
)

ALERT_PROMPT = (
    "Warn the user about this in one short spoken sentence, and suggest one "
    "useful action if there is an obvious one (for example plugging in, or "
    "unloading a loaded local model when memory is tight):\n{facts}\n"
    "Context: {context}"
)

# A report runs inside a voice turn's tool call, so it must finish before
# the tool timeout (with room to spare) -- or the fixed sentence is used. An
# alert is not in anyone's way and can wait for a slow local model.
ALERT_TIMEOUT_S = 30.0


class DiagnosticsAgent:
    def __init__(self, runtime: "AgentRuntime") -> None:
        self.runtime = runtime
        self.settings = runtime.settings
        self.metrics = TurnMetrics()
        self.logs = log_ring()
        self.rules = AlertRules(Thresholds.from_settings(self.settings))
        self.sampler: SystemSampler | None = None
        self.latest: dict = {}
        self.alerts: deque[dict] = deque(maxlen=30)
        self.ollama: dict = {"reachable": False, "models": []}
        self.started = time.time()
        self._subs: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._jarvis_every = Every(5)
        self._storage_every = Every(60)
        self._storage: dict = {}
        self._jarvis: dict = {}
        self.missing = psutil_missing()

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        if self.missing:
            log.warning("diagnostics: %s - laptop stats off, Jarvis stats only", self.missing)
        else:
            self.sampler = await asyncio.to_thread(SystemSampler, self.settings.diag_ping_host)
        await self._refresh_ollama()
        self._task = self.runtime.spawn(self._loop())
        # A local model's first call pays its load time (15 s measured on a
        # CPU-only laptop); pay it now, not during the first status report.
        self.runtime.spawn(self._ask("Reply with the word ready.", 90.0, quiet=True))

    @property
    def report_timeout_s(self) -> float:
        return max(3.0, min(12.0, self.settings.tool_timeout_s - 3.0))

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
        if self.sampler is not None:
            self.sampler.close()

    async def _loop(self) -> None:
        interval = max(0.5, self.settings.diag_interval_s)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("diagnostics tick failed")
            await asyncio.sleep(interval)

    async def tick(self) -> dict:
        snap = await asyncio.to_thread(self.sampler.sample) if self.sampler else {"ts": time.time()}
        now = time.monotonic()
        if self._jarvis_every(now):
            await self._refresh_ollama()
            self._jarvis = await self._jarvis_section(now)
        snap["jarvis"] = self._jarvis
        snap["alerts"] = list(self.alerts)[-10:]
        self.latest = snap
        for q in list(self._subs):
            _offer(q, snap)
        if self.settings.diag_alerts:
            for alert in self.rules.check(snap):
                self.runtime.spawn(self._raise(alert))
        return snap

    # -- the Systems view's feed ---------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._subs.add(q)
        if self.latest:
            _offer(q, self.latest)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    # -- Jarvis's own health ----------------------------------------------------

    async def _refresh_ollama(self) -> None:
        from ...core.models.providers.ollama import ollama_ps

        self.ollama = await ollama_ps(self.runtime.models.http, self.settings.ollama_host)

    async def _jarvis_section(self, now: float) -> dict:
        rt = self.runtime
        if self._storage_every(now):
            self._storage = await asyncio.to_thread(self._storage_section)
        web = "off"
        if self.settings.web_agent_enabled:
            web = "idle"
            wa = rt._web_agent
            if wa is not None and wa.task is not None:
                state = wa.task.state
                web = "busy" if state in ("working", "waiting") else "failed" if state == "failed" else "idle"
        return {
            "models": rt.models.describe(),
            "ollama": self.ollama,
            "agents": {"web": web, "jobs": len(rt.jobs),
                       "camera": rt.camera is not None,
                       "motion": bool(rt.motion is not None and getattr(rt.motion, "running", False))},
            "voice": self.metrics.summary(),
            "storage": {**self._storage, "memory_facts": len(rt.memory)},
            "logs": self.logs.summary(),
            "uptime_s": round(time.time() - self.started),
        }

    def _storage_section(self) -> dict:
        mem = self.runtime.memory.path
        out = {"memory_kb": None, "memory_mtime": None}
        try:
            st = mem.stat()
            out["memory_kb"] = round(st.st_size / 1024, 1)
            out["memory_mtime"] = st.st_mtime
        except OSError:
            pass
        debug = REPO_ROOT / "debug"
        out["debug_mb"] = dir_size_mb(debug) if debug.exists() else 0.0
        profile = REPO_ROOT / self.settings.browser_profile_dir
        out["browser_profile_mb"] = dir_size_mb(profile) if profile.exists() else 0.0
        return out

    # -- words: reports and alerts -----------------------------------------------

    def compact(self, snap: dict | None = None) -> dict:
        """The readings a small model can take in at a glance."""
        s = snap or self.latest or {}
        cpu, ram, gpu = s.get("cpu") or {}, s.get("ram") or {}, s.get("gpu") or {}
        bat, disk, net = s.get("battery"), s.get("disk") or {}, s.get("net") or {}
        j = s.get("jarvis") or {}
        out: dict = {
            "cpu_pct": cpu.get("pct"), "cpu_temp_c": cpu.get("temp_c"),
            "ram_pct": ram.get("pct"), "ram_used_gb": ram.get("used_gb"), "ram_total_gb": ram.get("total_gb"),
            "disk_free_gb": disk.get("free_gb"),
            "online": net.get("online"), "ping_ms": net.get("ping_ms"),
        }
        if gpu:
            out["gpu"] = {k: gpu.get(k) for k in ("name", "pct", "vram_used_mb", "vram_total_mb", "temp_c")}
        if bat:
            out["battery"] = bat
        procs = (s.get("procs") or {})
        if procs.get("by_cpu"):
            out["top_cpu"] = [f"{p['name']} {p['cpu']}%" for p in procs["by_cpu"][:3]]
        if procs.get("by_ram"):
            out["top_ram"] = [f"{p['name']} {p['ram_mb']} MB" for p in procs["by_ram"][:3]]
        if j:
            out["jarvis"] = {
                "models": {m["agent"]: {"model": m["active"], "ok": not m["unavailable"],
                                        "tok_per_s": m["avg_tok_per_s"], "errors": m["errors"]}
                           for m in j.get("models", [])},
                "ollama_loaded": [m["name"] for m in (j.get("ollama") or {}).get("models", [])],
                "web_agent": (j.get("agents") or {}).get("web"),
                "errors_logged": (j.get("logs") or {}).get("errors"),
                "reply_latency_ms": (j.get("voice") or {}).get("avg_first_audio_ms"),
            }
        return out

    async def report(self, question: str = "") -> str:
        data = self.compact()
        prompt = (f"Readings: {json.dumps(data, separators=(',', ':'))}\n"
                  f"The user asked: {question.strip() or 'status report'}")
        text = await self._ask(prompt, self.report_timeout_s)
        return text or self._report_template(data)

    async def phrase_alert(self, alert: Alert) -> str:
        loaded = [m["name"] for m in self.ollama.get("models", [])]
        context = f"local models loaded in memory: {', '.join(loaded)}" if loaded else "no local models loaded"
        text = await self._ask(ALERT_PROMPT.format(facts=alert.facts, context=context), ALERT_TIMEOUT_S)
        return text or alert.template

    async def _ask(self, prompt: str, timeout: float, quiet: bool = False) -> str:
        try:
            model = self.runtime.models.get("diagnostics_agent")
            result = await asyncio.wait_for(model.complete(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                max_tokens=90), timeout)
            return result.text.strip().strip('"')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                self.runtime.models.record_error(
                    "diagnostics_agent", f"no answer in {timeout:.0f} s")
            if not quiet:
                log.warning("diagnostics model unavailable (%s); using a fixed sentence",
                            str(exc)[:120] or type(exc).__name__)
            return ""

    async def _raise(self, alert: Alert) -> None:
        text = await self.phrase_alert(alert)
        entry = {**alert.as_dict(), "text": text}
        self.alerts.append(entry)
        self.runtime.events.publish(Event(kind="diag_alert", text=text, data=entry))

    @staticmethod
    def _report_template(d: dict) -> str:
        parts = []
        if d.get("cpu_pct") is not None:
            parts.append(f"CPU at {d['cpu_pct']:.0f} percent")
        if d.get("ram_pct") is not None:
            parts.append(f"memory at {d['ram_pct']:.0f} percent")
        gpu = d.get("gpu") or {}
        if gpu.get("pct") is not None:
            parts.append(f"GPU at {gpu['pct']} percent")
        bat = d.get("battery")
        if bat:
            parts.append(f"battery {bat['pct']} percent{' and charging' if bat.get('plugged') else ''}")
        if not parts:
            return "I can't read the system sensors right now, sir."
        online = "" if d.get("online") is not False else " We are offline."
        return "All systems running, sir: " + ", ".join(parts) + "." + online


def _offer(q: asyncio.Queue, item) -> None:
    """Latest wins: a viewer that fell behind gets the newest snapshot."""
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        pass
