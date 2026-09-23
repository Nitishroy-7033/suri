"""How a capability becomes something the model can call.

    base.py      Tool, ToolContext, and the @tool decorator
    registry.py  ToolRegistry: schemas for both brains, safe execution
    catalog.py   which domain modules provide tools (edit this to add one)
"""

from .base import Tool, ToolContext, tool
from .registry import ToolOutcome, ToolRegistry

__all__ = ["Tool", "ToolContext", "ToolOutcome", "ToolRegistry", "tool"]
