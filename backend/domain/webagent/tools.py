"""What Jarvis can ask of the web agent. The agent's own tools live in agent.py.

Jarvis delegates; it never clicks. That keeps page snapshots out of the voice
model's context and lets a long job run while you keep talking.
"""

from __future__ import annotations

from ...core.tools import ToolContext, tool
from .browser import playwright_missing


def _unavailable(ctx: ToolContext) -> str | None:
    if not ctx.settings.web_agent_enabled:
        return "web agent disabled (WEB_AGENT_ENABLED=false)"
    why = ctx.runtime.models.available("web_agent")
    if why:
        return f"the web agent has no usable model ({why})"
    return playwright_missing()


@tool(
    "web_task",
    "Hand a job to the web agent, which controls a real Chrome window: open websites, search them, "
    "click, type, fill forms, scroll, switch tabs, read what's on screen, and use sites' own WebMCP tools. "
    "Use it for anything that needs a website, beyond a quick fact from web_search. Follow-ups about the "
    "same page ('click the second one', 'scroll down', 'go back') also go here. Describe the job in the "
    "user's words. Returns the result, a question to ask the user, or that it's still working.",
    params={"task": {"type": "string", "description": "What to do, e.g. 'open YouTube and play lo-fi music'"}},
    required=["task"],
    action=True,
    unavailable=_unavailable,
)
async def web_task(ctx: ToolContext, task: str) -> str:
    return await ctx.runtime.web_agent.submit(task)


@tool(
    "web_reply",
    "Pass the user's answer to the web agent's question (including yes or no to a confirmation).",
    params={"answer": {"type": "string", "description": "The user's answer, in their words"}},
    required=["answer"],
    action=True,
    unavailable=_unavailable,
)
async def web_reply(ctx: ToolContext, answer: str) -> str:
    return await ctx.runtime.web_agent.reply(answer)


@tool(
    "web_status",
    "What the web agent is doing: its task, last steps, the current page, and any question it is waiting on.",
    unavailable=_unavailable,
)
async def web_status(ctx: ToolContext) -> str:
    return ctx.runtime.web_agent.status()


@tool(
    "web_stop",
    "Stop the web agent's current job. The browser window stays open.",
    unavailable=_unavailable,
)
async def web_stop(ctx: ToolContext) -> str:
    return await ctx.runtime.web_agent.stop()
