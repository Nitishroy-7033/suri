"""Every agent's model, built from its profile in config and cached.

    runtime.models.get("web_agent")          -> a FallbackModel (ChatModel-like)
    runtime.models.available("vision_agent") -> None, or why it can't run
    runtime.models.describe()                -> per-agent stats for the HUD
    runtime.models.reload()                  -> after the Setup page changed a profile

Each call through the hub is recorded, so the diagnostics view gets tokens
per second, latency and error counts for every agent at no extra cost.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import get_args

import httpx

from ...config import ModelProfile, Settings
from .base import ChatModel, ModelError, Usage
from .fallback import FallbackModel

log = logging.getLogger("jarvis.models")


def agent_names(settings_cls: type[Settings] = Settings) -> tuple[str, ...]:
    """Every agent is a ModelProfile field on Settings, in declaration order,
    so a new agent needs only its field (and a default in _builtin_profiles)."""
    return tuple(name for name, f in settings_cls.model_fields.items()
                 if f.annotation is ModelProfile or ModelProfile in get_args(f.annotation))


AGENTS = agent_names()
LOCAL_PROVIDERS = ("ollama", "openai_compat")


def build_model(profile: ModelProfile, settings: Settings,
                http: httpx.AsyncClient | None = None) -> ChatModel:
    p = profile.provider
    if p in ("openai", "groq", "openrouter", "openai_compat"):
        from .providers.openai_compat import OpenAICompatModel
        return OpenAICompatModel(profile, settings, http)
    if p == "ollama":
        from .providers.ollama import OllamaModel
        return OllamaModel(profile, settings, http)
    if p == "gemini":
        from .providers.gemini import GeminiModel
        return GeminiModel(profile, settings, http)
    if p == "anthropic":
        from .providers.anthropic import AnthropicModel
        return AnthropicModel(profile, settings, http)
    raise ValueError(f"unknown model provider {p!r}")


@dataclass
class AgentStats:
    calls: int = 0
    errors: int = 0
    last_model: str = ""
    last_error: str = ""
    last_error_ts: float = 0.0
    last_ok_ts: float = 0.0
    last_usage: Usage | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=20))  # Usage

    def summary(self) -> dict:
        tps = [u.tok_per_s for u in self.recent if u.tok_per_s]
        first = [u.first_token_ms for u in self.recent if u.first_token_ms is not None]
        return {
            "calls": self.calls, "errors": self.errors, "model": self.last_model,
            "last_error": self.last_error, "last_error_ts": self.last_error_ts,
            "last_ok_ts": self.last_ok_ts,
            "last": self.last_usage.as_dict() if self.last_usage else None,
            "avg_tok_per_s": round(sum(tps) / len(tps), 1) if tps else None,
            "avg_first_token_ms": round(sum(first) / len(first)) if first else None,
        }


class ModelHub:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._http: httpx.AsyncClient | None = None
        self._models: dict[tuple[str, bool], FallbackModel] = {}
        #: What each chain was built from, keys included: models read their
        #: key live, but Gemini's client is made with it once.
        self._built: dict[tuple[str, bool], list] = {}
        self.stats: dict[str, AgentStats] = {a: AgentStats() for a in AGENTS}

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(http2=False)
        return self._http

    def chain(self, agent: str, local_only: bool = False) -> list[ModelProfile]:
        profile = self.settings.agent_profile(agent)
        chain = [profile.model_copy(update={"fallback": []})]
        todo = list(profile.fallback)
        while todo:  # flatten nested fallbacks, in order
            p = todo.pop(0)
            chain.append(p.model_copy(update={"fallback": []}))
            todo[:0] = p.fallback
        if local_only:
            chain = [p for p in chain if p.provider in LOCAL_PROVIDERS]
        return chain

    def get(self, agent: str, local_only: bool = False) -> FallbackModel:
        key = (agent, local_only)
        if key not in self._models:
            profiles = self.chain(agent, local_only)
            if not profiles:
                raise ModelError("none", 0, f"{agent} has no local model configured")
            models = [build_model(p, self.settings, self.http) for p in profiles]
            self._models[key] = FallbackModel(models, agent, self._recorder(agent))
            self._built[key] = self._fingerprint(profiles)
        return self._models[key]

    def reload(self) -> list[str]:
        """Rebuild every chain already handed out from the current settings.

        The FallbackModel objects are kept and refilled, because their
        holders keep them: a session's pipeline for its whole life, the web
        agent and vision for the process. Returns the agents whose chain
        changed; their stats start over, so the HUD's tokens per second is
        never an average across two different models."""
        changed = []
        for (agent, local_only), fm in list(self._models.items()):
            try:
                profiles = self.chain(agent, local_only)
            except KeyError:
                continue
            if not profiles:
                log.warning("%s: no local model left in its chain; keeping the old one", agent)
                continue
            built = self._fingerprint(profiles)
            if built == self._built.get((agent, local_only)):
                continue
            fm.replace([build_model(p, self.settings, self.http) for p in profiles])
            self._built[(agent, local_only)] = built
            if not local_only:
                changed.append(agent)
        for agent in changed:
            self.stats[agent] = AgentStats()
            log.info("model %-17s now %s", agent,
                     " -> ".join(f"{p.provider}:{p.model}" for p in self.chain(agent)))
        return changed

    def _fingerprint(self, profiles: list[ModelProfile]) -> list:
        return [(p.model_dump(), p.model_key or self.settings.provider_key(p.provider or ""))
                for p in profiles]

    def available(self, agent: str) -> str | None:
        try:
            return self.get(agent).unavailable()
        except (KeyError, ValueError, ModelError) as exc:
            return str(exc)

    def _recorder(self, agent: str):
        def record(model: ChatModel, usage: Usage | None, exc: Exception | None) -> None:
            # Looked up per call: reload() may have started this agent over.
            stats = self.stats.setdefault(agent, AgentStats())
            stats.calls += 1
            stats.last_model = model.describe()
            if exc is not None:
                stats.errors += 1
                stats.last_error = str(exc)[:200]
                stats.last_error_ts = time.time()
                return
            stats.last_ok_ts = time.time()
            if usage is not None:
                stats.last_usage = usage
                stats.recent.append(usage)

        return record

    def record_error(self, agent: str, message: str) -> None:
        """For failures the caller saw but the chain did not (its own timeout)."""
        stats = self.stats.setdefault(agent, AgentStats())
        stats.errors += 1
        stats.last_error = message[:200]
        stats.last_error_ts = time.time()

    def describe(self) -> list[dict]:
        out = []
        for agent in AGENTS:
            try:
                chain = self.chain(agent)
            except KeyError:
                continue
            fm = self._models.get((agent, False))
            active = fm.active.describe() if fm else f"{chain[0].provider}:{chain[0].model}"
            out.append({"agent": agent,
                        "chain": [f"{p.provider}:{p.model}" for p in chain],
                        "active": active,
                        "unavailable": self.available(agent),
                        **self.stats[agent].summary()})
        return out

    def log_summary(self) -> None:
        for agent in AGENTS:
            chain = " -> ".join(f"{p.provider}:{p.model}" for p in self.chain(agent))
            why = self.available(agent)
            log.info("model %-17s %s%s", agent, chain, f"  (unavailable: {why})" if why else "")

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
