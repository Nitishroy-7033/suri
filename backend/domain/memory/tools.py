"""Remembering things about the user, across restarts."""

from __future__ import annotations

from ...core.tools.base import ToolContext, tool


@tool(
    "remember",
    "Save a fact the user wants you to remember long-term, e.g. a name, a "
    "preference, where they left something.",
    params={"fact": {"type": "string",
                     "description": "One self-contained sentence, e.g. "
                                    "'The user's sister is called Asha.'"}},
    required=["fact"],
)
async def remember(ctx: ToolContext, fact: str) -> str:
    ctx.runtime.memory.add(fact)
    return "saved"


@tool(
    "recall",
    "Look up facts you were asked to remember.",
    params={"query": {"type": "string", "description": "What to look for."}},
    required=["query"],
)
async def recall(ctx: ToolContext, query: str) -> str:
    hits = ctx.runtime.memory.search(query)
    if not hits:
        return "nothing remembered about that"
    return "\n".join(f"- {f.text}" for f in hits)


@tool(
    "forget",
    "Delete a remembered fact when the user asks you to forget it.",
    params={"query": {"type": "string", "description": "Which fact to forget."}},
    required=["query"],
)
async def forget(ctx: ToolContext, query: str) -> str:
    gone = ctx.runtime.memory.forget(query)
    if not gone:
        return "nothing matched"
    return "forgot: " + "; ".join(f.text for f in gone)
