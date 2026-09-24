"""Which domains provide tools.

To add a capability: create `domain/<name>/tools.py` with one or more `@tool`
functions, then add it to DOMAIN_TOOL_MODULES. Nothing else changes -- both
brains pick it up from the registry.

TOOLS_ENABLED=all offers everything available; a comma-separated list
("get_datetime,web_search") offers only those; an empty value turns tools
off entirely.

Kept apart from tools/__init__.py on purpose: domain tool modules import
`core.tools`, so the package itself must not import them back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...domain.diagnostics import tools as diagnostics
from ...domain.forge import tools as forge
from ...domain.fs import tools as fs
from ...domain.holo import tools as holo
from ...domain.pc import tools as pc
from ...domain.memory import tools as memory
from ...domain.system import tools as system
from ...domain.vision import tools as vision
from ...domain.web import tools as web
from ...domain.webagent import tools as webagent
from .base import ToolContext
from .registry import ToolRegistry

if TYPE_CHECKING:
    from ...config import Settings
    from ..runtime import AgentRuntime

DOMAIN_TOOL_MODULES = [system, web, memory, vision, webagent, diagnostics, holo, fs, pc, forge]


def build_registry(settings: "Settings", runtime: "AgentRuntime",
                   brain: str = "") -> ToolRegistry:
    reg = ToolRegistry(
        ToolContext(settings=settings, runtime=runtime, brain=brain),
        timeout_s=settings.tool_timeout_s,
        max_chars=settings.tool_result_max_chars,
        allow_actions=settings.tools_allow_actions,
    )
    spec = settings.tools_enabled.strip()
    if not spec:
        return reg
    enabled = None if spec == "all" else {s.strip() for s in spec.split(",") if s.strip()}
    for module in DOMAIN_TOOL_MODULES:
        reg.register_module(module, enabled)
    return reg
