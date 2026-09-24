"""Anthropic Claude through the official `anthropic` SDK.

Optional: the package is only imported when an agent's profile says
provider=anthropic (pip install anthropic).

OpenAI-shaped history converts as: system -> the top-level `system` field,
assistant tool calls -> tool_use blocks, tool results -> tool_result blocks
in one user turn, images -> base64 image blocks.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator

from ..base import (ChatModel, ModelError, ToolCall, ToolChoice, UsageClock,
                    images_of, split_system, text_of)

_CHOICE = {"auto": {"type": "auto"}, "required": {"type": "any"}, "none": {"type": "none"}}


class AnthropicModel(ChatModel):
    provider = "anthropic"

    def __init__(self, profile, settings, http=None) -> None:
        super().__init__(profile, settings, http)
        self._client = None

    def unavailable(self) -> str | None:
        if not self.key:
            return "no ANTHROPIC_API_KEY"
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return "the anthropic package is not installed (pip install anthropic)"
        return super().unavailable()

    @property
    def client(self):
        if self._client is None:
            import anthropic

            kw = {"api_key": self.key, "timeout": self.timeout_s, "max_retries": 0}
            if self.profile.base_url:
                kw["base_url"] = self.profile.base_url
            self._client = anthropic.AsyncAnthropic(**kw)
        return self._client

    async def stream(self, messages, tools=None, *, tool_choice: ToolChoice = "auto",
                     temperature=None, max_tokens=None) -> AsyncIterator[str | ToolCall]:
        import anthropic

        system, rest = split_system(messages)
        kw = {
            "model": self.model,
            "max_tokens": self.max_tokens(max_tokens),
            "messages": to_anthropic_messages(rest),
        }
        if system:
            kw["system"] = system
        # Only when set explicitly: newer Claude models reject sampling params.
        t = temperature if temperature is not None else self.profile.temperature
        if t is not None:
            kw["temperature"] = t
        if tools:
            kw["tools"] = [{"name": f["function"]["name"],
                            "description": f["function"].get("description", ""),
                            "input_schema": f["function"].get("parameters")
                            or {"type": "object", "properties": {}}} for f in tools]
            kw["tool_choice"] = _CHOICE[tool_choice]
        clock = UsageClock()
        try:
            async with self.client.messages.stream(**kw) as stream:
                async for text in stream.text_stream:
                    clock.token()
                    yield text
                final = await stream.get_final_message()
        except anthropic.APITimeoutError as exc:
            raise ModelError(self.provider, 0, str(exc), timeout=True) from exc
        except anthropic.APIStatusError as exc:
            raise ModelError(self.provider, exc.status_code, str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise ModelError(self.provider, 0, str(exc)) from exc
        for block in final.content:
            if block.type == "tool_use":
                yield ToolCall(id=block.id, name=block.name, arguments=json.dumps(block.input or {}))
        if final.usage:
            clock.prompt_tokens = final.usage.input_tokens or 0
            clock.completion_tokens = final.usage.output_tokens or 0
        self.last_usage = clock.finish()


def to_anthropic_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []

    def add(role: str, blocks: list[dict]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "tool":
            add("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                          "content": text_of(content) or str(content or "")}])
            continue
        blocks: list[dict] = []
        for img in images_of(content):
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": img.get("mime", "image/jpeg"),
                "data": base64.b64encode(img["data"]).decode("ascii")}})
        text = text_of(content)
        if text:
            blocks.append({"type": "text", "text": text})
        for call in m.get("tool_calls") or []:
            fn = call["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            blocks.append({"type": "tool_use", "id": call.get("id", ""), "name": fn["name"],
                           "input": args if isinstance(args, dict) else {}})
        if blocks:
            add("assistant" if role == "assistant" else "user", blocks)
    return out
