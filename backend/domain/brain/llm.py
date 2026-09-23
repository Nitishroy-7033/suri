"""Streaming chat completion.

Streaming is not a nicety here: the clause splitter needs tokens as they
arrive so TTS can start on the first few words. Waiting for a complete reply
would add seconds before Jarvis makes a sound.

With tools, the stream carries two kinds of item: text deltas (str), which
go to the clause splitter as before, and ToolCall objects, which arrive once
the model has finished writing a call's arguments. Text a model says before
calling a tool ("let me check") is still spoken straight away.

Cancellation matters just as much. When you interrupt, the task running this
generator is cancelled, which raises inside `aiter_lines()` and closes the
connection. That only works if nothing in here swallows CancelledError --
never write a bare `except` in this file.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from ...config import Settings

log = logging.getLogger("jarvis.llm")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text, exactly as the model wrote it

    def as_message_part(self) -> dict:
        """OpenAI shape, for the assistant message stored in history."""
        return {"id": self.id, "type": "function",
                "function": {"name": self.name, "arguments": self.arguments}}


class ToolUseFailed(RuntimeError):
    """The provider could not parse a tool call the model wrote."""


class LlmEngine(ABC):
    name = "llm"

    @abstractmethod
    def stream(self, messages: list[dict],
               tools: list[dict] | None = None) -> AsyncIterator[str | ToolCall]:
        """Yield text deltas as they arrive, and ToolCalls once complete."""


class GroqLlm(LlmEngine):
    name = "groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self._client = client
        self.model = settings.groq_llm_model
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not set")

    async def stream(self, messages: list[dict],
                     tools: list[dict] | None = None) -> AsyncIterator[str | ToolCall]:
        try:
            async for item in self._stream(messages, tools):
                yield item
        except ToolUseFailed as exc:
            # Groq returns 400 tool_use_failed when the model writes a call
            # it cannot parse. Better an answer without tools than silence.
            log.warning("groq could not parse a tool call, retrying without "
                        "tools: %s", str(exc)[:160])
            async for item in self._stream(messages, None):
                yield item

    async def _stream(self, messages: list[dict],
                      tools: list[dict] | None) -> AsyncIterator[str | ToolCall]:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.settings.llm_max_tokens,
            "temperature": self.settings.llm_temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self.settings.groq_api_key}"}
        # Tool calls stream as fragments keyed by index: the name first, then
        # the JSON arguments a few characters at a time.
        pending: dict[int, dict] = {}

        async with self._client.stream(
            "POST", self.URL, json=payload, headers=headers,
            timeout=self.settings.llm_timeout_s,
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")[:300]
                if tools and "tool_use_failed" in body:
                    raise ToolUseFailed(body)
                raise RuntimeError(f"groq {resp.status_code}: {body}")
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
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    yield delta["content"]
                for part in delta.get("tool_calls") or []:
                    slot = pending.setdefault(part.get("index", 0),
                                              {"id": "", "name": "", "arguments": ""})
                    fn = part.get("function") or {}
                    slot["id"] = part.get("id") or slot["id"]
                    slot["name"] += fn.get("name") or ""
                    slot["arguments"] += fn.get("arguments") or ""

        for i, slot in sorted(pending.items()):
            if slot["name"]:
                yield ToolCall(id=slot["id"] or f"call_{i}", name=slot["name"],
                               arguments=slot["arguments"] or "{}")


class OllamaLlm(LlmEngine):
    """Local fallback. No key, no network, but slow on a CPU without a GPU."""

    name = "ollama"

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self._client = client
        self.model = settings.ollama_model

    async def stream(self, messages: list[dict],
                     tools: list[dict] | None = None) -> AsyncIterator[str | ToolCall]:
        payload = {
            "model": self.model,
            "messages": [self._to_ollama(m) for m in messages],
            "stream": True,
            "options": {
                "num_thread": self.settings.ollama_num_thread,
                "temperature": self.settings.llm_temperature,
                "num_predict": self.settings.llm_max_tokens,
            },
        }
        if tools:
            payload["tools"] = tools
        url = f"{self.settings.ollama_host.rstrip('/')}/api/chat"
        n_calls = 0
        async with self._client.stream("POST", url, json=payload,
                                       timeout=self.settings.llm_timeout_s) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = chunk.get("message") or {}
                if message.get("content"):
                    yield message["content"]
                # Ollama sends each tool call whole, with arguments as an
                # object rather than JSON text.
                for call in message.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    n_calls += 1
                    yield ToolCall(id=f"call_{n_calls}", name=fn.get("name", ""),
                                   arguments=json.dumps(fn.get("arguments") or {}))
                if chunk.get("done"):
                    return

    @staticmethod
    def _to_ollama(m: dict) -> dict:
        """History is stored OpenAI-shaped; Ollama wants arguments as objects."""
        if m.get("role") != "assistant" or not m.get("tool_calls"):
            return m
        calls = []
        for c in m["tool_calls"]:
            fn = c["function"]
            try:
                args = json.loads(fn["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"function": {"name": fn["name"], "arguments": args}})
        return {"role": "assistant", "content": m.get("content") or "",
                "tool_calls": calls}
