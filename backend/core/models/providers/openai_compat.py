"""Any OpenAI-compatible chat endpoint: OpenAI, Groq, OpenRouter, and local
servers such as LM Studio or vLLM (provider "openai_compat" + BASE_URL).

Streams over SSE. Tool calls arrive as fragments keyed by index -- the name
first, then the JSON arguments a few characters at a time -- and are yielded
whole once the stream ends.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator

import httpx

from ..base import ChatModel, ModelError, ToolCall, ToolChoice, ToolUseFailed, UsageClock

log = logging.getLogger("jarvis.models")

PRESETS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


class OpenAICompatModel(ChatModel):
    def __init__(self, profile, settings, http=None) -> None:
        super().__init__(profile, settings, http)
        self.provider = profile.provider
        self.base_url = (profile.base_url or PRESETS.get(profile.provider, "")).rstrip("/")

    def unavailable(self) -> str | None:
        if not self.base_url:
            return "openai_compat needs a base_url"
        # A local server (LM Studio, vLLM) usually needs no key.
        if self.provider in PRESETS and not self.key:
            return f"no API key for {self.provider}"
        return super().unavailable()

    async def stream(self, messages, tools=None, *, tool_choice: ToolChoice = "auto",
                     temperature=None, max_tokens=None) -> AsyncIterator[str | ToolCall]:
        try:
            async for item in self._stream(messages, tools, tool_choice, temperature, max_tokens):
                yield item
        except ToolUseFailed as exc:
            if tool_choice == "required":
                raise ModelError(self.provider, 400, str(exc)) from exc
            # Groq returns 400 tool_use_failed when the model writes a call
            # it cannot parse. Better an answer without tools than silence.
            log.warning("%s could not parse a tool call, retrying without tools: %s",
                        self.provider, str(exc)[:160])
            async for item in self._stream(messages, None, "auto", temperature, max_tokens):
                yield item

    async def _stream(self, messages, tools, tool_choice, temperature,
                      max_tokens) -> AsyncIterator[str | ToolCall]:
        payload = {
            "model": self.model,
            "messages": [to_openai_message(m) for m in messages],
            "max_tokens": self.max_tokens(max_tokens),
            "temperature": self.temperature(temperature),
            "stream": True,
        }
        if self.provider in PRESETS:
            # Local servers may reject options they do not know.
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        payload.update(self.profile.extra)
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        pending: dict[int, dict] = {}
        clock = UsageClock()
        client = self.http or httpx.AsyncClient()
        try:
            async with client.stream("POST", f"{self.base_url}/chat/completions", json=payload,
                                     headers=headers, timeout=self.timeout_s) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")[:300]
                    if tools and "tool_use_failed" in body:
                        raise ToolUseFailed(body)
                    raise ModelError(self.provider, resp.status_code, body)
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
                    if usage:
                        clock.prompt_tokens = usage.get("prompt_tokens") or 0
                        clock.completion_tokens = usage.get("completion_tokens") or 0
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    if delta.get("content"):
                        clock.token()
                        yield delta["content"]
                    for part in delta.get("tool_calls") or []:
                        clock.token()
                        slot = pending.setdefault(part.get("index", 0),
                                                  {"id": "", "name": "", "arguments": ""})
                        fn = part.get("function") or {}
                        slot["id"] = part.get("id") or slot["id"]
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
        except httpx.TimeoutException as exc:
            raise ModelError(self.provider, 0, str(exc) or "timed out", timeout=True) from exc
        except httpx.TransportError as exc:
            raise ModelError(self.provider, 0, str(exc) or type(exc).__name__) from exc
        finally:
            if self.http is None:
                await client.aclose()

        for i, slot in sorted(pending.items()):
            if slot["name"]:
                yield ToolCall(id=slot["id"] or f"call_{i}", name=slot["name"],
                               arguments=slot["arguments"] or "{}")
        self.last_usage = clock.finish()


def to_openai_message(m: dict) -> dict:
    """History is already OpenAI-shaped; only image parts and provider
    metadata on tool calls need handling."""
    if m.get("tool_calls") and any("meta" in c for c in m["tool_calls"]):
        m = {**m, "tool_calls": [{k: v for k, v in c.items() if k != "meta"}
                                 for c in m["tool_calls"]]}
    content = m.get("content")
    if not isinstance(content, list):
        return m
    parts = []
    for p in content:
        if p.get("type") == "image":
            b64 = base64.b64encode(p["data"]).decode("ascii")
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{p.get('mime', 'image/jpeg')};base64,{b64}"}})
        else:
            parts.append(p)
    return {**m, "content": parts}
