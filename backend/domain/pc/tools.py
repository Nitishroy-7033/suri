"""What Jarvis can change on this PC. Battery, CPU and temperatures are
diagnostics' system_status; this is the knobs."""

from __future__ import annotations

from ...core.tools.base import ToolContext, tool
from . import controls


def _off(ctx: ToolContext) -> str | None:
    if not ctx.settings.pc_controls_enabled:
        return "PC controls are off (PC_CONTROLS_ENABLED=false)"
    return controls.unavailable()


@tool(
    "pc_control",
    "Change the PC's volume, mute, screen brightness, or press a media key. Examples: volume to "
    "30 -> control=volume value=30; 'louder' -> control=volume change=10; mute -> control=mute "
    "action=on; brightness down -> control=brightness change=-20; pause the music -> "
    "control=media action=play_pause; next song -> control=media action=next.",
    params={
        "control": {"type": "string", "enum": ["volume", "mute", "brightness", "media"]},
        "value": {"type": "number", "description": "Absolute level 0-100"},
        "change": {"type": "number", "description": "Relative change, e.g. 10 or -20"},
        "action": {"type": "string", "description": "media: play_pause, next, previous, stop. mute: on, off, toggle"},
    },
    required=["control"],
    action=True,
    unavailable=_off,
)
async def pc_control(ctx: ToolContext, control: str, value=None, change=None, action: str = "") -> dict:
    try:
        state = await controls.run(controls.control, control, value, change, action or None)
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}
    return state


@tool(
    "pc_status",
    "The PC's volume, mute, screen brightness and Wi-Fi connection right now.",
    unavailable=_off,
)
async def pc_status(ctx: ToolContext) -> dict:
    return await controls.run(controls.status)
