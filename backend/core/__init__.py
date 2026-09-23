"""Shared plumbing every domain builds on -- nothing here knows about any
one capability.

    runtime.py   per-process state: memory, camera, event bus, tool registry
    events.py    proactive alerts (timers, motion) pushed to sessions
    tools/       the @tool decorator, the registry, and the catalogue
"""
