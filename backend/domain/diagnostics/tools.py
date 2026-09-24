"""What Jarvis can ask of the diagnostics agent.

The answer is already phrased by the diagnostics agent's own model, so the
voice model only has to say it.
"""

from __future__ import annotations

from ...core.tools import ToolContext, tool


def _off(ctx: ToolContext) -> str | None:
    if ctx.runtime.diag is None:
        return "diagnostics disabled (DIAG_ENABLED=false)"
    return None


def _no_ollama(ctx: ToolContext) -> str | None:
    return _off(ctx) or (None if ctx.runtime.diag.ollama.get("reachable")
                         else "Ollama is not running")


@tool(
    "system_status",
    "Status report on the laptop and on Jarvis itself: CPU, memory, GPU, battery, disk, "
    "network, heavy processes, which models are running and how fast, and recent errors. "
    "Use for 'status report', 'how is my laptop doing', battery, temperature, memory or "
    "'what's slowing my laptop' questions. The result is already phrased for speaking: "
    "say it as it is.",
    params={"question": {"type": "string",
                         "description": "What the user wants to know, e.g. 'is my battery ok?'. "
                                        "Empty for a general status report."}},
    unavailable=_off,
)
async def system_status(ctx: ToolContext, question: str = "") -> str:
    return await ctx.runtime.diag.report(question)


@tool(
    "unload_model",
    "Unload a local Ollama model from memory to free RAM or GPU memory. Use when the user "
    "agrees to free memory after a low-memory or battery warning.",
    params={"model": {"type": "string",
                      "description": "The model name, e.g. 'qwen2.5:1.5b'. Empty for every loaded model."}},
    action=True,
    unavailable=_no_ollama,
)
async def unload_model(ctx: ToolContext, model: str = "") -> str:
    from ...core.models.providers.ollama import ollama_ps, ollama_unload

    diag = ctx.runtime.diag
    host = ctx.settings.ollama_host
    http = ctx.runtime.models.http
    loaded = [m["name"] for m in (await ollama_ps(http, host)).get("models", [])]
    if not loaded:
        return "No local models are loaded."
    targets = loaded if not model.strip() else [m for m in loaded if model.strip().lower() in m.lower()]
    if not targets:
        return f"No loaded model matches {model!r}. Loaded: {', '.join(loaded)}."
    for name in targets:
        await ollama_unload(http, host, name)
    diag.ollama = await ollama_ps(http, host)
    return f"Unloaded {', '.join(targets)}."
