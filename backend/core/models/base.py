"""One interface for every model provider.

Every agent talks to a ChatModel and never to a vendor SDK, so which company
answers is a line in .env (see ModelProfile in config.py), not code.

Messages are OpenAI-shaped, as the conversation history already is:

    {"role": "system" | "user" | "assistant" | "tool", "content": ...}

`content` is a string, or a list of parts for pictures:

    [{"type": "text", "text": "what is this?"},
     {"type": "image", "data": b"...jpeg...", "mime": "image/jpeg"}]

Tools are always OpenAI function schemas (ToolRegistry.openai_tools()); each
adapter converts them to its own format.

Cancellation rule, same as brain/llm.py: nothing in here may swallow
CancelledError -- never write a bare `except`.
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import httpx

    from ...config import ModelProfile, Settings

ToolChoice = Literal["auto", "required", "none"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text, exactly as the model wrote it
    #: Provider data that must travel back with the call in history, e.g.
    #: Gemini's thought signature. Other providers never see it.
    meta: dict = field(default_factory=dict, compare=False, repr=False)

    def as_message_part(self) -> dict:
        """OpenAI shape, for the assistant message stored in history."""
        part = {"id": self.id, "type": "function",
                "function": {"name": self.name, "arguments": self.arguments}}
        if self.meta:
            part["meta"] = self.meta
        return part


class ToolUseFailed(RuntimeError):
    """The provider could not parse a tool call the model wrote."""


class ModelError(RuntimeError):
    """A provider call failed. `retryable` decides whether a fallback runs."""

    def __init__(self, provider: str, status: int, message: str,
                 timeout: bool = False) -> None:
        super().__init__(f"{provider} {status or ('timeout' if timeout else 'error')}: {message[:300]}")
        self.provider = provider
        self.status = status
        self.timeout = timeout

    @property
    def retryable(self) -> bool:
        # 0 = no HTTP answer at all (connection refused, Ollama not running).
        return self.timeout or self.status in (0, 408, 429, 500, 502, 503, 504, 529)

    @property
    def daily_quota(self) -> bool:
        return self.status == 429 and bool(re.search(r"per ?day|daily", str(self), re.I))


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    first_token_ms: float | None = None
    total_ms: float = 0.0
    #: Generation speed. From the provider's own timing when it reports one
    #: (Ollama), else completion tokens over the time after the first token.
    tok_per_s: float | None = None

    def as_dict(self) -> dict:
        return {"prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "first_token_ms": round(self.first_token_ms) if self.first_token_ms is not None else None,
                "total_ms": round(self.total_ms),
                "tok_per_s": round(self.tok_per_s, 1) if self.tok_per_s else None}


@dataclass
class ChatResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None


class UsageClock:
    """Times one call: first token, total, tokens per second."""

    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.t_first: float | None = None
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.chunks = 0  # fallback token estimate when the provider reports none
        self.tok_per_s: float | None = None

    def token(self) -> None:
        self.chunks += 1
        if self.t_first is None:
            self.t_first = time.perf_counter()

    def finish(self) -> Usage:
        end = time.perf_counter()
        completion = self.completion_tokens or self.chunks
        tps = self.tok_per_s
        if tps is None and self.t_first is not None and completion > 1:
            gen = end - self.t_first
            if gen > 0.01:
                tps = (completion - 1) / gen
        return Usage(
            prompt_tokens=self.prompt_tokens, completion_tokens=completion,
            first_token_ms=(self.t_first - self.t0) * 1000 if self.t_first else None,
            total_ms=(end - self.t0) * 1000, tok_per_s=tps)


class ChatModel(ABC):
    """A model from some provider, configured by one ModelProfile."""

    provider = "model"

    def __init__(self, profile: "ModelProfile", settings: "Settings",
                 http: "httpx.AsyncClient | None" = None) -> None:
        self.profile = profile
        self.settings = settings
        self.http = http
        self.model = profile.model
        self.last_usage: Usage | None = None

    @property
    def name(self) -> str:
        return self.provider

    @property
    def key(self) -> str:
        return self.profile.model_key or self.settings.provider_key(self.provider)

    def temperature(self, override: float | None) -> float:
        for v in (override, self.profile.temperature):
            if v is not None:
                return v
        return self.settings.llm_temperature

    def max_tokens(self, override: int | None) -> int:
        return override or self.profile.max_tokens or self.settings.llm_max_tokens

    @property
    def timeout_s(self) -> float:
        return self.profile.timeout_s or float(self.settings.llm_timeout_s)

    def unavailable(self) -> str | None:
        """Why this model cannot be called, or None. Cheap: no network."""
        if not self.model:
            return f"no model name set for {self.provider}"
        return None

    @abstractmethod
    def stream(self, messages: list[dict], tools: list[dict] | None = None, *,
               tool_choice: ToolChoice = "auto", temperature: float | None = None,
               max_tokens: int | None = None) -> AsyncIterator[str | ToolCall]:
        """Yield text deltas as they arrive, and ToolCalls once complete.

        Sets `last_usage` when the stream ends normally."""

    async def complete(self, messages: list[dict], tools: list[dict] | None = None, *,
                       tool_choice: ToolChoice = "auto", temperature: float | None = None,
                       max_tokens: int | None = None) -> ChatResult:
        """The whole reply at once."""
        text: list[str] = []
        calls: list[ToolCall] = []
        async for item in self.stream(messages, tools, tool_choice=tool_choice,
                                      temperature=temperature, max_tokens=max_tokens):
            if isinstance(item, ToolCall):
                calls.append(item)
            else:
                text.append(item)
        return ChatResult("".join(text).strip(), calls, self.last_usage)

    def describe(self) -> str:
        return f"{self.provider}:{self.model}"


# -- shared message helpers ---------------------------------------------------

def split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Pull the system prompt(s) out, for providers that take it separately."""
    system = "\n\n".join(text_of(m.get("content")) for m in messages if m.get("role") == "system")
    return system, [m for m in messages if m.get("role") != "system"]


def text_of(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")


def images_of(content) -> list[dict]:
    if not isinstance(content, list):
        return []
    return [p for p in content if isinstance(p, dict) and p.get("type") == "image"]
