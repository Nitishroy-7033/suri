"""The web agent's own model: one function call per step.

Separate from the voice brain on purpose. It uses the ordinary (non-live)
Gemini endpoint, where the tool list can change on every call -- which is
what lets a site's WebMCP tools become real functions the moment the page
registers them. Gemini Live fixes its tools when the session connects.

Each step is stateless: the agent sends the task, its recent steps and the
current page, and forces exactly one function call back. No growing chat
history to keep in sync, and a snapshot from ten pages ago never lingers in
the context.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from ...config import Settings

log = logging.getLogger("jarvis.webagent.llm")


@dataclass
class Action:
    name: str
    args: dict
    thought: str = ""  # any text the model wrote alongside the call


class GeminiAgentLlm:
    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        from google import genai

        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self.settings = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._gemini_down_until = 0.0
        self._daily_quota = False  # the last Gemini failure was a per-day limit

    async def next_action(self, system: str, prompt: str, functions: list[dict]) -> Action:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            tools=[{"function_declarations": functions}],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY")),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        from google.genai import errors

        import time

        # Gemini failed recently (quota, overload, hang): don't pay for the
        # retries again on every step -- go straight to Groq for a while.
        if self.settings.groq_api_key and time.monotonic() < self._gemini_down_until:
            return await self._groq(system, prompt, functions)
        try:
            resp = await self._generate(prompt, config)
        except (errors.APIError, TimeoutError) as exc:
            # Gemini overloaded, out of quota or hanging: this step goes to
            # Groq instead of stalling the task for minutes (seen: 503 on both
            # models, then 429, then a call that hung for five minutes).
            code = getattr(exc, "code", 0) or 0
            if not self.settings.groq_api_key or not (isinstance(exc, TimeoutError) or code in (429, 500, 502, 503, 504)):
                raise
            # A daily quota won't come back in minutes: stop paying for the
            # retries (and a 25 s hang on the fallback) on every step.
            down = 3600 if self._daily_quota else 180
            self._gemini_down_until = time.monotonic() + down
            log.warning("web agent: Gemini unavailable (%s); using Groq for the next %d minutes",
                        "daily quota" if self._daily_quota else code or "timeout", down // 60)
            return await self._groq(system, prompt, functions)
        calls = resp.function_calls or []
        text = ""
        try:
            text = (resp.text or "").strip()
        except Exception:
            pass  # .text complains when the reply is only a function call
        if not calls:
            # Mode ANY should always call; if a model answers in prose anyway,
            # treat it as the final answer rather than stalling.
            return Action("finish", {"summary": text or "I couldn't decide what to do next."})
        fc = calls[0]
        return Action(fc.name, dict(fc.args or {}), text)

    async def _generate(self, prompt: str, config):
        """"High demand" (503) is common and brief: retry with backoff, then
        try the lighter fallback model before giving up. Quota errors (429)
        get one short retry only -- waiting won't refill a daily quota."""
        import asyncio

        from google.genai import errors

        models = [self.settings.web_agent_model]
        if self.settings.web_agent_fallback_model:
            models.append(self.settings.web_agent_fallback_model)
        last: Exception | None = None
        self._daily_quota = False
        for model in models:
            for delay in (0, 1.5, 4.0):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    # Bounded: one call once hung for five minutes.
                    return await asyncio.wait_for(self._client.aio.models.generate_content(
                        model=model, contents=prompt, config=config), 25)
                except TimeoutError:
                    log.warning("web agent model %s: no answer in 25 s", model)
                    last = TimeoutError(f"{model} timed out")
                    break  # a hang won't fix itself in 4 s; try the next model
                except errors.APIError as exc:
                    last = exc
                    code = getattr(exc, "code", 0) or 0
                    log.warning("web agent model %s: %s %s", model, code, str(exc)[:120])
                    if code == 429 and re.search(r"per ?day|daily", str(exc), re.I):
                        self._daily_quota = True
                        break  # retrying can't refill a daily quota
                    if code == 429 and delay >= 1.5:
                        break
                    if code not in (429, 500, 502, 503, 504):
                        raise
        raise last

    async def _groq(self, system: str, prompt: str, functions: list[dict]) -> Action:
        """The same step on Groq's OpenAI-compatible API, tool call forced."""
        import json

        import httpx

        tools = [{"type": "function", "function": {
            "name": f["name"], "description": f.get("description", ""),
            "parameters": f.get("parameters_json_schema") or {"type": "object", "properties": {}},
        }} for f in functions]
        # Groq's free tier allows ~8k tokens a minute per model, and a step is
        # 2-3k: a busy task hits it within a minute (seen on step 4 after a
        # full-page read). Wait out the short reset, then try the other model,
        # which has its own allowance.
        models = [self.settings.web_agent_groq_model or self.settings.groq_llm_model,
                  self.settings.groq_llm_fallback_model]
        r = None
        async with httpx.AsyncClient(timeout=40) as client:
            for model in dict.fromkeys(m for m in models if m):
                for attempt in range(2):
                    body = {
                        "model": model,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                        "tools": tools, "tool_choice": "required", "temperature": 0.2,
                    }
                    r = await client.post("https://api.groq.com/openai/v1/chat/completions", json=body,
                                          headers={"Authorization": f"Bearer {self.settings.groq_api_key}"})
                    if r.status_code != 429:
                        break
                    if attempt == 0:
                        wait = min(float(r.headers.get("retry-after") or 5), 15)
                        log.warning("web agent: Groq %s rate-limited; retrying in %.0f s", model, wait)
                        await asyncio.sleep(wait)
                if r.status_code != 429:
                    break
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            return Action("finish", {"summary": (msg.get("content") or "").strip() or "I couldn't decide what to do next."})
        fn = calls[0]["function"]
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        return Action(fn["name"], args if isinstance(args, dict) else {}, msg.get("content") or "")
