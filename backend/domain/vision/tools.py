"""The camera: looking on request, and watching for motion.

Only offered when CAMERA_ENABLED=true and OpenCV is installed. Looking also
needs a vision model (VISION_AGENT__*, Gemini by default); motion watching
does not -- it is local frame differencing and never sends anything anywhere.
"""

from __future__ import annotations

from ...core.tools.base import ToolContext, tool


def _no_camera(ctx: ToolContext) -> str | None:
    if ctx.runtime.camera is None:
        return "camera is off (CAMERA_ENABLED) or OpenCV is missing"
    return None


def _no_vision(ctx: ToolContext) -> str | None:
    return _no_camera(ctx) or ctx.runtime.models.available("vision_agent")


@tool(
    "look",
    "Take a picture with the camera and describe it. Use when the user asks "
    "what you can see, to look at something, read something they hold up, "
    "or who or what is in the room.",
    params={"question": {"type": "string",
                         "description": "What to look for, e.g. 'what am I holding?'"}},
    unavailable=_no_vision,
)
async def look(ctx: ToolContext, question: str = "") -> str:
    jpeg = await ctx.runtime.camera.snapshot_jpeg()
    return await ctx.runtime.vision.describe(jpeg, question)


@tool(
    "start_motion_watch",
    "Start watching the camera for movement and speak up when something "
    "moves, e.g. 'tell me if anyone comes in'.",
    unavailable=_no_camera,
)
async def start_motion_watch(ctx: ToolContext) -> str:
    ctx.runtime.motion.start()
    return "watching for motion"


@tool("stop_motion_watch", "Stop watching the camera for movement.",
      unavailable=_no_camera)
async def stop_motion_watch(ctx: ToolContext) -> str:
    await ctx.runtime.motion.stop()
    return "stopped watching"


@tool("camera_status", "Whether the camera and motion watch are running.",
      unavailable=_no_camera)
async def camera_status(ctx: ToolContext) -> dict:
    cam, motion = ctx.runtime.camera, ctx.runtime.motion
    return {"camera_open": cam.active, "error": cam.error,
            "motion_watch": motion.running, "motion_alerts": motion.alerts}
