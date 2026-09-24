"""Threshold alerts: plain rules, no model, so they are instant and free.

Each rule fires once when its condition starts, then stays quiet until the
value has clearly recovered (hysteresis) *and* its cooldown has passed. A
battery hovering at 19-20% must not produce an alert every two seconds.

An Alert carries two phrasings: `facts` for the diagnostics model to turn into
one Jarvis-style sentence, and `template`, a finished sentence used when that
model is unavailable -- an alert must never be lost because Ollama is down.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Alert:
    rule: str
    level: str  # "info" | "warn" | "critical"
    value: float | None
    facts: str
    template: str
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {"rule": self.rule, "level": self.level, "value": self.value,
                "facts": self.facts, "ts": self.ts}


@dataclass
class _RuleState:
    armed: bool = True
    last_fired: float = float("-inf")
    streak: int = 0


@dataclass
class Thresholds:
    battery_low: float = 20.0
    battery_critical: float = 10.0
    temp_hot_c: float = 85.0
    ram_high_pct: float = 90.0
    disk_low_gb: float = 5.0
    vram_full_pct: float = 95.0
    cooldown_s: float = 600.0
    ram_samples: int = 3  # consecutive samples before RAM counts as "full"

    @classmethod
    def from_settings(cls, s) -> "Thresholds":
        return cls(battery_low=s.diag_battery_low, battery_critical=s.diag_battery_critical,
                   temp_hot_c=s.diag_temp_hot_c, ram_high_pct=s.diag_ram_high_pct,
                   disk_low_gb=s.diag_disk_low_gb, cooldown_s=s.diag_alert_cooldown_s)


class AlertRules:
    def __init__(self, t: Thresholds, clock: Callable[[], float] = time.monotonic) -> None:
        self.t = t
        self.clock = clock
        self._state: dict[str, _RuleState] = {}
        self._was_online: bool | None = None
        self._told_offline = False

    def _rule(self, name: str) -> _RuleState:
        return self._state.setdefault(name, _RuleState())

    def _trip(self, name: str, active: bool, recovered: bool,
              make: Callable[[], Alert], out: list[Alert]) -> None:
        st = self._rule(name)
        if recovered:
            st.armed = True
            return
        if not active or not st.armed:
            return
        now = self.clock()
        if now - st.last_fired < self.t.cooldown_s:
            return
        st.armed = False
        st.last_fired = now
        out.append(make())

    def check(self, snap: dict) -> list[Alert]:
        t = self.t
        out: list[Alert] = []

        b = snap.get("battery")
        if b and b.get("pct") is not None:
            pct, plugged = b["pct"], b.get("plugged", False)
            left = _minutes(b.get("secs_left"))
            left_txt = f", about {left} minutes left" if left else ""
            self._trip(
                "battery_critical", not plugged and pct <= t.battery_critical,
                plugged or pct >= t.battery_critical + 5,
                lambda: Alert("battery_critical", "critical", pct,
                              f"Battery critically low at {pct}% and discharging{left_txt}.",
                              f"Battery at {pct} percent, sir. Please plug in the charger now."), out)
            self._trip(
                "battery_low", not plugged and t.battery_critical < pct <= t.battery_low,
                plugged or pct >= t.battery_low + 5,
                lambda: Alert("battery_low", "warn", pct,
                              f"Battery low at {pct}% and discharging{left_txt}.",
                              f"Battery at {pct} percent, sir. You may want to plug in soon."), out)

        for key, label in (("gpu", "GPU"), ("cpu", "CPU")):
            temp = (snap.get(key) or {}).get("temp_c")
            if temp is None:
                continue
            self._trip(
                f"{key}_hot", temp >= t.temp_hot_c, temp < t.temp_hot_c - 5,
                lambda temp=temp, label=label, key=key: Alert(
                    f"{key}_hot", "warn", temp,
                    f"{label} temperature is {temp:.0f} degrees Celsius; thermal throttling is likely.",
                    f"{label} at {temp:.0f} degrees, sir. Throttling is likely."), out)

        ram = (snap.get("ram") or {}).get("pct")
        if ram is not None:
            st = self._rule("ram_high")
            st.streak = st.streak + 1 if ram >= t.ram_high_pct else 0
            self._trip(
                "ram_high", st.streak >= t.ram_samples, ram < t.ram_high_pct - 5,
                lambda: Alert("ram_high", "warn", ram,
                              f"RAM is {ram:.0f}% full.",
                              f"Memory is {ram:.0f} percent full, sir. Closing something heavy would help."), out)

        gpu = snap.get("gpu") or {}
        used, total = gpu.get("vram_used_mb"), gpu.get("vram_total_mb")
        if used is not None and total:
            vpct = used / total * 100
            self._trip(
                "vram_full", vpct >= t.vram_full_pct, vpct < t.vram_full_pct - 10,
                lambda: Alert("vram_full", "warn", round(vpct),
                              f"GPU memory is {vpct:.0f}% full ({used} of {total} MB).",
                              "GPU memory is nearly full, sir. Unloading a model would free it."), out)

        free = (snap.get("disk") or {}).get("free_gb")
        if free is not None:
            self._trip(
                "disk_low", free < t.disk_low_gb, free > t.disk_low_gb + 1,
                lambda: Alert("disk_low", "warn", free,
                              f"Only {free:.1f} GB of disk space left.",
                              f"Disk space is down to {free:.0f} gigabytes, sir."), out)

        online = (snap.get("net") or {}).get("online")
        if online is not None:
            if self._was_online and not online:
                self._trip("net_offline", True, False,
                           lambda: Alert("net_offline", "warn", None,
                                         "The internet connection just dropped; cloud models will not answer.",
                                         "We've lost the internet connection, sir."), out)
                self._told_offline = any(a.rule == "net_offline" for a in out)
            elif online and self._was_online is False:
                self._rule("net_offline").armed = True
                if self._told_offline:
                    self._told_offline = False
                    out.append(Alert("net_online", "info", None,
                                     "The internet connection is back.",
                                     "We're back online, sir."))
            self._was_online = online
        return out


def _minutes(secs) -> int | None:
    return round(secs / 60) if isinstance(secs, (int, float)) and secs > 0 else None
