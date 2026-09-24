"""HTTP routes for the holographic workshop.

Everything here needs the page's token (core/security.py): these routes hand
out files from your drives and write to the model library, so no other page
on this machine may use them.

    GET  /api/library       the model library (yours + the samples)
    POST /api/models        save a dropped 3D file to the library
    GET  /api/fs/drives     drives and the usual folders
    GET  /api/fs/list       one folder, a page at a time
    GET  /api/fs/file       a file's bytes, for previews (supports Range)
    POST /api/fs/trash      ask to move files to the Recycle Bin (needs a yes)
    GET  /api/pc            volume, brightness, Wi-Fi
    POST /api/pc            turn a dial or press a media key
    POST /api/snapshot      the camera picture a forge_build(from_camera) asked for

The /api/fs routes only work with FS_ENABLED=true, and every path goes
through domain/fs/access.py first.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .config import settings
from .core import security

log = logging.getLogger("jarvis.workshop")

MODEL_TYPES = {".glb", ".gltf", ".stl", ".obj"}


def require_token(request: Request) -> None:
    given = request.headers.get("x-jarvis-token") or request.query_params.get("token")
    if not security.token_ok(given):
        raise HTTPException(401, "missing or wrong token")


router = APIRouter(dependencies=[Depends(require_token)])


def _agent(request: Request):
    return request.app.state.agent


def models_dir() -> Path:
    from .domain.holo.catalog import USER_DIR

    USER_DIR.mkdir(parents=True, exist_ok=True)
    return USER_DIR


@router.get("/api/library")
async def library(request: Request) -> dict:
    """Every model "show the ..." can find: yours first, then the samples."""
    return {"models": _agent(request).holo_models.all()}


async def read_capped(request: Request, max_bytes: int) -> bytes:
    """The request body, refusing it as soon as it passes the cap."""
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(413, f"too big (limit {max_bytes // 1_000_000} MB)")
        chunks.append(chunk)
    return b"".join(chunks)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "model"


@router.post("/api/models")
async def save_model(request: Request, name: str) -> dict:
    """Body: the file's bytes. `name` is its original file name."""
    ext = Path(name).suffix.lower()
    if ext not in MODEL_TYPES:
        raise HTTPException(415, f"only {', '.join(sorted(MODEL_TYPES))} files")
    data = await read_capped(request, int(settings.fs_upload_max_mb * 1_000_000))
    if not data:
        raise HTTPException(400, "empty file")
    catalog = _agent(request).holo_models
    model_id = f"{slug(Path(name).stem)}-{secrets.token_hex(3)}"
    catalog.user_dir.mkdir(parents=True, exist_ok=True)
    (catalog.user_dir / f"{model_id}{ext}").write_bytes(data)
    entry = catalog.add({
        "id": model_id, "name": Path(name).stem.replace("_", " ").replace("-", " "),
        "aliases": [Path(name).stem.lower()], "src": f"/models/{model_id}{ext}",
        "credit": "saved from your computer",
    })
    log.info("saved model %s (%d KB)", model_id, len(data) // 1024)
    return {"entry": entry}


# -- files ---------------------------------------------------------------------

def _checked(path: str):
    from .domain.fs.access import Refused, resolve_checked

    if not settings.fs_enabled:
        raise HTTPException(403, "file access is off (FS_ENABLED=false)")
    try:
        return resolve_checked(path, settings)
    except Refused as exc:
        raise HTTPException(403, str(exc)) from None


@router.get("/api/fs/drives")
async def fs_drives() -> dict:
    from .domain.fs import browse

    if not settings.fs_enabled:
        raise HTTPException(403, "file access is off (FS_ENABLED=false)")
    drives, places = await asyncio.gather(asyncio.to_thread(browse.drives), asyncio.to_thread(browse.shortcuts))
    return {"drives": drives, "places": places}


@router.get("/api/fs/list")
async def fs_list(path: str, offset: int = 0) -> dict:
    from .domain.fs import browse

    p = _checked(path)
    if not p.is_dir():
        raise HTTPException(400, "not a folder")
    try:
        return await asyncio.to_thread(browse.list_dir, p, max(0, offset), browse.PAGE, settings)
    except PermissionError:
        raise HTTPException(403, "Windows won't let me open that folder") from None


@router.get("/api/fs/file")
async def fs_file(path: str):
    p = _checked(path)
    if not p.is_file():
        raise HTTPException(400, "not a file")
    # FileResponse answers Range requests, so videos can seek. Inline, so a
    # PDF opens in the preview instead of downloading.
    return FileResponse(p, content_disposition_type="inline", filename=p.name)


@router.post("/api/fs/trash")
async def fs_trash(request: Request, paths: list[str] = Body(..., embed=True)) -> dict:
    from .domain.fs.access import Refused

    if not settings.fs_enabled:
        raise HTTPException(403, "file access is off (FS_ENABLED=false)")
    try:
        req = _agent(request).trash.request(paths)
    except Refused as exc:
        raise HTTPException(403, str(exc)) from None
    return {"id": req.id, "items": req.items}


# -- PC controls ---------------------------------------------------------------

def _pc():
    from .domain.pc import controls

    why = None if settings.pc_controls_enabled else "PC controls are off (PC_CONTROLS_ENABLED=false)"
    why = why or controls.unavailable()
    if why:
        raise HTTPException(403, why)
    return controls


@router.get("/api/pc")
async def pc_state() -> dict:
    controls = _pc()
    return await controls.run(controls.status)


@router.post("/api/pc")
async def pc_set(body: dict = Body(...)) -> dict:
    """The PC panel's dials and buttons: {control, value?, change?, action?}."""
    controls = _pc()
    try:
        return await controls.run(controls.control, body.get("control"), body.get("value"),
                                  body.get("change"), body.get("action"))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from None


# -- camera snapshot --------------------------------------------------------------

@router.post("/api/snapshot")
async def snapshot(request: Request, id: str) -> dict:
    """Body: a JPEG. Only accepted for a request the server itself made."""
    data = await read_capped(request, 8_000_000)
    if data[:3] != bytes([0xFF, 0xD8, 0xFF]):  # a JPEG's first bytes
        raise HTTPException(415, "expected a JPEG")
    if not _agent(request).holo.deliver(id, data):
        raise HTTPException(404, "no picture was asked for")
    return {"ok": True}
