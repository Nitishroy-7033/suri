"""The clock, timers, and launching things on this machine."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import webbrowser
from datetime import datetime

from ...core.events import Event
from ...core.tools.base import ToolContext, tool


@tool("get_datetime", "The current local date, time and day of the week.")
async def get_datetime(ctx: ToolContext) -> str:
    now = datetime.now().astimezone()
    return now.strftime("%A %d %B %Y, %H:%M (%Z, UTC%z)")


@tool(
    "set_timer",
    "Start a countdown timer. Jarvis will speak up when it finishes.",
    params={
        "seconds": {"type": "integer", "description": "Duration in seconds."},
        "label": {"type": "string", "description": "What it is for, e.g. 'tea'."},
    },
    required=["seconds"],
)
async def set_timer(ctx: ToolContext, seconds: int, label: str = "") -> str:
    seconds = int(seconds)
    if not 1 <= seconds <= 24 * 3600:
        return "error: timers must be between 1 second and 24 hours"
    what = f" for {label}" if label else ""

    async def ring() -> None:
        await asyncio.sleep(seconds)
        ctx.runtime.events.publish(Event(
            kind="timer",
            say=f"The user's timer{what} has finished. Tell them in a few words.",
            data={"label": label, "seconds": seconds},
        ))

    ctx.runtime.spawn(ring())
    return f"timer{what} set for {seconds} seconds"


@tool(
    "open_url",
    "Open a web page in the user's default browser.",
    params={"url": {"type": "string", "description": "An http or https URL."}},
    required=["url"],
    action=True,
)
async def open_url(ctx: ToolContext, url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "error: only http and https URLs can be opened"
    await asyncio.to_thread(webbrowser.open, url)
    return f"opened {url}"


def _apps(ctx: ToolContext) -> dict[str, str]:
    return {k.lower(): v for k, v in ctx.settings.app_allowlist.items()}


@tool(
    "open_app",
    "Launch an application on this computer by its short name.",
    params={"name": {"type": "string",
                     "description": "One of the allowed app names, e.g. 'notepad'."}},
    required=["name"],
    action=True,
    unavailable=lambda ctx: None if ctx.settings.app_allowlist else "no apps allowed",
)
async def open_app(ctx: ToolContext, name: str) -> str:
    # Only names from the allowlist, never a path or a command line the
    # model made up: whatever it read on a web page must not be able to
    # talk it into running something.
    apps = _apps(ctx)
    target = apps.get(name.strip().lower())
    if target is None:
        return f"error: {name!r} is not an allowed app. Allowed: {', '.join(sorted(apps))}"
    if sys.platform == "win32":
        await asyncio.to_thread(os.startfile, target)  # also handles ms-settings: URIs
    else:
        await asyncio.to_thread(subprocess.Popen, [target])
    return f"launched {name}"
