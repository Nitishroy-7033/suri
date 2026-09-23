"""FastAPI entrypoint: serves the frontend and the one WebSocket.

Run with:  uvicorn backend.main:app --host 127.0.0.1 --port 8080
or just double-click run.bat.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .core.runtime import AgentRuntime
from .config import settings
from .domain.voice.session import Session

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-7s %(name)-16s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("jarvis")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm the ONNX graphs and start the agent before anyone connects.

    openWakeWord and Silero are constructed per session because both are
    stateful, but the expensive part -- reading and optimising the graphs --
    is cached by onnxruntime, so paying it once here keeps the first
    connection from stalling for a second.
    """
    log.info("Jarvis backend up  ->  http://%s:%d", settings.host, settings.port)
    t0 = time.perf_counter()
    try:
        import numpy as np

        from .domain.voice.vad import VadGate
        from .domain.voice.wake import WakeWordDetector

        warm_wake = WakeWordDetector(
            model_name=settings.wake_model, gate_on_vad=False
        )
        warm_wake.push(np.zeros(settings.wire_frame_samples, dtype=np.int16))
        VadGate().push(np.zeros(settings.vad_frame_samples, dtype=np.int16))
        del warm_wake
        log.info("models warmed in %.0f ms", (time.perf_counter() - t0) * 1000)
    except Exception:
        log.exception("model warm-up failed - sessions will load on demand")

    # One per process: the camera, memory file and timers are shared by
    # every connection, unlike the per-session ears and brain.
    agent = AgentRuntime(settings)
    await agent.start()
    app.state.agent = agent
    try:
        yield
    finally:
        log.info("shutting down")
        await agent.close()


app = FastAPI(title="Jarvis", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "phase": 2, "mode": settings.jarvis_mode}


@app.get("/api/tools")
async def list_tools() -> dict:
    """What the model is offered right now -- handy when a tool seems ignored."""
    reg = app.state.agent.registry("api")
    return {"tools": [{"name": t["function"]["name"],
                       "description": t["function"]["description"]}
                      for t in reg.openai_tools()]}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    log.info("client connected from %s", ws.client)
    await Session(ws, settings, ws.app.state.agent).run()


# Mounted last so /ws and /healthz win. html=True serves index.html at /.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
