"""What Jarvis can do in the holographic workshop.

Every tool here only *tells the page* what to do, by publishing a holo_*
event; the page does it and reports back what is on screen (HoloState). So
these return at once, and "what's this?" is answered from what the page last
said, never guessed.
"""

from __future__ import annotations

import difflib

from ...core.events import Event
from ...core.tools.base import ToolContext, tool

PANELS = ("workshop", "chat", "settings", "systems", "pc", "guide", "tutorial")
VIEW_ACTIONS = ("rotate_left", "rotate_right", "tilt_up", "tilt_down", "spin_on", "spin_off",
                "zoom_in", "zoom_out", "reset", "explode", "collapse", "next", "previous")


def _off(ctx: ToolContext) -> str | None:
    return None if ctx.settings.holo_enabled else "the workshop is off (HOLO_ENABLED=false)"


def _publish(ctx: ToolContext, kind: str, **data) -> None:
    ctx.runtime.events.publish(Event(kind=kind, data=data))


@tool(
    "holo_show",
    "Show a 3D model as a hologram in the workshop (opens it if needed), e.g. 'show the engine'. "
    "Looks in the model library by name.",
    params={"name": {"type": "string", "description": "What to show, e.g. 'engine', 'helmet'"}},
    required=["name"],
    unavailable=_off,
)
async def holo_show(ctx: ToolContext, name: str) -> str:
    entry = ctx.runtime.holo_models.search(name)
    if entry is None:
        have = ", ".join(ctx.runtime.holo_models.names())
        more = ""
        if ctx.runtime.forge_ready():
            more = (" You can search online for a free one with forge_find, or generate one "
                    "with forge_build (ask the user first).")
        return f"no model called {name!r} in the library. It has: {have}.{more}"
    _publish(ctx, "holo_show", entry=entry)
    about = f" {entry['about']}" if entry.get("about") else ""
    return f"showing {entry.get('name') or entry['id']}.{about}"


@tool(
    "holo_view",
    "Move or change the hologram on screen: rotate_left, rotate_right, tilt_up, tilt_down, "
    "spin_on, spin_off, zoom_in, zoom_out, reset, explode (pull the parts apart), collapse, "
    "next / previous model.",
    params={"action": {"type": "string", "enum": list(VIEW_ACTIONS)}},
    required=["action"],
    unavailable=_off,
)
async def holo_view(ctx: ToolContext, action: str) -> str:
    action = action.strip().lower()
    if action not in VIEW_ACTIONS:
        return f"error: action must be one of {', '.join(VIEW_ACTIONS)}"
    _publish(ctx, "holo_view", action=action)
    return f"done: {action.replace('_', ' ')}"


@tool(
    "holo_highlight",
    "Highlight one part of the model on screen and show its label, e.g. 'the piston'. "
    "Empty clears the highlight.",
    params={"part": {"type": "string", "description": "The part's name, in the user's words"}},
    unavailable=_off,
)
async def holo_highlight(ctx: ToolContext, part: str = "") -> str:
    parts = ctx.runtime.holo.view.get("parts") or []
    match = part
    if part and parts:
        lower = {p.lower(): p for p in parts}
        hit = next((p for lp, p in lower.items() if part.lower() in lp), None)
        close = difflib.get_close_matches(part.lower(), list(lower), n=1, cutoff=0.5)
        match = hit or (lower[close[0]] if close else part)
    _publish(ctx, "holo_highlight", part=match)
    if not part:
        return "highlight cleared"
    if parts and match == part and part.lower() not in (p.lower() for p in parts):
        return (f"no part clearly named {part!r}; asked the page to find the closest. "
                f"Parts include: {', '.join(parts[:15])}")
    return f"highlighted {match}"


@tool(
    "holo_panel",
    "Open, close or arrange floating panels in the workshop. Targets: workshop (the whole "
    "hologram screen), chat, settings (Jarvis settings), systems (live laptop gauges), pc "
    "(volume, brightness, media), guide (how to use it: every hand gesture, voice command and "
    "keyboard shortcut), tutorial (a step-by-step hand-gesture lesson with the camera), "
    "folder:<path or name like 'downloads', 'E drive'>, "
    "file:<path>, or a panel's title for close/focus. 'close_all' closes every panel; "
    "'arrange' tidies them into a grid.",
    params={
        "action": {"type": "string", "enum": ["open", "close", "focus", "close_all", "arrange"]},
        "target": {"type": "string", "description": "e.g. 'workshop', 'settings', 'folder:downloads'"},
    },
    required=["action"],
    unavailable=_off,
)
async def holo_panel(ctx: ToolContext, action: str, target: str = "") -> str:
    action, target = action.strip().lower(), target.strip()
    kind, _, rest = target.partition(":")
    kind = kind.strip().lower()
    if action == "open" and kind in ("folder", "file"):
        if not ctx.settings.fs_enabled:
            return "file access is off (FS_ENABLED=false)"
        from ..fs import access

        try:
            path = access.resolve_spoken(rest.strip(), ctx.settings, want_dir=kind == "folder")
        except access.Refused as exc:
            return f"can't open that: {exc}"
        _publish(ctx, "holo_panel", action="open", target=kind, path=str(path))
        return f"opened {path}"
    if action == "open" and kind not in PANELS:
        return f"error: open one of {', '.join(PANELS)}, folder:<path> or file:<path>"
    _publish(ctx, "holo_panel", action=action, target=target)
    return f"done: {action} {target}".strip()


@tool(
    "holo_status",
    "What the workshop shows right now: whether it is open, the model and its parts, the part "
    "or file the user is pointing at or selected, and the open panels. Call this when the user "
    "says 'this', 'that part' or 'what am I looking at'.",
    unavailable=_off,
)
async def holo_status(ctx: ToolContext) -> dict:
    return ctx.runtime.holo.summary()
