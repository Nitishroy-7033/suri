"""Provider-agnostic models: one interface, any vendor, chosen per agent.

    base.py       ChatModel, ToolCall, Usage, ModelError
    providers/    openai_compat (OpenAI, Groq, OpenRouter, LM Studio...),
                  gemini, ollama, anthropic
    fallback.py   FallbackModel: primary + fallbacks, skips ones that are down
    hub.py        ModelHub: runtime.models.get("<agent>") from config profiles

To add a provider: subclass ChatModel in providers/, add a branch to
hub.build_model, and add its name to `Provider` in config.py.
"""

from .base import ChatModel, ChatResult, ModelError, ToolCall, ToolUseFailed, Usage
from .fallback import FallbackModel
from .hub import AGENTS, ModelHub, build_model

__all__ = ["AGENTS", "ChatModel", "ChatResult", "FallbackModel", "ModelError",
           "ModelHub", "ToolCall", "ToolUseFailed", "Usage", "build_model"]
