"""What Jarvis can ask of the forge: find a free model, build a new one."""

from __future__ import annotations

from ...core.tools.base import ToolContext, tool


def _off(ctx: ToolContext) -> str | None:
    if not ctx.settings.holo_enabled:
        return "the workshop is off (HOLO_ENABLED=false)"
    if not ctx.runtime.forge_ready():
        return "no model search or generator is set up"
    return None


def _no_search(ctx: ToolContext) -> str | None:
    return _off(ctx) or (None if ctx.settings.poly_pizza_api_key else "free search needs POLY_PIZZA_API_KEY")


def _no_build(ctx: ToolContext) -> str | None:
    s = ctx.settings
    return _off(ctx) or (None if (s.tripo_api_key or s.meshy_api_key or s.forge_local_url)
                         else "no 3D generator is set up")


@tool(
    "forge_find",
    "Search free 3D model libraries for a model the library doesn't have (e.g. 'a drone', 'a "
    "sports car'), download the best match and put it on the hologram. Free; takes a few seconds.",
    params={"query": {"type": "string", "description": "What to look for, in a few words"}},
    required=["query"],
    unavailable=_no_search,
)
async def forge_find(ctx: ToolContext, query: str) -> str:
    return await ctx.runtime.forge.find(query)


@tool(
    "forge_build",
    "Generate a brand-new 3D model from a description ('an arc reactor', 'a steampunk helmet'), "
    "or from what the camera sees (from_camera=true, for 'make a 3D model of this'). Runs in the "
    "background for a minute or two; Jarvis is told when it is ready. Paid services need the "
    "user's yes first: call without confirmed, and if it says to confirm, ask the user and call "
    "again with confirmed=true only after they agree.",
    params={
        "description": {"type": "string", "description": "What to build, in the user's words"},
        "from_camera": {"type": "boolean", "description": "Build from a camera picture of what the user holds up"},
        "confirmed": {"type": "boolean", "description": "The user has said yes to spending credits"},
    },
    required=["description"],
    unavailable=_no_build,
)
async def forge_build(ctx: ToolContext, description: str, from_camera: bool = False,
                      confirmed: bool = False) -> str:
    image = None
    if from_camera in (True, "true", "True"):
        image = await ctx.runtime.holo.snapshot(ctx.runtime.events)
        if image is None:
            return ("couldn't get a camera picture: open the workshop (and turn on Hands, or allow the "
                    "camera) and try again")
    return await ctx.runtime.forge.build(description, image=image, confirmed=confirmed in (True, "true", "True"))


@tool(
    "forge_job",
    "The model being built: status (what, how far, which service) or cancel.",
    params={"action": {"type": "string", "enum": ["status", "cancel"]}},
    unavailable=_no_build,
)
async def forge_job(ctx: ToolContext, action: str = "status") -> dict | str:
    if action == "cancel":
        return await ctx.runtime.forge.cancel()
    return ctx.runtime.forge.status()
