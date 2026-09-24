"""A primary model plus fallbacks, with a memory for which ones are down.

Generalised from what the web agent learned the hard way: "503 high demand"
is common and brief (retry with backoff), a per-minute 429 clears in seconds
(one short retry), a daily quota does not come back for hours, and a call can
hang for minutes (bounded by each adapter's timeout). A model that failed
that way is skipped for a while instead of paying for its retries again on
every call.

A stream can only fall back before its first token: once words are spoken,
switching model mid-sentence would be worse than failing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable

from .base import ChatModel, ChatResult, ModelError, ToolCall, ToolChoice, Usage

log = logging.getLogger("jarvis.models")

DOWN_S = 180.0
DOWN_DAILY_S = 3600.0
RETRY_DELAYS = (0.0, 1.5, 4.0)

#: (model that answered or failed, usage or None, error or None)
CallHook = Callable[[ChatModel, Usage | None, Exception | None], None]


class FallbackModel:
    """Looks like a ChatModel; tries each candidate in order."""

    def __init__(self, models: list[ChatModel], agent: str = "",
                 on_call: CallHook | None = None) -> None:
        if not models:
            raise ValueError("FallbackModel needs at least one model")
        self.models = models
        self.agent = agent
        self.on_call = on_call
        self._down_until: dict[int, float] = {}
        self.active: ChatModel = models[0]  # the one that answered last

    # ChatModel-like surface, so callers need not know about the chain.
    @property
    def name(self) -> str:
        return self.active.provider

    @property
    def model(self) -> str:
        return self.active.model

    @property
    def last_usage(self) -> Usage | None:
        return self.active.last_usage

    def describe(self) -> str:
        return " -> ".join(m.describe() for m in self.models)

    def unavailable(self) -> str | None:
        reasons = [m.unavailable() for m in self.models]
        if any(r is None for r in reasons):
            return None
        return "; ".join(f"{m.describe()}: {r}" for m, r in zip(self.models, reasons))

    def _candidates(self) -> list[tuple[int, ChatModel]]:
        now = time.monotonic()
        usable = [(i, m) for i, m in enumerate(self.models) if m.unavailable() is None]
        up = [(i, m) for i, m in usable if self._down_until.get(i, 0) <= now]
        # Everything marked down: try anyway rather than refuse outright.
        return up or usable

    def _mark_down(self, i: int, exc: ModelError) -> None:
        secs = DOWN_DAILY_S if exc.daily_quota else DOWN_S
        self._down_until[i] = time.monotonic() + secs
        m = self.models[i]
        log.warning("%s: %s unavailable (%s); skipping it for %d min",
                    self.agent or "model", m.describe(), exc.status or "timeout", secs // 60)

    def _report(self, model: ChatModel, usage: Usage | None, exc: Exception | None) -> None:
        if self.on_call is not None:
            self.on_call(model, usage, exc)

    async def stream(self, messages: list[dict], tools: list[dict] | None = None, *,
                     tool_choice: ToolChoice = "auto", temperature: float | None = None,
                     max_tokens: int | None = None) -> AsyncIterator[str | ToolCall]:
        candidates = self._candidates()
        if not candidates:
            raise ModelError("none", 0, f"no usable model for {self.agent}: {self.unavailable()}")
        last: Exception | None = None
        for n, (i, model) in enumerate(candidates):
            started = False
            try:
                async for item in model.stream(messages, tools, tool_choice=tool_choice,
                                               temperature=temperature, max_tokens=max_tokens):
                    started = True
                    yield item
                self.active = model
                self._report(model, model.last_usage, None)
                return
            except ModelError as exc:
                self._report(model, None, exc)
                last = exc
                if started or not exc.retryable or n == len(candidates) - 1:
                    raise
                self._mark_down(i, exc)
        if last is not None:
            raise last

    async def complete(self, messages: list[dict], tools: list[dict] | None = None, *,
                       tool_choice: ToolChoice = "auto", temperature: float | None = None,
                       max_tokens: int | None = None) -> ChatResult:
        candidates = self._candidates()
        if not candidates:
            raise ModelError("none", 0, f"no usable model for {self.agent}: {self.unavailable()}")
        last: ModelError | None = None
        for n, (i, model) in enumerate(candidates):
            for delay in RETRY_DELAYS:
                if delay:
                    await asyncio.sleep(delay)
                try:
                    result = await model.complete(messages, tools, tool_choice=tool_choice,
                                                  temperature=temperature, max_tokens=max_tokens)
                    self.active = model
                    self._report(model, result.usage, None)
                    return result
                except ModelError as exc:
                    self._report(model, None, exc)
                    last = exc
                    if not exc.retryable:
                        raise
                    # A hang or a daily quota won't fix itself in 4 s, and a
                    # per-minute 429 gets one short retry only.
                    if exc.timeout or exc.status == 0 or exc.daily_quota:
                        break
                    if exc.status == 429 and delay >= RETRY_DELAYS[1]:
                        break
            if n < len(candidates) - 1 and last is not None:
                self._mark_down(i, last)
        assert last is not None
        raise last
