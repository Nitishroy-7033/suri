"""The pipeline's LLM, now just a view onto the shared model layer.

The pipeline gets its model from `runtime.models.get("voice_llm")` (set
VOICE_LLM__PROVIDER / VOICE_LLM__MODEL in .env). The real adapters live in
core/models/providers; the names below stay for older imports and tests.

Streaming still matters as much as ever: the clause splitter needs tokens as
they arrive so TTS can start on the first few words.
"""

from __future__ import annotations

import httpx

from ...config import ModelProfile, Settings
from ...core.models.base import ChatModel as LlmEngine
from ...core.models.base import ToolCall, ToolUseFailed
from ...core.models.providers.ollama import OllamaModel
from ...core.models.providers.openai_compat import OpenAICompatModel

__all__ = ["GroqLlm", "LlmEngine", "OllamaLlm", "ToolCall", "ToolUseFailed"]


def GroqLlm(settings: Settings, client: httpx.AsyncClient) -> OpenAICompatModel:
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set")
    return OpenAICompatModel(ModelProfile(provider="groq", model=settings.groq_llm_model),
                             settings, client)


def OllamaLlm(settings: Settings, client: httpx.AsyncClient) -> OllamaModel:
    return OllamaModel(ModelProfile(provider="ollama", model=settings.ollama_model),
                       settings, client)
