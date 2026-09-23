"""What a tool is, and the one decorator that makes one.

A tool is an async function plus the JSON schema a model needs to call it.
Both brains consume the same definition -- Groq/Ollama get it in OpenAI
"function" shape, Gemini Live as a function declaration -- so a capability is
written once and works in either mode.

Adding a capability is one function:

    @tool("get_weather", "Current weather for a city.",
          params={"city": {"type": "string"}}, required=["city"])
    async def get_weather(ctx: ToolContext, city: str) -> str:
        ...

in `domain/<name>/tools.py`, then add that module to `DOMAIN_TOOL_MODULES`
in `core/tools/catalog.py`.

Tools return a short string (or anything JSON-serialisable). Keep it short:
it is read by a model that is about to speak two sentences, not a human
scrolling a page, and every extra kilobyte is latency.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...config import Settings
    from ..runtime import AgentRuntime

ToolFn = Callable[..., Awaitable[Any]]


@dataclass
class ToolContext:
    """Everything a tool may touch. Passed as the first argument."""

    settings: "Settings"
    runtime: "AgentRuntime"
    #: Which brain is calling -- occasionally worth knowing, e.g. to decide
    #: how much detail a result should carry.
    brain: str = ""


@dataclass
class Tool:
    name: str
    description: str
    fn: ToolFn
    params: dict[str, dict] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    #: Tools that change the machine (launch programs, write files) rather
    #: than just read the world. Refused unless TOOLS_ALLOW_ACTIONS is on.
    action: bool = False
    #: Optional gate, given the ToolContext: return a reason string if the
    #: tool cannot run in this setup (e.g. camera disabled), or None if it
    #: can. Unavailable tools are never offered to the model, so it cannot
    #: promise what it can't do.
    unavailable: Callable[["ToolContext"], str | None] | None = None

    @property
    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": self.params,
            "required": self.required,
        }

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }

    def gemini_schema(self) -> dict:
        decl: dict = {"name": self.name, "description": self.description}
        if self.params:
            decl["parameters_json_schema"] = self.schema
        return decl


def tool(
    name: str,
    description: str,
    *,
    params: dict[str, dict] | None = None,
    required: list[str] | None = None,
    action: bool = False,
    unavailable: Callable[["ToolContext"], str | None] | None = None,
) -> Callable[[ToolFn], Tool]:
    """Turn `async def fn(ctx, **args)` into a Tool."""

    def wrap(fn: ToolFn) -> Tool:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"tool {name!r} must be async (use asyncio.to_thread "
                            "for blocking work)")
        return Tool(name=name, description=description, fn=fn,
                    params=params or {}, required=required or [],
                    action=action, unavailable=unavailable)

    return wrap
