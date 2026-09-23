"""The set of tools one session may use, and the only place they are run.

Everything that can go wrong with a tool is contained here, because a tool
failing must never take a voice turn down with it: the model gets an error
string it can apologise with, and the conversation carries on.

The one exception is cancellation. When the user interrupts, the task running
the tool is cancelled and that has to propagate -- so `except Exception`, never
a bare `except`, exactly as in the pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from .base import Tool, ToolContext

log = logging.getLogger("jarvis.tools")


@dataclass
class ToolOutcome:
    name: str
    ok: bool
    output: str
    ms: float


class ToolRegistry:
    def __init__(self, ctx: ToolContext, *, timeout_s: float = 15.0,
                 max_chars: int = 2000, allow_actions: bool = False) -> None:
        self.ctx = ctx
        self.timeout_s = timeout_s
        self.max_chars = max_chars
        self.allow_actions = allow_actions
        self._tools: dict[str, Tool] = {}

    # -- building ----------------------------------------------------------

    def register(self, t: Tool) -> None:
        if t.name in self._tools:
            raise ValueError(f"duplicate tool name {t.name!r}")
        self._tools[t.name] = t

    def register_module(self, module: ModuleType, enabled: set[str] | None = None) -> None:
        """Register every Tool defined at a module's top level."""
        for value in vars(module).values():
            if not isinstance(value, Tool):
                continue
            if enabled is not None and value.name not in enabled:
                continue
            if value.action and not self.allow_actions:
                log.debug("tool %s skipped (action tools are off)", value.name)
                continue
            reason = value.unavailable(self.ctx) if value.unavailable else None
            if reason:
                log.debug("tool %s unavailable: %s", value.name, reason)
                continue
            self.register(value)

    # -- introspection -----------------------------------------------------

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def openai_tools(self) -> list[dict]:
        return [t.openai_schema() for t in self._tools.values()]

    def gemini_tools(self) -> list[dict]:
        if not self._tools:
            return []
        return [{"function_declarations":
                 [t.gemini_schema() for t in self._tools.values()]}]

    # -- running -----------------------------------------------------------

    async def execute(self, name: str, args: dict | str | None) -> ToolOutcome:
        t0 = time.perf_counter()

        def done(ok: bool, output: str) -> ToolOutcome:
            ms = (time.perf_counter() - t0) * 1000
            log.info("tool %s %s in %.0f ms: %s", name, "ok" if ok else "FAILED",
                     ms, output[:120].replace("\n", " "))
            return ToolOutcome(name, ok, output, ms)

        t = self._tools.get(name)
        if t is None:
            return done(False, f"error: no tool named {name!r}")

        if isinstance(args, str):
            # OpenAI-style APIs send arguments as a JSON string, and small
            # models occasionally send an empty one for no-arg tools.
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                return done(False, f"error: arguments were not valid JSON: {args[:200]}")
        args = dict(args or {})
        # Models sometimes invent parameters; drop them rather than crash.
        unknown = set(args) - set(t.params)
        for key in unknown:
            args.pop(key)
        missing = [k for k in t.required if k not in args]
        if missing:
            return done(False, f"error: missing required argument(s): {', '.join(missing)}")

        try:
            result = await asyncio.wait_for(t.fn(self.ctx, **args), self.timeout_s)
        except TimeoutError:
            return done(False, f"error: {name} timed out after {self.timeout_s:.0f}s")
        except Exception as exc:
            log.exception("tool %s raised", name)
            return done(False, f"error: {type(exc).__name__}: {exc}"[:300])

        return done(True, self._stringify(result))

    def _stringify(self, result: Any) -> str:
        text = result if isinstance(result, str) else json.dumps(
            result, ensure_ascii=False, default=str)
        if len(text) > self.max_chars:
            text = text[: self.max_chars] + " ...[truncated]"
        return text
