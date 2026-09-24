"""Reads the laptop's vital signs. No model, no network beyond a ping.

Everything here is blocking (psutil, NVML, typeperf) and runs in a worker
thread; the agent calls `sample()` every couple of seconds and keeps the
result. Expensive readings run on slower clocks of their own:

    every tick   CPU, RAM, battery, disk and network rates, NVIDIA GPU
    5 s          top processes
    10 s         ping; non-NVIDIA GPU load via Windows performance counters

Anything the machine can't report comes back as None, never an exception:
Windows gives psutil no CPU temperature, and only NVIDIA exposes GPU
temperature through a free API.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("jarvis.diag")

REPO_ROOT = Path(__file__).resolve().parents[3]


def psutil_missing() -> str | None:
    try:
        import psutil  # noqa: F401
    except ImportError:
        return "psutil is not installed (pip install psutil)"
    return None


class Every:
    """True once per `seconds`; the first call is always True."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._next = 0.0

    def __call__(self, now: float) -> bool:
        if now >= self._next:
            self._next = now + self.seconds
            return True
        return False


class SystemSampler:
    def __init__(self, ping_host: str = "1.1.1.1") -> None:
        import psutil

        self.psutil = psutil
        self.ping_host = ping_host
        self._last_t: float | None = None
        self._last_disk = None
        self._last_net = None
        self._procs_every = Every(5)
        self._ping_every = Every(10)
        self._procs: dict = {"by_cpu": [], "by_ram": []}
        self._net_status: dict = {"online": None, "ping_ms": None}
        self._primed: set[int] = set()
        self.disk_path = str(REPO_ROOT.anchor or REPO_ROOT)
        self.gpu = GpuReader()
        psutil.cpu_percent(percpu=True)  # the first reading is always 0

    # -- the whole snapshot ---------------------------------------------------

    def sample(self) -> dict:
        now = time.monotonic()
        dt = (now - self._last_t) if self._last_t else None
        self._last_t = now
        snap = {
            "ts": time.time(),
            "cpu": self._cpu(),
            "ram": self._ram(),
            "gpu": self.gpu.read(),
            "battery": self._battery(),
            "disk": self._disk(dt),
            "net": self._net(dt, now),
            "procs": self._top_procs(now),
        }
        return snap

    # -- sections ---------------------------------------------------------------

    def _cpu(self) -> dict:
        ps = self.psutil
        cores = ps.cpu_percent(percpu=True)
        freq = None
        try:
            f = ps.cpu_freq()
            freq = round(f.current) if f else None
        except (OSError, NotImplementedError):
            pass
        return {"pct": round(sum(cores) / len(cores), 1) if cores else 0.0,
                "cores": [round(c) for c in cores],
                "freq_mhz": freq, "temp_c": _cpu_temp(ps),
                "count": ps.cpu_count(logical=True)}

    def _ram(self) -> dict:
        vm = self.psutil.virtual_memory()
        sw = self.psutil.swap_memory()
        return {"pct": vm.percent, "used_gb": _gb(vm.total - vm.available),
                "total_gb": _gb(vm.total), "swap_pct": sw.percent}

    def _battery(self) -> dict | None:
        try:
            b = self.psutil.sensors_battery()
        except (OSError, NotImplementedError, AttributeError):
            return None
        if b is None:
            return None  # a desktop, or no battery driver
        secs = b.secsleft if isinstance(b.secsleft, int) and b.secsleft >= 0 else None
        return {"pct": round(b.percent), "plugged": bool(b.power_plugged), "secs_left": secs}

    def _disk(self, dt: float | None) -> dict:
        ps = self.psutil
        u = ps.disk_usage(self.disk_path)
        out = {"path": self.disk_path, "pct": u.percent, "free_gb": _gb(u.free),
               "total_gb": _gb(u.total), "read_mb_s": None, "write_mb_s": None}
        try:
            io_now = ps.disk_io_counters()
        except (OSError, RuntimeError):
            io_now = None
        if io_now and self._last_disk and dt:
            out["read_mb_s"] = round((io_now.read_bytes - self._last_disk.read_bytes) / dt / 2**20, 2)
            out["write_mb_s"] = round((io_now.write_bytes - self._last_disk.write_bytes) / dt / 2**20, 2)
        self._last_disk = io_now
        return out

    def _net(self, dt: float | None, now: float) -> dict:
        n = self.psutil.net_io_counters()
        out = {"up_kb_s": None, "down_kb_s": None, **self._net_status}
        if self._last_net and dt:
            out["up_kb_s"] = round((n.bytes_sent - self._last_net.bytes_sent) / dt / 1024, 1)
            out["down_kb_s"] = round((n.bytes_recv - self._last_net.bytes_recv) / dt / 1024, 1)
        self._last_net = n
        if self._ping_every(now):
            ms = tcp_ping(self.ping_host)
            self._net_status = {"online": ms is not None, "ping_ms": ms}
            out.update(self._net_status)
        return out

    def _top_procs(self, now: float) -> dict:
        if not self._procs_every(now):
            return self._procs
        ps = self.psutil
        rows = []
        ncpu = ps.cpu_count(logical=True) or 1
        for p in ps.process_iter(["pid", "name", "memory_info"]):
            try:
                cpu = p.cpu_percent(None)  # since the last call for this pid
            except (ps.NoSuchProcess, ps.AccessDenied, ps.ZombieProcess):
                continue
            info = p.info
            if info["pid"] == 0 or not info.get("memory_info"):
                continue  # "System Idle Process"
            first = info["pid"] not in self._primed
            self._primed.add(info["pid"])
            rows.append({"pid": info["pid"], "name": info.get("name") or "?",
                         "cpu": None if first else round(cpu / ncpu, 1),
                         "ram_mb": round(info["memory_info"].rss / 2**20)})
        by_cpu = sorted((r for r in rows if r["cpu"] is not None), key=lambda r: r["cpu"], reverse=True)
        by_ram = sorted(rows, key=lambda r: r["ram_mb"], reverse=True)
        self._procs = {"by_cpu": by_cpu[:5], "by_ram": by_ram[:5]}
        return self._procs

    def close(self) -> None:
        self.gpu.close()


# -- GPU --------------------------------------------------------------------------

class GpuReader:
    """NVIDIA through NVML when present; otherwise Windows' own GPU counters
    (any vendor: load and VRAM in use, no temperature)."""

    def __init__(self) -> None:
        self._nvml = None
        self._handle = None
        self._name = None
        self._win: dict | None = None
        self._win_lock = threading.Lock()
        self._win_thread: threading.Thread | None = None
        self._win_every = Every(10)
        try:
            import pynvml

            pynvml.nvmlInit()
            if pynvml.nvmlDeviceGetCount() > 0:
                self._nvml = pynvml
                self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                name = pynvml.nvmlDeviceGetName(self._handle)
                self._name = name.decode() if isinstance(name, bytes) else name
                log.info("gpu: %s via NVML", self._name)
        except Exception as exc:  # ImportError, or NVMLError: no NVIDIA driver
            log.info("gpu: NVML unavailable (%s)%s", type(exc).__name__,
                     "; using Windows GPU counters" if sys.platform == "win32" else "")

    def read(self) -> dict | None:
        if self._nvml is not None:
            return self._read_nvml()
        if sys.platform == "win32":
            return self._read_windows()
        return None

    def _read_nvml(self) -> dict | None:
        nv, h = self._nvml, self._handle
        out = {"name": self._name, "source": "nvml", "pct": None, "vram_used_mb": None,
               "vram_total_mb": None, "temp_c": None, "power_w": None}
        try:
            out["pct"] = nv.nvmlDeviceGetUtilizationRates(h).gpu
            mem = nv.nvmlDeviceGetMemoryInfo(h)
            out["vram_used_mb"] = round(mem.used / 2**20)
            out["vram_total_mb"] = round(mem.total / 2**20)
            out["temp_c"] = nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU)
            out["power_w"] = round(nv.nvmlDeviceGetPowerUsage(h) / 1000, 1)
        except Exception as exc:  # NVMLError_NotSupported on some laptops
            log.debug("nvml read failed: %s", exc)
        return out

    def _read_windows(self) -> dict | None:
        # typeperf takes seconds, so it runs in its own thread and the tick
        # reports whatever it measured last.
        if self._win_every(time.monotonic()) and not (self._win_thread and self._win_thread.is_alive()):
            self._win_thread = threading.Thread(target=self._poll_windows, daemon=True)
            self._win_thread.start()
        with self._win_lock:
            return dict(self._win) if self._win else None

    def _poll_windows(self) -> None:
        if self._name is None:
            self._name = _windows_gpu_name() or "GPU"
        try:
            out = subprocess.run(
                ["typeperf", r"\GPU Engine(*engtype_3D)\Utilization Percentage",
                 r"\GPU Adapter Memory(*)\Dedicated Usage", "-sc", "1"],
                capture_output=True, text=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            rows = [r for r in csv.reader(io.StringIO(out.stdout)) if len(r) > 1]
            if len(rows) < 2:
                return
            header, values = rows[0], rows[1]
            util = 0.0
            vram: list[float] = []
            for name, val in zip(header[1:], values[1:]):
                try:
                    v = float(val)
                except ValueError:
                    continue
                if "Utilization Percentage" in name:
                    util += v
                elif "Dedicated Usage" in name:
                    vram.append(v)
            data = {"name": self._name, "source": "windows", "pct": round(min(util, 100.0)),
                    "vram_used_mb": round(max(vram) / 2**20) if vram else None,
                    "vram_total_mb": None, "temp_c": None, "power_w": None}
            with self._win_lock:
                self._win = data
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("typeperf failed: %s", exc)

    def close(self) -> None:
        if self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass


def _windows_gpu_name() -> str | None:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# -- helpers ----------------------------------------------------------------------

def _cpu_temp(ps) -> float | None:
    fn = getattr(ps, "sensors_temperatures", None)
    if fn is None:
        return None  # Windows: psutil has no temperature sensors
    try:
        temps = fn()
    except (OSError, NotImplementedError):
        return None
    for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
        if temps.get(key):
            return round(max(t.current for t in temps[key]), 1)
    return None


def tcp_ping(host: str, port: int = 443, timeout: float = 2.0) -> float | None:
    """Round trip of a TCP connect, in ms. ICMP needs admin on Windows."""
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return round((time.perf_counter() - t0) * 1000, 1)
    except OSError:
        return None


def dir_size_mb(path: Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return round(total / 2**20, 1)


def _gb(n: int) -> float:
    return round(n / 2**30, 1)
