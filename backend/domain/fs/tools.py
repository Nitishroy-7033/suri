"""What Jarvis can do with your files. Opening a folder or file on screen is
holo_panel (domain/holo); these find, read and delete."""

from __future__ import annotations

import asyncio

from ...core.tools.base import ToolContext, tool
from . import access, browse, read


def _off(ctx: ToolContext) -> str | None:
    if not ctx.settings.fs_enabled:
        return "file access is off (FS_ENABLED=false)"
    return None


@tool(
    "fs_find",
    "Search the user's files by name, e.g. 'my resume', 'invoice pdf from last week'. Looks in "
    "Desktop, Documents, Downloads, Pictures, Videos and Music unless a folder is given. Returns "
    "the newest matches with their full paths; open one on screen with holo_panel file:<path>.",
    params={
        "name": {"type": "string", "description": "Part of the name, or a pattern like '*.pdf'"},
        "under": {"type": "string", "description": "Optional folder to search, e.g. 'E drive', 'downloads'"},
        "ext": {"type": "string", "description": "Optional extensions, e.g. 'pdf' or 'jpg,png'"},
        "days": {"type": "integer", "description": "Optional: only changed in the last N days"},
    },
    required=["name"],
    unavailable=_off,
)
async def fs_find(ctx: ToolContext, name: str, under: str = "", ext: str = "", days: int = 0) -> dict:
    try:
        days = int(days or 0)
    except (TypeError, ValueError):
        days = 0
    try:
        if under:
            root = access.resolve_spoken(under, ctx.settings)
            roots = [root] if root else [access.resolve_checked(d["path"], ctx.settings) for d in browse.drives()]
        else:
            roots = [p for n, p in access.places().items()
                     if n in ("desktop", "documents", "downloads", "pictures", "videos", "music") and p.is_dir()]
    except access.Refused as exc:
        return {"error": str(exc)}
    found = await asyncio.to_thread(browse.find, name, roots, ext, days or None, ctx.settings)
    hits = [{"name": h["name"], "path": h["path"], "kind": h["kind"]} for h in found["results"][:12]]
    return {"matches": hits, "more": found["truncated"] or len(found["results"]) > 12}


@tool(
    "fs_ask",
    "Read one of the user's files and answer a question about it: summarise a PDF, describe a "
    "photo, explain some code, find something in a document. Only when the user asks about that "
    "file. Use the full path (from fs_find, holo_status, or what the user dropped on you).",
    params={
        "path": {"type": "string", "description": "Full path, e.g. C:\\Users\\me\\Downloads\\report.pdf"},
        "question": {"type": "string", "description": "What the user wants to know"},
    },
    required=["path"],
    unavailable=_off,
)
async def fs_ask(ctx: ToolContext, path: str, question: str = "") -> str:
    try:
        return await read.ask(ctx.runtime, path, question)
    except access.Refused as exc:
        return f"can't read that: {exc}"


@tool(
    "fs_delete",
    "Ask to move files or folders to the Recycle Bin. This does NOT delete anything by itself: it "
    "shows the user a confirm panel, and the files move only when the user says yes, clicks it, or "
    "gives a thumbs up. After calling it, ask the user to confirm; when they say yes it happens "
    "on its own -- do not call this again.",
    params={"paths": {"type": "array", "items": {"type": "string"},
                      "description": "Full paths, at most 20"}},
    required=["paths"],
    action=True,
    unavailable=_off,
)
async def fs_delete(ctx: ToolContext, paths: list) -> str:
    if isinstance(paths, str):
        paths = [paths]
    try:
        req = ctx.runtime.trash.request([str(p) for p in paths])
    except access.Refused as exc:
        return f"can't delete that: {exc}"
    names = ", ".join(i["name"] for i in req.items[:5])
    return (f"waiting for the user's yes to move {len(req.items)} item(s) to the Recycle Bin ({names}). "
            "Ask them to confirm.")
