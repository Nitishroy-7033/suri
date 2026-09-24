"""All tunable knobs, in one place.

Values come from .env (see .env.example). Anything under "Tunables" can also
be patched live from the browser debug panel via a {"t":"config"} message --
retuning voice thresholds by restarting the server gets old within about ten
minutes of real testing.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BrainMode = Literal["auto", "gemini_live", "pipeline", "offline"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- server ---
    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "INFO"
    debug_dump_audio: bool = False

    # --- keys (both free, both optional; offline mode needs neither) ---
    gemini_api_key: str = ""
    groq_api_key: str = ""

    # --- mode selection ---
    jarvis_mode: BrainMode = "auto"

    # --- audio contract (changing these means changing protocol.js too) ---
    mic_sample_rate: int = 16000
    tts_sample_rate: int = 24000
    wire_frame_samples: int = 1280  # 80 ms @ 16 kHz = openWakeWord's frame
    vad_frame_samples: int = 512  # 32 ms @ 16 kHz = Silero's frame
    tts_chunk_samples: int = 5760  # 240 ms @ 24 kHz
    ring_seconds: float = 3.0
    tts_prebuffer_ms: int = 150  # raise to ~600 when the LLM is local

    # --- wake word ---
    wake_model: str = "hey_jarvis"
    # Despite the model's name it is really a "jarvis" detector: measured on
    # this machine, "jarvis" / "ok jarvis" / "hi jarvis" / "yo jarvis" all
    # score 0.99+, while near-misses ("travis", "harvest", "service", "java")
    # score under 0.03. With that much margin a low threshold costs nothing
    # and catches quiet or distant speech that 0.5 was dropping.
    # Two tiers. Anything at or above `wake_threshold` opens a turn, but only
    # scores at or above `wake_confident` are trusted outright -- below that
    # the transcript has to actually start with "jarvis" or the turn is
    # dropped silently. That buys accent robustness (see has_wake_word) without
    # the false-positive storm a low flat threshold would cause. Measured
    # distractors measured 0.000 across US, Indian-English and Hindi clips,
    # so 0.05 still leaves a wide margin.
    wake_threshold: float = 0.05
    wake_confident: float = 0.5
    wake_verify: bool = True
    wake_cooldown_ms: int = 2000
    # Off by default. This only ever vetoes a trigger that fired with no
    # voice activity nearby; it never stops the model hearing you. Turn it
    # on only if you get false wakes from non-speech noise.
    gate_wake_on_vad: bool = False
    wake_vad_lookback_ms: int = 400

    # --- VAD / endpointing ---
    vad_threshold: float = 0.5
    min_speech_ms: int = 200
    end_silence_ms: int = 700
    end_silence_short_ms: int = 500
    short_utterance_ms: int = 700
    no_speech_timeout_ms: int = 3000
    max_utterance_ms: int = 15000
    thinking_timeout_ms: int = 12000  # give up if no reply audio starts
    # The wake word only resolves at the *end* of "jarvis", measured here at
    # 634-1032 ms after the speaker started (slower on accents the model finds
    # harder). At 500 ms the phrase itself was being clipped, which both lost
    # the start of the question and made the transcript unreliable -- Gemini
    # heard "Ciao" instead of "Hey Jarvis". 1200 ms covers the worst case.
    preroll_ms: int = 1200

    # --- barge-in ---
    barge_enabled: bool = True
    barge_vad_threshold: float = 0.6  # higher than listening: echo lifts the floor
    barge_consecutive_ms: int = 240
    barge_start_guard_ms: int = 200
    barge_rms_dbfs: float = -42.0
    barge_snr_db: float = 8.0
    barge_preroll_ms: int = 400
    echo_guard: bool = True
    echo_corr_max: float = 0.35
    echo_ref_ms: int = 500
    thinking_barge_grace_ms: int = 250
    half_duplex: bool = False
    playback_report_timeout_ms: int = 300
    client_buffer_estimate_ms: int = 400

    # --- follow-up window ---
    followup_ms: int = 6000
    followup_ms_question: int = 10000
    followup_trigger_ms: int = 250
    followup_preroll_ms: int = 600
    post_tts_guard_ms: int = 250
    client_drain_grace_ms: int = 400

    # --- STT ---
    groq_stt_model: str = "whisper-large-v3-turbo"
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    whisper_cpu_threads: int = 6
    whisper_beam_size: int = 1
    min_transcript_chars: int = 2

    # --- LLM ---
    # Groq dropped the Llama models from its catalogue. Measured on the free
    # tier: qwen3.8-27b returns a complete two-sentence answer in ~310 ms.
    # The gpt-oss models are reasoning models -- they stream their thinking
    # into a separate channel and leave `content` empty, so they are useless
    # for a voice turn without extra handling.
    groq_llm_model: str = "qwen/qwen3.8-27b"
    groq_llm_fallback_model: str = "openai/gpt-oss-20b"
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b-instruct-q4_K_M"
    ollama_num_thread: int = 6
    llm_max_tokens: int = 160
    llm_temperature: float = 0.7
    llm_timeout_s: int = 20
    max_history_turns: int = 12
    history_ttl_s: int = 300

    # --- TTS ---
    tts_voice_en: str = "en-US-AndrewMultilingualNeural"
    tts_voice_hi: str = "hi-IN-MadhurNeural"
    # Orpheus on Groq: faster than edge-tts, but needs a one-off terms
    # acceptance at console.groq.com before the endpoint will serve it.
    groq_tts_model: str = "canopylabs/orpheus-v1-english"
    groq_tts_voice: str = "tara"
    tts_engine: str = "auto"  # auto | groq | edge
    tts_concurrency: int = 3  # clauses synthesised in parallel

    kokoro_model_path: str = "models/kokoro-v1.0.int8.onnx"
    kokoro_voices_path: str = "models/voices-v1.0.bin"
    kokoro_voice: str = "am_michael"
    kokoro_speed: float = 1.05
    kokoro_intra_op_threads: int = 4
    first_clause_max_chars: int = 60
    clause_min_chars: int = 80
    clause_max_chars: int = 200

    # --- shared persona ---
    # Written for speech, not text. Without the explicit bans a model will
    # happily emit bullet points and asterisks, and a voice reading
    # "asterisk asterisk important" is the fastest way to break the illusion.
    system_prompt: str = (
        "You are Jarvis, a friendly and concise voice assistant. "
        "You are speaking out loud, so reply in at most two short spoken "
        "sentences. Never use markdown, bullet points, numbered lists, code "
        "blocks or emoji. Use natural contractions. If the user speaks Hindi "
        "or mixes Hindi and English, reply the same way. If you do not know "
        "something, say so briefly."
    )

    # --- Gemini Live ---
    gemini_live_model: str = "gemini-3.8-live"
    gemini_voice: str = "Puck"
    gemini_session_idle_s: int = 60
    gemini_local_wake: bool = True

    # --- agent: tools ---
    # "all", a comma-separated list of tool names, or empty for none.
    tools_enabled: str = "all"
    # Tools that change the machine (open_app, open_url). Read-only tools
    # (search, time, memory, camera) are unaffected by this switch.
    tools_allow_actions: bool = True
    tool_timeout_s: float = 15.0
    tool_result_max_chars: int = 2000
    # Rounds of call -> result -> call the pipeline allows in one turn.
    # Every round is another LLM request before Jarvis can finish speaking.
    max_tool_rounds: int = 3
    webpage_max_chars: int = 3000
    web_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    )
    # Short name -> what os.startfile runs. Set as JSON in .env to change.
    app_allowlist: dict[str, str] = {
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "paint": "mspaint.exe",
        "file explorer": "explorer.exe",
        "settings": "ms-settings:",
    }
    memory_prompt_facts: int = 20  # remembered facts folded into the prompt

    # --- agent: camera + motion (domain/vision) ---
    # Off by default: the camera light should only come on when you chose it.
    camera_enabled: bool = False
    camera_index: int = 0
    camera_width: int = 1280
    camera_height: int = 720
    vision_model: str = "gemini-flash-latest"
    motion_watch_on_start: bool = False
    motion_fps: float = 5.0
    motion_cooldown_s: float = 30.0
    motion_pixel_delta: float = 25.0
    motion_min_area: float = 0.02

    # --- agent: web agent (domain/webagent) ---
    # A separate agent that drives a real Chrome window: Jarvis delegates
    # website jobs to it with web_task. Off by default -- it clicks and types
    # on real sites, so it should only run when you chose it.
    web_agent_enabled: bool = False
    web_agent_model: str = "gemini-flash-latest"  # its own model, not the Live one
    # Tried when the main one is overloaded ("503 high demand"). Empty = none.
    web_agent_fallback_model: str = "gemini-flash-lite-latest"
    # When both Gemini models are overloaded, a step goes to Groq (needs
    # GROQ_API_KEY). Empty = the pipeline's GROQ_LLM_MODEL.
    web_agent_groq_model: str = ""
    web_agent_max_steps: int = 25
    # web_task waits this long before answering "working on it": quick jobs
    # ("what's the title of this page") come back in the same turn.
    web_agent_sync_wait_s: float = 6.0
    web_agent_answer_timeout_s: float = 180.0  # how long a question waits for you
    # How long it waits while you log in / solve a CAPTCHA in the window.
    web_agent_handover_timeout_s: float = 600.0
    # Installed Chrome (149+ has WebMCP). "chromium" needs `playwright install`.
    browser_channel: str = "chrome"
    browser_profile_dir: str = "data/browser-profile"
    browser_idle_close_s: float = 600.0
    browser_nav_timeout_s: float = 25.0
    # Links that would open a new tab open in the same one, so follow-ups
    # ("add it to the cart") continue exactly where the agent left off.
    browser_single_tab: bool = True
    # Comma-separated domains the agent must never open, e.g. "bank.com,paypal.com".
    browser_blocked_domains: str = ""
    # Buy / pay / delete / send / post... always ask you first. Leave this on.
    browser_confirm_risky: bool = True

    # --- resilience ---
    fallback_after_failures: int = 2
    fallback_cooldown_s: int = 120

    @property
    def ring_samples(self) -> int:
        return int(self.ring_seconds * self.mic_sample_rate)


settings = Settings()


@dataclass
class Tunables:
    """Per-session mutable copy of the knobs the debug panel can move live."""

    wake_threshold: float
    wake_confident: float
    wake_verify: bool
    vad_threshold: float
    min_speech_ms: int
    end_silence_ms: int
    end_silence_short_ms: int
    short_utterance_ms: int
    no_speech_timeout_ms: int
    max_utterance_ms: int
    preroll_ms: int
    barge_enabled: bool
    barge_vad_threshold: float
    barge_consecutive_ms: int
    barge_start_guard_ms: int
    barge_rms_dbfs: float
    barge_snr_db: float
    barge_preroll_ms: int
    echo_guard: bool
    echo_corr_max: float
    followup_ms: int
    followup_trigger_ms: int
    half_duplex: bool

    @classmethod
    def from_settings(cls, s: Settings) -> "Tunables":
        return cls(**{f.name: getattr(s, f.name) for f in fields(cls)})

    def patch(self, updates: dict) -> list[str]:
        """Apply a partial update, ignoring unknown keys. Returns what changed."""
        allowed = {f.name: f.type for f in fields(self)}
        changed = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            current = getattr(self, key)
            try:
                coerced = type(current)(value)
            except (TypeError, ValueError):
                continue
            if coerced != current:
                setattr(self, key, coerced)
                changed.append(key)
        return changed

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}
