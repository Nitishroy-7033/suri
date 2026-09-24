"""Listing folders and finding files, for the hologram file panels.

Blocking file-system work: callers run these with asyncio.to_thread. Denied
and hidden entries are simply left out of listings, not shown greyed out.
"""

from __future__ import annotations

import fnmatch
import mimetypes
import os
import shutil
import time
from pathlib import Path

from .access import denied, display, places

PAGE = 200
FIND_LIMIT = 50
FIND_BUDGET_S = 3.0


def _entry(p: Path, st: os.stat_result, is_dir: bool) -> dict:
    return {
        "name": p.name or display(p),
        "path": display(p),
        "kind": "dir" if is_dir else "file",
        "size": None if is_dir else st.st_size,
        "mtime": int(st.st_mtime),
        "ext": "" if is_dir else p.suffix.lower(),
        "mime": None if is_dir else (mimetypes.guess_type(p.name)[0] or ""),
    }


def drives() -> list[dict]:
    names = os.listdrives() if hasattr(os, "listdrives") else ["/"]
    out = []
    for d in names:
        try:
            usage = shutil.disk_usage(d)
        except OSError:
            continue  # an empty card reader or DVD drive
        out.append({"name": d.rstrip("\\"), "path": d, "kind": "drive",
                    "free": usage.free, "total": usage.total})
    return out


def shortcuts() -> list[dict]:
    """Desktop, Documents, Downloads... for the side of the root view."""
    out = []
    for name in ("desktop", "documents", "downloads", "pictures", "videos", "music"):
        p = places()[name]
        if p.is_dir():
            out.append({"name": name.capitalize(), "path": display(p), "kind": "place"})
    return out


def list_dir(path: Path, offset: int = 0, limit: int = PAGE, settings=None) -> dict:
    entries = []
    with os.scandir(path) as it:
        for e in it:
            try:
                p = Path(e.path)
                if denied(p, settings):
                    continue
                is_dir = e.is_dir(follow_symlinks=False)
                entries.append(_entry(p, e.stat(follow_symlinks=False), is_dir))
            except OSError:
                continue
    entries.sort(key=lambda x: (x["kind"] != "dir", x["name"].lower()))
    parent = path.parent if path.parent != path else None
    return {
        "path": display(path), "name": path.name or display(path),
        "parent": display(parent) if parent and not denied(parent, settings) else None,
        "total": len(entries), "offset": offset,
        "entries": entries[offset:offset + limit],
    }


def find(name: str, under: list[Path], ext: str = "", days: int | None = None,
         settings=None, limit: int = FIND_LIMIT, budget_s: float = FIND_BUDGET_S) -> dict:
    """Files and folders whose name contains `name` (or matches a glob like
    "*.pdf"), newest first. Stops at `limit` hits or `budget_s` seconds."""
    pat = name.strip().lower()
    glob = any(c in pat for c in "*?[")
    exts = {e if e.startswith(".") else f".{e}" for e in (x.strip().lower() for x in ext.split(",")) if e}
    since = time.time() - days * 86400 if days else None
    deadline = time.monotonic() + budget_s
    hits, truncated = [], False
    for root in under:
        for dirpath, dirnames, filenames in os.walk(root):
            if time.monotonic() > deadline or len(hits) >= limit:
                truncated = True
                break
            base = Path(dirpath)
            dirnames[:] = [d for d in dirnames if not denied(base / d, settings)]
            for n in dirnames + filenames:
                low = n.lower()
                if pat and not (fnmatch.fnmatch(low, pat) if glob else pat in low):
                    continue
                p = base / n
                if exts and p.suffix.lower() not in exts:
                    continue
                if n in filenames and denied(p, settings):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if since and st.st_mtime < since:
                    continue
                hits.append(_entry(p, st, n in dirnames))
                if len(hits) >= limit:
                    break
        if truncated:
            break
    hits.sort(key=lambda h: -h["mtime"])
    return {"results": hits, "truncated": truncated}
