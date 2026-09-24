"""The Setup page's API: every agent's model, the API keys, Ollama's model
library, and the rest of .env -- read, changed, and applied without a restart
wherever the code allows it.

    GET  /api/setup/state                 everything the page shows
    PUT  /api/setup/agents/{agent}        one agent's model chain
    POST /api/setup/test                  one model, one short probe
    GET  /api/setup/models?provider=...   what a provider offers
    PUT  /api/setup/keys                  provider API keys
    POST /api/setup/keys/check            does this key work?
    PUT  /api/setup/fields                every other setting
    GET  /api/setup/ollama                installed, loaded, downloading
    POST /api/setup/ollama/{pull,cancel,delete,load,unload}

main.py includes this router behind require_token: it reads and writes API
keys and downloads multi-GB files.

Saving always goes the same way: render the new .env text, build a Settings
from it (a typo must not stop the next start), swap the file in, copy the new
values onto the live settings object and rebuild the models that changed.
Models and keys therefore apply at once; what a session reads when it starts
applies from the next connection; what is built at startup (camera, sampler)
needs a restart, and the page says which is which. Real environment variables
beat .env -- pydantic-settings reads them first -- and the page is told when
one hides a change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import struct
import tempfile
import time
import zlib
from dataclasses import dataclass, field, fields as dc_fields
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from . import config, envfile
from .config import ModelProfile, Provider, Settings, Tunables, settings as boot_settings
from .core.models import AGENTS, ModelError, ToolCall, build_model
from .core.models.providers import ollama as ollama_api
from .core.models.providers.openai_compat import PRESETS

log = logging.getLogger("jarvis.setup")

router = APIRouter(prefix="/api/setup")

_lock = asyncio.Lock()  # one save at a time: read, render, check, write, apply

# -- what the page knows about agents and providers ------------------------------

AGENT_INFO: dict[str, tuple[str, str, list[str]]] = {
    "voice_llm": ("Voice LLM", "Answers you when the pipeline brain runs (Gemini Live "
                  "unavailable, or Brain set to Pipeline). Speed matters: it streams "
                  "into speech.", ["tools"]),
    "web_agent": ("Web agent", "Drives Chrome for website jobs, one tool call per step.",
                  ["tools"]),
    "vision_agent": ("Vision", "Describes camera frames and screenshots.", ["vision"]),
    "diagnostics_agent": ("Diagnostics", "Words status reports and spoken alerts. Small and "
                          "local on purpose, so it keeps working without cloud quota.", []),
    "file_agent": ("Files", "Reads a file you asked about (text, PDF, image) and answers.",
                   ["vision"]),
}

PROVIDER_INFO: dict[str, dict] = {
    "groq": {"label": "Groq", "key": "groq_api_key",
             "keys_url": "https://console.groq.com/keys"},
    "gemini": {"label": "Google Gemini", "key": "gemini_api_key",
               "keys_url": "https://aistudio.google.com/apikey"},
    "openai": {"label": "OpenAI", "key": "openai_api_key",
               "keys_url": "https://platform.openai.com/api-keys"},
    "anthropic": {"label": "Anthropic", "key": "anthropic_api_key",
                  "keys_url": "https://console.anthropic.com/settings/keys"},
    "openrouter": {"label": "OpenRouter", "key": "openrouter_api_key",
                   "keys_url": "https://openrouter.ai/keys"},
    "ollama": {"label": "Ollama (this PC)", "key": None, "local": True},
    "openai_compat": {"label": "OpenAI-compatible server", "key": None, "local": True,
                      "base_url": True},
}

#: Keys that belong to no model provider.
KEY_NOTES = {
    "poly_pizza_api_key": ("Poly Pizza", "Free 3D model search for the workshop"),
    "tripo_api_key": ("Tripo", "3D model generation for the workshop (paid credits)"),
    "meshy_api_key": ("Meshy", "3D model generation for the workshop (paid credits)"),
}

#: Small enough for this CPU-only laptop (gemma4:e2b, 5B, runs at ~3.5 tok/s).
SUGGESTED_OLLAMA = [
    {"name": "qwen2.5:1.5b", "size": "1.0 GB", "caps": ["tools"],
     "why": "Fast here; the diagnostics agent's default"},
    {"name": "qwen2.5:0.5b", "size": "0.4 GB", "caps": ["tools"],
     "why": "The fastest; fine for one-line status reports"},
    {"name": "llama3.2:3b", "size": "2.0 GB", "caps": ["tools"],
     "why": "Better answers, still usable as an offline voice LLM"},
    {"name": "gemma3:1b", "size": "0.8 GB", "caps": [],
     "why": "Small Google model, no tool calling"},
    {"name": "qwen2.5vl:3b", "size": "3.2 GB", "caps": ["vision"],
     "why": "Local vision: can describe camera frames"},
    {"name": "moondream", "size": "1.7 GB", "caps": ["vision"],
     "why": "Tiny vision model"},
]

GEMINI_VOICES = ["Puck", "Charon", "Kore", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr"]

# -- the other settings, in plain words ------------------------------------------


@dataclass
class F:
    key: str
    label: str
    hint: str = ""
    #: When a change takes effect: "now", "reconnect" (the next connection --
    #: what a session reads when it starts), or "restart".
    apply: str = "restart"
    kind: str = ""  # "" = from the type; "text" = multi-line; "tools" = tool picker
    options: list | None = None  # [(value, label)] for a select
    suggest: list | None = None  # free text, with these offered
    min: float | None = None
    max: float | None = None


@dataclass
class Section:
    id: str
    tab: str
    title: str
    note: str
    fields: list[F] = field(default_factory=list)


SECTIONS = [
    Section("model_defaults", "agents", "Defaults for every agent",
            "Used when an agent leaves its own field empty.", [
                F("llm_temperature", "Temperature", "Higher is more varied, lower more "
                  "predictable.", "now", min=0, max=2),
                F("llm_max_tokens", "Max reply tokens", "Voice replies are short; 160 is "
                  "about two spoken sentences.", "now", min=16, max=32768),
                F("llm_timeout_s", "Timeout (seconds)", "How long one model call may take "
                  "before the next fallback is tried.", "now", min=3, max=600),
            ]),
    Section("ollama", "ollama", "Ollama server", "", [
        F("ollama_host", "Address", "Where Ollama listens. The Ollama app uses "
          "http://127.0.0.1:11434.", "now"),
        F("ollama_num_thread", "CPU threads", "Threads per request. More is faster up to "
          "your physical core count.", "now", min=1, max=64),
    ]),
    Section("voice", "voice", "Voice", "How Jarvis listens and speaks. The voice-detection "
            "knobs you tune while testing are in the settings drawer.", [
        F("jarvis_mode", "Brain", "Auto tries Gemini Live first, then the pipeline "
          "(speech-to-text, the Voice LLM agent, text-to-speech). Offline keeps to local "
          "models.", "reconnect", options=[
              ("auto", "Auto"), ("gemini_live", "Gemini Live only"),
              ("pipeline", "Pipeline: speech-to-text, Voice LLM, text-to-speech"),
              ("offline", "Offline: local models only")]),
        F("gemini_live_model", "Gemini Live model", "The realtime voice brain (needs "
          "GEMINI_API_KEY). Separate from the agents.", "reconnect"),
        F("gemini_voice", "Gemini Live voice", "", "reconnect", suggest=GEMINI_VOICES),
        F("tts_engine", "Pipeline speech engine", "Groq's Orpheus is faster; edge-tts "
          "needs no key.", "reconnect",
          options=[("auto", "Auto"), ("groq", "Groq Orpheus"), ("edge", "edge-tts")]),
        F("tts_voice_en", "English voice (edge-tts)", "", "now",
          suggest=["en-US-AndrewMultilingualNeural", "en-US-AvaMultilingualNeural",
                   "en-GB-RyanNeural", "en-IN-PrabhatNeural", "en-IN-NeerjaNeural"]),
        F("tts_voice_hi", "Hindi voice (edge-tts)", "", "now",
          suggest=["hi-IN-MadhurNeural", "hi-IN-SwaraNeural"]),
        F("groq_stt_model", "Speech-to-text model (Groq)", "", "now",
          suggest=["whisper-large-v3-turbo", "whisper-large-v3"]),
        F("system_prompt", "Persona", "What Jarvis is told at the start of every "
          "conversation. It is spoken aloud, so keep the no-markdown rules.",
          "reconnect", kind="text"),
        F("max_history_turns", "Turns remembered", "Earlier turns are dropped from the "
          "conversation after this many.", "reconnect", min=1, max=100),
    ]),
    Section("tools", "tools", "Tools", "What Jarvis may do for you in a conversation.", [
        F("tools_enabled", "Tools offered to the model", "", "reconnect", kind="tools"),
        F("tools_allow_actions", "Allow actions", "Opening apps and websites, unloading "
          "models. Read-only tools are unaffected.", "reconnect"),
        F("tool_timeout_s", "Tool timeout (seconds)", "", "reconnect", min=1, max=120),
        F("max_tool_rounds", "Tool rounds per reply", "Each round is another model call "
          "before Jarvis can answer.", "now", min=0, max=10),
        F("memory_prompt_facts", "Remembered facts in the prompt", "", "now", min=0, max=200),
        F("app_allowlist", "Apps Jarvis may open", "Name → what Windows runs, as JSON.",
          "now"),
    ]),
    Section("web", "web", "Web agent", "A separate agent that drives a real Chrome window for "
            "website jobs.", [
        F("web_agent_enabled", "Web agent", "Lets Jarvis hand website jobs to it.",
          "reconnect"),
        F("web_agent_max_steps", "Max steps per job", "", "now", min=1, max=200),
        F("browser_confirm_risky", "Ask before risky clicks", "Buy, pay, delete, send, "
          "post. Keep this on.", "now"),
        F("browser_blocked_domains", "Blocked sites", "Comma-separated, e.g. "
          "bank.com,paypal.com", "now"),
        F("browser_idle_close_s", "Close the browser after idle (seconds)", "", "now",
          min=30, max=86400),
        F("browser_channel", "Browser", "", "restart",
          options=[("chrome", "Chrome"), ("msedge", "Edge"), ("chromium", "Chromium "
                   "(needs playwright install)")]),
        F("browser_single_tab", "Keep to one tab", "Links that open a new tab open in "
          "the same one, so follow-ups continue where it left off.", "restart"),
    ]),
    Section("diagnostics", "diagnostics", "Diagnostics",
            "The Systems view, “Jarvis, status report”, and spoken warnings.", [
                F("diag_alerts", "Spoken alerts", "Speak up when a threshold below is "
                  "crossed.", "now"),
                F("diag_battery_low", "Battery low (%)", "", "now", min=1, max=100),
                F("diag_battery_critical", "Battery critical (%)", "", "now", min=1, max=100),
                F("diag_temp_hot_c", "Too hot (°C)", "", "now", min=40, max=110),
                F("diag_ram_high_pct", "Memory high (%)", "", "now", min=50, max=100),
                F("diag_disk_low_gb", "Disk low (GB free)", "", "now", min=0, max=1000),
                F("diag_alert_cooldown_s", "Repeat an alert after (seconds)", "", "now",
                  min=10, max=86400),
                F("diag_enabled", "Diagnostics", "", "restart"),
                F("diag_interval_s", "Sample every (seconds)", "", "restart", min=0.5, max=60),
                F("diag_ping_host", "Ping host", "Used to measure internet latency.",
                  "restart"),
            ]),
    Section("camera", "camera", "Camera", "Needs opencv-python. The camera light only "
            "comes on when you turn this on.", [
                F("camera_enabled", "Camera", "", "restart"),
                F("camera_index", "Camera number", "0 is the built-in one.", "restart",
                  min=0, max=10),
                F("motion_watch_on_start", "Watch for motion at start", "", "restart"),
                F("motion_cooldown_s", "Motion alert cooldown (seconds)", "", "restart",
                  min=1, max=3600),
            ]),
]

#: Never on the page: who can reach the server, and the audio wire format
#: (changing those means changing protocol.js too).
HIDDEN = {"host", "port", "allowed_hosts", "mic_sample_rate", "tts_sample_rate",
          "wire_frame_samples", "vad_frame_samples", "tts_chunk_samples"}

#: Read on every call, so a change applies at once (checked in the code).
LIVE = {"llm_max_tokens", "llm_temperature", "llm_timeout_s", "ollama_host",
        "ollama_num_thread", "web_agent_max_steps", "browser_confirm_risky",
        "browser_blocked_domains", "browser_idle_close_s", "max_tool_rounds",
        "memory_prompt_facts", "app_allowlist", "tts_voice_en", "tts_voice_hi",
        "groq_stt_model", "diag_alerts", "diag_battery_low", "diag_battery_critical",
        "diag_temp_hot_c", "diag_ram_high_pct", "diag_disk_low_gb", "diag_alert_cooldown_s"}
#: Read when a session starts: the voice knobs, the brain, the tool list.
PER_SESSION = ({f.name for f in dc_fields(Tunables)}
               | {"jarvis_mode", "gemini_live_model", "gemini_voice", "system_prompt",
                  "tts_engine", "max_history_turns", "tools_enabled", "tools_allow_actions",
                  "tool_timeout_s", "web_agent_enabled", "gemini_session_idle_s",
                  "gemini_local_wake", "tts_prebuffer_ms", "history_ttl_s"})


def _apply_level(name: str) -> str:
    if name in AGENTS or name.endswith("_api_key") or name in LIVE:
        return "now"
    return "reconnect" if name in PER_SESSION else "restart"


# What the server started with, to tell which restart-only changes are pending.
_BOOT = {name: getattr(boot_settings, name) for name in Settings.model_fields}


# -- reading config.py's own comments, for the Advanced tab ----------------------

def _config_notes() -> tuple[dict[str, str], dict[str, str]]:
    """Each Settings field's comment in config.py, and the "# --- x ---"
    heading it sits under. The comments are the best docs these knobs have."""
    notes: dict[str, str] = {}
    groups: dict[str, str] = {}
    try:
        lines = Path(config.__file__).read_text(encoding="utf-8").splitlines()
    except OSError:
        return notes, groups
    inside, pending, heading = False, [], "Other"
    for line in lines:
        if line.startswith("class Settings("):
            inside = True
            continue
        if not inside:
            continue
        if line.startswith(("    def ", "    @")) or line.startswith("class "):
            break
        text = line.strip()
        m = re.match(r"#\s*---\s*(.*?)\s*-{2,}\s*$", text)
        if m:
            heading, pending = m.group(1), []
            continue
        if text.startswith("#"):
            pending.append(text.lstrip("#").strip())
            continue
        m = re.match(r"^    ([a-z_][a-z0-9_]*)\s*:", line)
        if m:
            inline = line.split("  # ", 1)[1].strip() if "  # " in line else ""
            notes[m.group(1)] = " ".join(pending + ([inline] if inline else []))
            groups[m.group(1)] = heading
            pending = []
        elif not text:
            pending = []
    return notes, groups


NOTES, GROUPS = _config_notes()


# -- field types -----------------------------------------------------------------

def _kind(name: str) -> tuple[str, list | None, bool]:
    """("bool" | "int" | "float" | "str" | "choice" | "json", choices, nullable)."""
    ann = Settings.model_fields[name].annotation
    args = get_args(ann)
    nullable = type(None) in args
    if nullable:
        ann = next(a for a in args if a is not type(None))
    if get_origin(ann) is Literal:
        return "choice", list(get_args(ann)), nullable
    if ann is bool:
        return "bool", None, nullable
    if ann is int:
        return "int", None, nullable
    if ann is float:
        return "float", None, nullable
    if ann is str:
        return "str", None, nullable
    return "json", None, nullable


def _env_value(name: str, value: Any) -> str | None:
    kind, _, _ = _kind(name)
    if value is None or (value == "" and kind in ("int", "float")):
        return None
    if kind == "bool":
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        return "true" if value else "false"
    if kind == "json":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise HTTPException(400, f"{name.upper()}: not valid JSON ({exc.msg})") from exc
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def _field_out(f: F, s: Settings) -> dict:
    kind, choices, nullable = _kind(f.key)
    out = {"key": f.key, "env": f.key.upper(), "label": f.label, "hint": f.hint,
           "apply": f.apply, "type": f.kind or kind, "nullable": nullable,
           "value": getattr(s, f.key), "default": Settings.model_fields[f.key].default,
           "min": f.min, "max": f.max, "suggest": f.suggest}
    if f.options:  # a plain str in config, but only these values make sense
        out["options"] = [list(o) for o in f.options]
        out["type"] = "choice"
    elif choices:
        out["options"] = [[c, c] for c in choices]
    if kind == "json":
        out["value"] = json.dumps(out["value"], ensure_ascii=False)
        out["default"] = json.dumps(out["default"], ensure_ascii=False)
    return out


def _sections(s: Settings) -> list[dict]:
    shown = {f.key for sec in SECTIONS for f in sec.fields}
    out = [{"id": sec.id, "tab": sec.tab, "title": sec.title, "note": sec.note,
            "fields": [_field_out(f, s) for f in sec.fields]} for sec in SECTIONS]
    # Everything else, grouped the way config.py groups it.
    groups: dict[str, list[dict]] = {}
    for name in Settings.model_fields:
        if (name in shown or name in HIDDEN or name in AGENTS or name.endswith("_api_key")):
            continue
        f = F(name, name.upper(), NOTES.get(name, ""), _apply_level(name))
        groups.setdefault(GROUPS.get(name, "Other"), []).append(_field_out(f, s))
    for heading, flds in groups.items():
        # "agent: camera + motion (domain/vision)" -> "Camera + motion"
        title = re.sub(r"^agent:\s*|\s*\(.*\)\s*$", "", heading) or heading
        out.append({"id": "adv-" + re.sub(r"\W+", "-", title.lower()).strip("-"),
                    "tab": "advanced", "title": title[:1].upper() + title[1:], "note": "",
                    "fields": flds})
    return out


# -- secrets ---------------------------------------------------------------------

def _secret(value: str) -> dict:
    if not value:
        return {"set": False, "hint": ""}
    return {"set": True, "hint": "…" + value[-4:] if len(value) >= 12 else "set"}


def _keys(runtime) -> list[dict]:
    s = runtime.settings
    by_key = {info["key"]: pid for pid, info in PROVIDER_INFO.items() if info.get("key")}
    out = []
    for name in Settings.model_fields:
        if not name.endswith("_api_key"):
            continue
        pid = by_key.get(name)
        label, note = (PROVIDER_INFO[pid]["label"], "") if pid else KEY_NOTES.get(
            name, (name.removesuffix("_api_key").replace("_", " ").title(), ""))
        used_by = []
        if pid:
            for agent in AGENTS:
                if any(p.provider == pid and not p.model_key
                       for p in runtime.models.chain(agent)):
                    used_by.append(agent)
            if pid == "gemini":
                used_by.insert(0, "gemini_live")
            if pid == "groq":
                used_by.insert(0, "speech")
        out.append({"field": name, "env": name.upper(), "label": label, "note": note,
                    "provider": pid, "keys_url": PROVIDER_INFO.get(pid, {}).get("keys_url"),
                    "used_by": used_by, "shadowed": name.upper() in os.environ,
                    "checkable": bool(pid), **_secret(getattr(s, name))})
    return out


# -- agents ----------------------------------------------------------------------

def _agent_out(runtime, agent: str) -> dict:
    s = runtime.settings
    chain = runtime.models.chain(agent)
    p = chain[0]
    label, purpose, needs = AGENT_INFO.get(agent, (agent.replace("_", " ").title(), "", []))
    builtin = s._builtin_profiles().get(agent) or ModelProfile()
    status = next((d for d in runtime.models.describe() if d["agent"] == agent), {})
    return {
        "id": agent, "label": label, "purpose": purpose, "needs": needs,
        "env": agent.upper(),
        "profile": {
            "provider": p.provider, "model": p.model, "model_key": _secret(p.model_key),
            "base_url": p.base_url, "temperature": p.temperature, "max_tokens": p.max_tokens,
            "timeout_s": p.timeout_s, "extra": p.extra,
            "fallback": [{"provider": f.provider, "model": f.model, "base_url": f.base_url,
                          "own_key": bool(f.model_key)} for f in chain[1:]],
        },
        "builtin": {"provider": builtin.provider, "temperature": builtin.temperature,
                    "max_tokens": builtin.max_tokens, "timeout_s": builtin.timeout_s},
        "status": status,
    }


class EntryIn(BaseModel):
    provider: Provider
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field("", max_length=500)


class AgentIn(EntryIn):
    #: None = keep the saved one, "" = clear it (use the provider's key).
    model_key: str | None = Field(None, max_length=500)
    temperature: float | None = Field(None, ge=0, le=2)
    max_tokens: int | None = Field(None, ge=1, le=65536)
    timeout_s: float | None = Field(None, ge=1, le=900)
    extra: dict = {}
    fallback: list[EntryIn] = Field(default_factory=list, max_length=8)


def _check_base_url(url: str) -> None:
    if url and not re.match(r"^https?://[^\s/]+", url):
        raise HTTPException(400, f"base URL must start with http:// or https:// ({url[:60]})")


def _agent_changes(agent: str, body: AgentIn, saved: list[ModelProfile]) -> dict[str, str | None]:
    """The .env lines for one agent. Every field is written or removed, so the
    file says exactly what the page showed; empty = the default."""
    P = agent.upper()
    primary = saved[0]
    if body.model_key is not None:
        key = body.model_key.strip()
    else:  # keep it, unless it belonged to another provider
        key = primary.model_key if body.provider == primary.provider else ""
    _check_base_url(body.base_url)
    fallback = []
    for e in body.fallback:
        _check_base_url(e.base_url)
        # Anything the page doesn't edit (a fallback's own key, temperature...)
        # is kept from the saved entry for the same model.
        old = next((f for f in saved[1:] if f.provider == e.provider and f.model == e.model), None)
        base = old.model_dump(exclude={"fallback"}) if old else {}
        fb = ModelProfile(**{**base, "provider": e.provider, "model": e.model.strip(),
                             "base_url": e.base_url.strip()})
        fallback.append(fb.model_dump(exclude_defaults=True))
    num = lambda v: None if v is None else format(v, "g")  # 0.4 -> "0.4", 20.0 -> "20"
    return {
        P: None,  # a whole-profile JSON value would fight the lines below
        f"{P}__PROVIDER": body.provider,
        f"{P}__MODEL": body.model.strip(),
        f"{P}__MODEL_KEY": key or None,
        f"{P}__BASE_URL": body.base_url.strip() or None,
        f"{P}__TEMPERATURE": num(body.temperature),
        f"{P}__MAX_TOKENS": None if body.max_tokens is None else str(body.max_tokens),
        f"{P}__TIMEOUT_S": num(body.timeout_s),
        f"{P}__EXTRA": json.dumps(body.extra, separators=(",", ":")) if body.extra else None,
        # Always written, even empty: an unset list would bring back the defaults.
        f"{P}__FALLBACK": json.dumps(fallback, separators=(",", ":")),
    }


# -- saving ----------------------------------------------------------------------

def _validate(text: str) -> Settings:
    fd, tmp = tempfile.mkstemp(suffix=".env")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return Settings(_env_file=tmp)
    except ValidationError as exc:
        msgs = ["__".join(str(x) for x in e["loc"]).upper() + ": " + e["msg"]
                for e in exc.errors()]
        raise HTTPException(400, "; ".join(msgs)[:600]) from exc
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


async def _save(runtime, changes: dict[str, str | None]) -> dict:
    """Write `changes` to .env and make them live. Returns what changed, and
    when each change takes effect."""
    async with _lock:
        try:
            text = envfile.render(envfile.load_text(), changes)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        fresh = _validate(text)
        envfile.write(text)
        s = runtime.settings
        changed = []
        for name in Settings.model_fields:
            new = getattr(fresh, name)
            if getattr(s, name) != new:
                setattr(s, name, new)
                changed.append(name)
        runtime.models.reload()
        if runtime.diag is not None:
            from .domain.diagnostics.alerts import Thresholds

            runtime.diag.rules.t = Thresholds.from_settings(s)
        _models_cache.clear()
    log.info("setup saved %s", ", ".join(changed) or "nothing new")
    by_level: dict[str, list[str]] = {"now": [], "reconnect": [], "restart": []}
    for name in changed:
        by_level[_apply_level(name)].append(name)
    return {"changed": changed, **by_level,
            "shadowed": sorted(k for k, v in changes.items() if v is not None and k in os.environ)}


def _restart_pending(s: Settings) -> list[str]:
    return [n for n in Settings.model_fields
            if _apply_level(n) == "restart" and getattr(s, n) != _BOOT.get(n)]


def _runtime(request: Request):
    return request.app.state.agent


# -- routes: state, agents, test -------------------------------------------------

@router.get("/state")
async def state(request: Request) -> dict:
    rt = _runtime(request)
    s = rt.settings
    return {
        "env_path": str(envfile.ENV_PATH.resolve()),
        "providers": [{"id": pid, "label": PROVIDER_INFO.get(pid, {}).get("label", pid),
                       "key": PROVIDER_INFO.get(pid, {}).get("key"),
                       "key_set": bool(s.provider_key(pid)),
                       "keys_url": PROVIDER_INFO.get(pid, {}).get("keys_url"),
                       "local": PROVIDER_INFO.get(pid, {}).get("local", False),
                       "base_url": PROVIDER_INFO.get(pid, {}).get("base_url", False),
                       "default_base_url": PRESETS.get(pid, "")}
                      for pid in get_args(Provider)],
        "agents": [_agent_out(rt, a) for a in AGENTS],
        "globals": {"temperature": s.llm_temperature, "max_tokens": s.llm_max_tokens,
                    "timeout_s": float(s.llm_timeout_s)},
        "keys": _keys(rt),
        "sections": _sections(s),
        "tools": _all_tools(rt),
        "restart_pending": _restart_pending(s),
    }


@router.put("/agents/{agent}")
async def save_agent(agent: str, body: AgentIn, request: Request) -> dict:
    rt = _runtime(request)
    if agent not in AGENTS:
        raise HTTPException(404, f"no agent {agent!r}")
    result = await _save(rt, _agent_changes(agent, body, rt.models.chain(agent)))
    return {"agent": _agent_out(rt, agent), "result": result,
            "keys": _keys(rt)}


class TestIn(BaseModel):
    agent: str
    #: 0 = the primary, 1.. = its fallbacks: which saved key to use.
    index: int = Field(0, ge=0, le=16)
    provider: Provider
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field("", max_length=500)
    model_key: str | None = Field(None, max_length=500)
    extra: dict = {}


PING_TOOL = {"type": "function", "function": {
    "name": "ping", "description": "Checks that tools work. Call it when asked to ping.",
    "parameters": {"type": "object", "properties": {}}}}


def _red_png(size: int = 32) -> bytes:
    """A plain red square: the vision check asks what colour it is."""
    raw = b"".join(b"\x00" + bytes((220, 30, 30)) * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@router.post("/test")
async def test_model(body: TestIn, request: Request) -> dict:
    """One short call to one model, shaped like the agent's real work: a tool
    call for agents that use tools, a picture for the ones that look."""
    rt = _runtime(request)
    if body.agent not in AGENTS:
        raise HTTPException(404, f"no agent {body.agent!r}")
    _check_base_url(body.base_url)
    chain = rt.models.chain(body.agent)
    saved = chain[body.index] if body.index < len(chain) else None
    if body.model_key is not None:
        key = body.model_key
    else:
        key = saved.model_key if saved and saved.provider == body.provider else ""
    base = saved.model_dump(exclude={"fallback"}) if saved and saved.provider == body.provider else {}
    profile = ModelProfile(**{**base, "provider": body.provider, "model": body.model.strip(),
                              "base_url": body.base_url.strip(), "model_key": key,
                              **({"extra": body.extra} if body.extra else {})})
    model = build_model(profile, rt.settings, rt.models.http)
    why = model.unavailable()
    if why:
        return {"ok": False, "error": why, "model": model.describe()}

    needs = AGENT_INFO.get(body.agent, ("", "", []))[2]
    out: dict = {"model": model.describe()}
    if profile.provider == "ollama":
        # Pay the cold start separately, so the answer's timing is the real one.
        t0 = time.perf_counter()
        try:
            await ollama_api.ollama_load(rt.models.http, model.host, profile.model,
                                         rt.settings.ollama_num_thread)
        except (ModelError, httpx.HTTPError) as exc:
            return {**out, "ok": False, "error": _short(exc)}
        out["load_ms"] = round((time.perf_counter() - t0) * 1000)

    if "vision" in needs:
        kind = "vision"
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "What colour is this picture? Answer with one word."},
            {"type": "image", "data": _red_png(), "mime": "image/png"}]}]
        tools = None
    elif "tools" in needs:
        kind = "tools"
        messages = [{"role": "system", "content": "You are being tested. Use the tool."},
                    {"role": "user", "content": "Please ping."}]
        tools = [PING_TOOL]
    else:
        kind = "text"
        messages = [{"role": "user", "content": "Say hello in five words or fewer."}]
        tools = None
    out["kind"] = kind
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            model.complete(messages, tools, max_tokens=300, temperature=0.2),
            timeout=model.timeout_s + 5)
    except asyncio.TimeoutError:
        return {**out, "ok": False, "error": f"no answer in {model.timeout_s + 5:.0f} s"}
    except (ModelError, httpx.HTTPError, ValueError, RuntimeError) as exc:
        return {**out, "ok": False, "error": _short(exc)}
    usage = result.usage.as_dict() if result.usage else {}
    out.update(ok=True, text=result.text[:300], ms=round((time.perf_counter() - t0) * 1000),
               first_token_ms=usage.get("first_token_ms"), tok_per_s=usage.get("tok_per_s"))
    if kind == "tools":
        out["tool_called"] = any(isinstance(c, ToolCall) and c.name == "ping"
                                 for c in result.tool_calls)
        if not out["tool_called"]:
            out["warning"] = "It answered but didn't call the tool, so tool use may be unreliable."
    elif kind == "vision" and "red" not in result.text.lower():
        out["warning"] = "It answered but didn't seem to see the picture (it should say red)."
    elif not result.text:
        out["warning"] = "It returned no text. A reasoning model may need a bigger max tokens."
    return out


def _short(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    # SDK errors embed the provider's JSON; its "message" is the readable part.
    # 'gemini 503: 503 Service Unavailable. {..."message": "This model is..."'
    # No closing quote needed: ModelError has already cut the text at 300.
    m = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)+)', text)
    if m:
        head = text.split(":", 1)[0] if isinstance(exc, ModelError) else ""
        text = f"{head}: {m.group(1)}" if head else m.group(1)
    return text[:300]


# -- routes: what each provider offers --------------------------------------------

_models_cache: dict[tuple, tuple[float, list[dict]]] = {}
_CACHE_S = 600
_NOT_CHAT = re.compile(r"whisper|tts|embed|moderation|dall-e|imagen|image|audio|realtime|"
                       r"transcribe|orpheus|guard|playai|sora|veo|aqa|computer-use", re.I)


async def _list_models(http: httpx.AsyncClient, provider: str, key: str,
                       base_url: str, host: str) -> list[dict]:
    if provider == "ollama":
        out = []
        for m in await ollama_api.ollama_tags(http, base_url or host):
            caps = m["capabilities"]  # None on older Ollama: unknown, not "can't"
            out.append({"id": m["name"],
                        "note": " · ".join(x for x in (m["params"], m["quant"], _gb(m["size_mb"])) if x),
                        "tools": None if caps is None else "tools" in caps,
                        "vision": None if caps is None else "vision" in caps})
        return out
    if provider == "gemini":
        if not key:
            raise ModelError("gemini", 401, "no GEMINI_API_KEY")
        r = await http.get("https://generativelanguage.googleapis.com/v1beta/models",
                           params={"pageSize": 1000}, headers={"x-goog-api-key": key}, timeout=10)
        _raise(provider, r)
        return [{"id": m["name"].removeprefix("models/"), "note": m.get("displayName", "")}
                for m in r.json().get("models", [])
                if "generateContent" in (m.get("supportedGenerationMethods") or [])
                and not _NOT_CHAT.search(m["name"])]
    if provider == "anthropic":
        if not key:
            raise ModelError("anthropic", 401, "no ANTHROPIC_API_KEY")
        r = await http.get("https://api.anthropic.com/v1/models", params={"limit": 1000},
                           headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                           timeout=10)
        _raise(provider, r)
        return [{"id": m["id"], "note": m.get("display_name", ""), "vision": True, "tools": True}
                for m in r.json().get("data", [])]
    base = (base_url or PRESETS.get(provider, "")).rstrip("/")
    if not base:
        raise ModelError(provider, 0, "set a base URL first")
    r = await http.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"} if key else {},
                       timeout=10)
    _raise(provider, r)
    out = []
    for m in r.json().get("data", []):
        mid = m.get("id", "")
        if not mid or _NOT_CHAT.search(mid) or m.get("active") is False:
            continue
        entry: dict = {"id": mid, "note": m.get("name") or ""}
        if m.get("context_window"):
            entry["note"] = f"{m['context_window'] // 1000}k context"
        arch = m.get("architecture") or {}
        if "input_modalities" in arch:
            entry["vision"] = "image" in arch["input_modalities"]
        if "supported_parameters" in m:
            entry["tools"] = "tools" in m["supported_parameters"]
        out.append(entry)
    return sorted(out, key=lambda e: e["id"])


def _raise(provider: str, r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    msg = r.text
    try:
        err = r.json().get("error")
        msg = err.get("message", msg) if isinstance(err, dict) else err or msg
    except (ValueError, AttributeError):
        pass
    raise ModelError(provider, r.status_code, str(msg)[:200])


def _gb(mb: int | None) -> str:
    if not mb:
        return ""
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb} MB"


def _key_for(rt, provider: str, agent: str) -> str:
    if agent in AGENTS:
        primary = rt.models.chain(agent)[0]
        if primary.provider == provider and primary.model_key:
            return primary.model_key
    return rt.settings.provider_key(provider)


@router.get("/models")
async def provider_models(request: Request, provider: str, agent: str = "",
                          base_url: str = "", refresh: bool = False) -> dict:
    rt = _runtime(request)
    if provider not in get_args(Provider):
        raise HTTPException(404, f"no provider {provider!r}")
    _check_base_url(base_url)
    key = _key_for(rt, provider, agent)
    cache_key = (provider, base_url, hash(key))
    hit = _models_cache.get(cache_key)
    if hit and not refresh and time.time() - hit[0] < _CACHE_S and provider != "ollama":
        return {"models": hit[1], "cached": True}
    try:
        models = await _list_models(rt.models.http, provider, key, base_url, rt.settings.ollama_host)
    except (ModelError, httpx.HTTPError, ValueError, KeyError) as exc:
        return {"models": [], "error": _short(exc)}
    _models_cache[cache_key] = (time.time(), models)
    return {"models": models}


# -- routes: keys and other fields -----------------------------------------------

class ValuesIn(BaseModel):
    values: dict[str, Any]


@router.put("/keys")
async def save_keys(body: ValuesIn, request: Request) -> dict:
    rt = _runtime(request)
    changes = {}
    for name, value in body.values.items():
        if not name.endswith("_api_key") or name not in Settings.model_fields:
            raise HTTPException(400, f"not an API key setting: {name}")
        # Cleared keys stay as an empty line, next to their comment in .env.
        changes[name.upper()] = (value or "").strip()
    result = await _save(rt, changes)
    return {"keys": _keys(rt), "result": result,
            "providers_key_set": {pid: bool(rt.settings.provider_key(pid))
                                  for pid in get_args(Provider)}}


class KeyCheckIn(BaseModel):
    field: str


@router.post("/keys/check")
async def check_key(body: KeyCheckIn, request: Request) -> dict:
    rt = _runtime(request)
    pid = next((p for p, i in PROVIDER_INFO.items() if i.get("key") == body.field), None)
    if pid is None:
        raise HTTPException(400, "this key can't be checked from here")
    key = getattr(rt.settings, body.field, "")
    if not key:
        return {"ok": False, "error": "not set"}
    try:
        if pid == "openrouter":  # its model list is public; this one needs the key
            r = await rt.models.http.get("https://openrouter.ai/api/v1/key",
                                         headers={"Authorization": f"Bearer {key}"}, timeout=10)
            _raise(pid, r)
            return {"ok": True, "detail": "key accepted"}
        models = await _list_models(rt.models.http, pid, key, "", rt.settings.ollama_host)
    except (ModelError, httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": _short(exc)}
    return {"ok": True, "detail": f"key accepted, {len(models)} models available"}


@router.put("/fields")
async def save_fields(body: ValuesIn, request: Request) -> dict:
    rt = _runtime(request)
    changes = {}
    for name, value in body.values.items():
        if (name not in Settings.model_fields or name in HIDDEN or name in AGENTS
                or name.endswith("_api_key")):
            raise HTTPException(400, f"not a setting this page can change: {name}")
        changes[name.upper()] = _env_value(name, value)
    result = await _save(rt, changes)
    return {"sections": _sections(rt.settings), "result": result,
            "restart_pending": _restart_pending(rt.settings)}


def _all_tools(rt) -> list[dict]:
    """Every tool Jarvis has, offered or not, for the Tools picker."""
    from .core.tools.base import Tool, ToolContext
    from .core.tools.catalog import DOMAIN_TOOL_MODULES

    ctx = ToolContext(settings=rt.settings, runtime=rt, brain="setup")
    out = []
    for module in DOMAIN_TOOL_MODULES:
        for value in vars(module).values():
            if not isinstance(value, Tool):
                continue
            try:
                why = value.unavailable(ctx) if value.unavailable else None
            except Exception as exc:  # a tool's own check must not break the page
                why = str(exc)[:120]
            out.append({"name": value.name, "description": " ".join(value.description.split()),
                        "action": value.action, "unavailable": why})
    return sorted(out, key=lambda t: t["name"])


# -- routes: Ollama ---------------------------------------------------------------

@dataclass
class PullJob:
    name: str
    state: str = "running"  # running | done | failed | cancelled
    status: str = "starting"  # Ollama's own words: "pulling manifest", "verifying..."
    layers: dict = field(default_factory=dict)  # digest -> [completed, total]
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    rate: float = 0.0  # bytes/s, smoothed
    task: asyncio.Task | None = None
    _mark: tuple[float, int] = (0.0, 0)

    @property
    def completed(self) -> int:
        return sum(c for c, _ in self.layers.values())

    @property
    def total(self) -> int:
        return sum(t for _, t in self.layers.values())

    def out(self) -> dict:
        return {"name": self.name, "state": self.state, "status": self.status,
                "completed": self.completed, "total": self.total, "error": self.error,
                "rate": round(self.rate), "started": self.started, "finished": self.finished}


_pulls: dict[str, PullJob] = {}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/:@]{0,199}$")


def _norm(name: str) -> str:
    """"qwen2.5" and "qwen2.5:latest" are the same model to Ollama."""
    tail = name.rsplit("/", 1)[-1]
    return name if ":" in tail else f"{name}:latest"


async def _run_pull(rt, job: PullJob) -> None:
    try:
        async for msg in ollama_api.ollama_pull(rt.models.http, rt.settings.ollama_host, job.name):
            job.status = msg.get("status") or job.status
            if msg.get("digest") and msg.get("total"):
                job.layers[msg["digest"]] = [msg.get("completed") or 0, msg["total"]]
            now, done = time.monotonic(), job.completed
            t0, c0 = job._mark
            if now - t0 >= 1.0:
                if t0:
                    inst = max(0.0, (done - c0) / (now - t0))
                    job.rate = inst if not job.rate else 0.7 * job.rate + 0.3 * inst
                job._mark = (now, done)
        job.state, job.status = "done", "success"
        log.info("ollama: pulled %s", job.name)
    except asyncio.CancelledError:
        job.state, job.status = "cancelled", "cancelled"
        raise
    except (ModelError, httpx.HTTPError) as exc:
        job.state, job.error = "failed", _short(exc)
        log.warning("ollama: pull %s failed: %s", job.name, job.error)
    finally:
        job.finished = time.time()
        _models_cache.clear()


async def _ollama_state(rt) -> dict:
    host = rt.settings.ollama_host
    http = rt.models.http
    version = await ollama_api.ollama_version(http, host)
    models, loaded, error = [], [], None
    if version:
        try:
            models = await ollama_api.ollama_tags(http, host)
        except (httpx.HTTPError, ValueError) as exc:
            error = _short(exc)
        loaded = (await ollama_api.ollama_ps(http, host)).get("models", [])
    # Which agents point at which Ollama model, installed or not.
    used: dict[str, list[str]] = {}
    for agent in AGENTS:
        for i, p in enumerate(rt.models.chain(agent)):
            if p.provider == "ollama" and p.model:
                used.setdefault(_norm(p.model), []).append(agent if i == 0 else f"{agent}:fallback")
    installed = {_norm(m["name"]) for m in models}
    loaded_names = {_norm(m["name"]) for m in loaded}
    for m in models:
        m["used_by"] = used.get(_norm(m["name"]), [])
        m["loaded"] = _norm(m["name"]) in loaded_names
    now = time.time()
    for name in [n for n, j in _pulls.items() if j.state != "running" and now - j.finished > 600]:
        del _pulls[name]
    try:
        import psutil

        ram_mb = round(psutil.virtual_memory().total / 2**20)
    except ImportError:
        ram_mb = None
    return {
        "host": host, "version": version, "reachable": bool(version), "error": error,
        "models": models, "loaded": loaded, "ram_mb": ram_mb,
        "pulls": [j.out() for j in _pulls.values()],
        "missing": [{"name": n, "used_by": a} for n, a in used.items() if n not in installed]
        if version else [],
        "suggested": [{**s, "installed": _norm(s["name"]) in installed} for s in SUGGESTED_OLLAMA],
    }


class NameIn(BaseModel):
    name: str


def _valid_name(name: str) -> str:
    name = name.strip()
    if not _NAME.match(name):
        raise HTTPException(400, "that doesn't look like an Ollama model name")
    return name


@router.get("/ollama")
async def ollama_state(request: Request) -> dict:
    return await _ollama_state(_runtime(request))


@router.post("/ollama/pull")
async def ollama_pull(body: NameIn, request: Request) -> dict:
    rt = _runtime(request)
    name = _valid_name(body.name)
    job = _pulls.get(name)
    if job is None or job.state != "running":
        if not await ollama_api.ollama_version(rt.models.http, rt.settings.ollama_host):
            raise HTTPException(503, f"Ollama isn't running at {rt.settings.ollama_host}")
        job = _pulls[name] = PullJob(name)
        job.task = rt.spawn(_run_pull(rt, job))
    return await _ollama_state(rt)


@router.post("/ollama/cancel")
async def ollama_cancel(body: NameIn, request: Request) -> dict:
    job = _pulls.get(body.name)
    if job and job.task and not job.task.done():
        job.task.cancel()
        await asyncio.wait({job.task}, timeout=3)  # never raises, unlike awaiting it
    return await _ollama_state(_runtime(request))


@router.post("/ollama/delete")
async def ollama_delete(body: NameIn, request: Request) -> dict:
    rt = _runtime(request)
    name = _valid_name(body.name)
    try:
        await ollama_api.ollama_delete(rt.models.http, rt.settings.ollama_host, name)
    except (ModelError, httpx.HTTPError) as exc:
        raise HTTPException(502, _short(exc)) from exc
    _models_cache.clear()
    return await _ollama_state(rt)


@router.post("/ollama/load")
async def ollama_load(body: NameIn, request: Request) -> dict:
    rt = _runtime(request)
    try:
        await ollama_api.ollama_load(rt.models.http, rt.settings.ollama_host, _valid_name(body.name),
                                     rt.settings.ollama_num_thread)
    except (ModelError, httpx.HTTPError) as exc:
        raise HTTPException(502, _short(exc)) from exc
    return await _ollama_state(rt)


@router.post("/ollama/unload")
async def ollama_unload(body: NameIn, request: Request) -> dict:
    rt = _runtime(request)
    try:
        await ollama_api.ollama_unload(rt.models.http, rt.settings.ollama_host, _valid_name(body.name))
    except httpx.HTTPError as exc:
        raise HTTPException(502, _short(exc)) from exc
    return await _ollama_state(rt)
