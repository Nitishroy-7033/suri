"""Volume, brightness, media keys and Wi-Fi on this Windows machine.

The workshop's PC panel turns these directly (a {"t":"pc"} message, no model
involved), and Jarvis can too (pc_control). Battery, CPU, memory and
temperatures are not here: the diagnostics sampler already reads those and
the panel shows its snapshot.

Windows audio is COM, which wants every call on a thread that initialised
it -- so all of this runs on one worker thread of its own.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import ctypes
import logging
import re
import subprocess
import sys

log = logging.getLogger("jarvis.pc")

MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}
KEYEVENTF_KEYUP = 0x2


def unavailable() -> str | None:
    if sys.platform != "win32":
        return "PC controls only work on Windows"
    return None


def _com_init() -> None:
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:  # comtypes missing: volume reports it, the rest still works
        pass


_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pc", initializer=_com_init)


async def run(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_pool, fn, *args)


# -- volume ---------------------------------------------------------------------

def _endpoint():
    try:
        from pycaw.pycaw import AudioUtilities
    except ImportError:
        raise RuntimeError("volume control needs pycaw (pip install pycaw)") from None
    return AudioUtilities.GetSpeakers().EndpointVolume


def get_volume() -> dict:
    ep = _endpoint()
    return {"volume": round(ep.GetMasterVolumeLevelScalar() * 100), "muted": bool(ep.GetMute())}


def set_volume(level: float | None = None, change: float | None = None, mute: bool | None = None) -> dict:
    ep = _endpoint()
    if level is not None or change is not None:
        cur = ep.GetMasterVolumeLevelScalar() * 100
        target = level if level is not None else cur + change
        ep.SetMasterVolumeLevelScalar(max(0.0, min(100.0, float(target))) / 100, None)
        if mute is None and target > 0:
            ep.SetMute(0, None)  # turning it up means you want to hear it
    if mute is not None:
        ep.SetMute(1 if mute else 0, None)
    return get_volume()


# -- brightness -----------------------------------------------------------------

def _sbc():
    try:
        import screen_brightness_control as sbc
    except ImportError:
        raise RuntimeError("brightness needs screen-brightness-control") from None
    return sbc


def get_brightness() -> dict:
    try:
        levels = _sbc().get_brightness()
    except Exception as exc:  # no controllable display (a desktop monitor without DDC/CI)
        return {"brightness": None, "brightness_error": str(exc)[:120]}
    return {"brightness": levels[0] if levels else None, "displays": len(levels)}


def set_brightness(level: float | None = None, change: float | None = None) -> dict:
    sbc = _sbc()
    cur = (sbc.get_brightness() or [50])[0]
    target = round(max(0, min(100, level if level is not None else cur + (change or 0))))
    sbc.set_brightness(target)  # every display that can be dimmed
    return get_brightness()


# -- media keys -----------------------------------------------------------------

def media(action: str) -> dict:
    vk = MEDIA_KEYS.get(action)
    if vk is None:
        raise ValueError(f"media action must be one of {', '.join(MEDIA_KEYS)}")
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    return {"media": action}


# -- Wi-Fi ----------------------------------------------------------------------

def wifi() -> dict:
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True,
                             timeout=4, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {"wifi": None}
    field = lambda name: (re.search(rf"^\s*{name}\s*:\s*(.+)$", out, re.M | re.I) or [None, None])[1]
    state = (field("State") or "").strip().lower()
    if not state:
        return {"wifi": None}  # no wireless adapter
    return {"wifi": {"connected": state == "connected", "ssid": (field("SSID") or "").strip() or None,
                     "signal": int(re.sub(r"\D", "", field("Signal") or "") or 0) or None}}


# -- everything at once -----------------------------------------------------------

def status() -> dict:
    out: dict = {}
    try:
        out.update(get_volume())
    except Exception as exc:
        out["volume_error"] = str(exc)[:120]
    out.update(get_brightness())
    out.update(wifi())
    return out


def control(control: str, value=None, change=None, action: str | None = None) -> dict:
    """One change, from the panel or a tool. Returns the new state."""
    control = (control or "").strip().lower()
    num = lambda v: None if v in (None, "") else float(v)
    if control == "volume":
        if action in ("mute", "unmute", "toggle"):
            muted = get_volume()["muted"]
            return set_volume(mute={"mute": True, "unmute": False}.get(action, not muted))
        return set_volume(level=num(value), change=num(change))
    if control == "mute":
        on = {"on": True, "off": False}.get(str(action or value).lower())
        return set_volume(mute=(not get_volume()["muted"]) if on is None else on)
    if control == "brightness":
        return set_brightness(level=num(value), change=num(change))
    if control == "media":
        return media(str(action or value))
    if control == "status":
        return status()
    raise ValueError("control must be volume, mute, brightness, media or status")
