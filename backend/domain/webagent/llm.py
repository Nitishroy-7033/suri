"""The web agent's own model: one function call per step.

Separate from the voice brain on purpose, and chosen in .env like every other
agent (WEB_AGENT__PROVIDER / __MODEL / __FALLBACK; default Gemini Flash, then
Flash-Lite, then Groq). Whatever the provider, the tool list can change on
every call -- which is what lets a site's WebMCP tools become real functions
the moment the page registers them.

Each step is stateless: the agent sends the task, its recent steps and the
current page, and forces exactly one function call back. No growing chat
history to keep in sync, and a snapshot from ten pages ago never lingers in
the context. Retries, "503 high demand" and quota fallbacks are handled by
the shared FallbackModel.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

log = logging.getLogger("jarvis.webagent.llm")


@dataclass
class Action:
    name: str
    args: dict
    thought: str = ""  # any text the model wrote alongside the call


class WebAgentLlm:
    def __init__(self, model) -> None:
        self.model = model  # a FallbackModel from runtime.models

    @property
    def name(self) -> str:
        return self.model.name

    async def next_action(self, system: str, prompt: str, functions: list[dict]) -> Action:
        tools = [{"type": "function", "function": {
            "name": f["name"], "description": f.get("description", ""),
            "parameters": f.get("parameters") or {"type": "object", "properties": {}},
        }} for f in functions]
        result = await self.model.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            tools, tool_choice="required", temperature=0.2, max_tokens=1024)
        if not result.tool_calls:
            # A forced call should always come back; if a model answers in
            # prose anyway, treat it as the final answer rather than stalling.
            return Action("finish", {"summary": result.text or "I couldn't decide what to do next."})
        call = result.tool_calls[0]
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        return Action(call.name, args if isinstance(args, dict) else {}, result.text)
