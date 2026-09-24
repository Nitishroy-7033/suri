"""Google Gemini through the ordinary (non-live) generate_content endpoint.

This is not the Live voice brain (domain/brain/gemini_live.py). Here the
tool list can change on every call, which is what the web agent needs, and
pictures ride along as parts, which is what vision needs.

OpenAI-shaped history converts as: assistant -> role "model", tool calls ->
function_call parts, tool results -> function_response parts (named after
the call they answer, which Gemini needs and OpenAI does not carry).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator

from ..base import (ChatModel, ModelError, ToolCall, ToolChoice, UsageClock,
                    images_of, split_system, text_of)

log = logging.getLogger("jarvis.models")

_MODES = {"auto": "AUTO", "required": "ANY", "none": "NONE"}


class GeminiModel(ChatModel):
    provider = "gemini"

    def __init__(self, profile, settings, http=None) -> None:
        super().__init__(profile, settings, http)
        self._client = None

    def unavailable(self) -> str | None:
        if not self.key:
            return "no GEMINI_API_KEY"
        return super().unavailable()

    @property
    def client(self):
        if self._client is None:
            from google import genai

            if not self.key:
                raise ModelError(self.provider, 401, "GEMINI_API_KEY is not set")
            self._client = genai.Client(api_key=self.key)
        return self._client

    def _config(self, system, tools, tool_choice, temperature, max_tokens):
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=self.temperature(temperature),
            max_output_tokens=self.max_tokens(max_tokens),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if tools:
            config.tools = [{"function_declarations": to_gemini_functions(tools)}]
            config.tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode=_MODES[tool_choice]))
        return config

    async def stream(self, messages, tools=None, *, tool_choice: ToolChoice = "auto",
                     temperature=None, max_tokens=None) -> AsyncIterator[str | ToolCall]:
        from google.genai import errors

        system, rest = split_system(messages)
        config = self._config(system, tools, tool_choice, temperature, max_tokens)
        clock = UsageClock()
        n = 0
        try:
            it = await asyncio.wait_for(self.client.aio.models.generate_content_stream(
                model=self.model, contents=to_gemini_contents(rest), config=config), self.timeout_s)
            while True:
                # Bounded per chunk: one call once hung for five minutes.
                try:
                    chunk = await asyncio.wait_for(anext(it), self.timeout_s)
                except StopAsyncIteration:
                    break
                meta = chunk.usage_metadata
                if meta:
                    clock.prompt_tokens = meta.prompt_token_count or clock.prompt_tokens
                    clock.completion_tokens = meta.candidates_token_count or clock.completion_tokens
                for part in _parts(chunk):
                    if part.function_call:
                        n += 1
                        clock.token()
                        fc = part.function_call
                        extra = {}
                        if part.thought_signature:
                            extra["gemini_thought_signature"] = base64.b64encode(
                                part.thought_signature).decode("ascii")
                        yield ToolCall(id=fc.id or f"call_{n}", name=fc.name,
                                       arguments=json.dumps(dict(fc.args or {})), meta=extra)
                    elif part.text and not part.thought:
                        clock.token()
                        yield part.text
        except TimeoutError as exc:
            raise ModelError(self.provider, 0, f"{self.model} gave no answer in {self.timeout_s:.0f} s",
                             timeout=True) from exc
        except errors.APIError as exc:
            raise ModelError(self.provider, getattr(exc, "code", 0) or 0, str(exc)) from exc
        self.last_usage = clock.finish()


def _parts(chunk) -> list:
    if not chunk.candidates:
        return []
    content = chunk.candidates[0].content
    return list(content.parts or []) if content else []


def to_gemini_functions(tools: list[dict]) -> list[dict]:
    out = []
    for t in tools:
        fn = t.get("function", t)
        decl = {"name": fn["name"], "description": fn.get("description", "")}
        params = fn.get("parameters")
        if params and params.get("properties"):
            decl["parameters_json_schema"] = params
        out.append(decl)
    return out


def to_gemini_contents(messages: list[dict]) -> list:
    from google.genai import types

    names: dict[str, str] = {}  # tool_call_id -> function name
    contents: list = []

    def add(role: str, parts: list) -> None:
        # Gemini wants turns to alternate; merge neighbours with one role.
        if contents and contents[-1].role == role:
            contents[-1].parts.extend(parts)
        else:
            contents.append(types.Content(role=role, parts=parts))

    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "tool":
            try:
                result = json.loads(content) if isinstance(content, str) else content
            except json.JSONDecodeError:
                result = content
            if not isinstance(result, dict):
                result = {"result": result}
            add("user", [types.Part.from_function_response(
                name=names.get(m.get("tool_call_id", ""), "tool"), response=result)])
            continue
        parts = []
        text = text_of(content)
        if text:
            parts.append(types.Part.from_text(text=text))
        for img in images_of(content):
            parts.append(types.Part.from_bytes(data=img["data"], mime_type=img.get("mime", "image/jpeg")))
        for call in m.get("tool_calls") or []:
            fn = call["function"]
            names[call.get("id", "")] = fn["name"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            part = types.Part.from_function_call(name=fn["name"], args=args)
            sig = (call.get("meta") or {}).get("gemini_thought_signature")
            if sig:
                part.thought_signature = base64.b64decode(sig)
            parts.append(part)
        if parts:
            add("model" if role == "assistant" else "user", parts)
    return contents
