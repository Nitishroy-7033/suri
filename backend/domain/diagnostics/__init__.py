"""System diagnostics: the laptop's and Jarvis's own health.

    sampler.py   psutil / NVML / Windows counters -> one snapshot dict
    alerts.py    threshold rules with hysteresis and cooldowns (no model)
    metrics.py   voice-turn latency, in-memory warning/error log
    agent.py     DiagnosticsAgent: the loop, its own model, reports, alerts
    tools.py     system_status, unload_model
"""
