"""One WebSocket connection = one Session.

The session owns the ears -- wake word, VAD, endpointing, the conversation
state machine -- and hands audio to a Brain that does the thinking.

Two kinds of Brain exist and they need different treatment. Gemini Live does
its own turn detection and interruption, so once you have said "Jarvis" we
simply forward audio and let the FSM follow the model's events. A pipeline
brain (STT then LLM then TTS) needs us to tell it when you stopped talking,
so the local endpointer drives it instead. `Brain.handles_turn_detection`
decides which path runs; running both at once produces turns cut in half.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import time
from pathlib import Path

import numpy as np
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from ...core.runtime import AgentRuntime
from ..brain.base import Brain
from ..brain.factory import build_brain
from ...config import Settings, Tunables
from .audio import Chunker, RingBuffer, rms_dbfs, wav_bytes
from .fsm import Fsm, State
from .protocol import ProtocolError, pack_tts, unpack_mic
from .vad import (BargeConfig, BargeDetector, EndpointConfig, Endpointer,
                  NoiseFloor, VadGate)
from .wake import WakeWordDetector

log = logging.getLogger("jarvis.session")

LEVEL_REPORT_EVERY = 2  # wire frames of 80 ms -> ~6 Hz to the meter
VAD_FRAME_MS = 32.0  # 512 samples @ 16 kHz
DEBUG_DIR = Path(__file__).resolve().parents[3] / "debug"


class Session:
    def __init__(self, ws: WebSocket, settings: Settings,
                 agent: AgentRuntime | None = None) -> None:
        self.ws = ws
        self.settings = settings
        self.agent = agent
        self._events_task: asyncio.Task | None = None
        self._diag_task: asyncio.Task | None = None  # Systems view feed
        self.tunables = Tunables.from_settings(settings)

        self.fsm = Fsm(self.send)
        self.ring = RingBuffer(settings.ring_samples)
        self.vad_chunker = Chunker(settings.vad_frame_samples)

        t0 = time.perf_counter()
        self.wake = WakeWordDetector(
            model_name=settings.wake_model,
            threshold=self.tunables.wake_threshold,
            cooldown_ms=settings.wake_cooldown_ms,
            gate_on_vad=settings.gate_wake_on_vad,
            vad_lookback_ms=settings.wake_vad_lookback_ms,
        )
        self.vad = VadGate(threshold=self.tunables.vad_threshold)
        log.info("models loaded in %.0f ms", (time.perf_counter() - t0) * 1000)

        self.endpointer = Endpointer(self._endpoint_config())
        self.barge = BargeDetector(self._barge_config())
        self.noise = NoiseFloor()

        # utterance capture
        self.utterance: list[np.ndarray] = []
        self.capturing = False
        self.utterance_count = 0
        self._heard_speech = False
        # Conversation mode: any speech starts a turn, no wake word needed.
        self.converse = False
        # (timestamp, wake score) over a short window, for the UI peak meter
        self._peak_window: list[tuple[float, float]] = []
        self._awaiting_played = False
        # Sending audio is not the same as the user hearing it. The brain can
        # produce a 12-second reply in 2 seconds, so the session stays in
        # SPEAKING until the browser says the queue has actually drained.
        self._draining = False
        self._drain_turn: int | None = None
        self._drain_task: asyncio.Task | None = None
        self._brain_done = False

        # follow-up window
        self._followup_voice_ms = 0.0
        self._followup_deadline = 0.0

        # brain
        self.brain: Brain | None = None
        self.brain_error: str | None = None

        # counters / plumbing
        self.frames_in = 0
        self.dropped_frames = 0
        self._expected_seq: int | None = None
        self._last_level = float("-inf")
        self._last_frame_at = 0.0  # monotonic; the mic counts as live if recent
        self.active_turn_id = 0
        self._reply_task: asyncio.Task | None = None
        self._closing = False

    def _barge_config(self) -> BargeConfig:
        t = self.tunables
        return BargeConfig(
            vad_threshold=t.barge_vad_threshold,
            consecutive_ms=t.barge_consecutive_ms,
            start_guard_ms=t.barge_start_guard_ms,
            rms_dbfs=t.barge_rms_dbfs,
            snr_db=t.barge_snr_db,
        )

    def _endpoint_config(self) -> EndpointConfig:
        t = self.tunables
        return EndpointConfig(
            vad_threshold=t.vad_threshold,
            min_speech_ms=t.min_speech_ms,
            end_silence_ms=t.end_silence_ms,
            end_silence_short_ms=t.end_silence_short_ms,
            short_utterance_ms=t.short_utterance_ms,
            no_speech_timeout_ms=t.no_speech_timeout_ms,
            max_utterance_ms=t.max_utterance_ms,
        )

    # -- brain ------------------------------------------------------------

    async def _build_brain(self) -> None:
        self.brain, self.brain_error = await build_brain(
            self.settings, self._brain_send, self.send_audio, self.agent)
        if self.brain is None:
            await self.send({"t": "error", "code": "no_brain", "fatal": False,
                             "message": self.brain_error})

    # -- proactive events -------------------------------------------------

    async def _watch_events(self) -> None:
        """Timers, motion alerts: always shown, spoken only when idle.

        Only IDLE, not FOLLOW_UP_WINDOW: in the window the user may be about
        to speak, and an alert barging in there is exactly the interruption
        the whole state machine exists to avoid. Anything that could not be
        spoken right away is on screen, and is said once things go quiet.
        """
        q = self.agent.events.subscribe()
        later: list = []  # things to say once the conversation goes quiet
        waiter: asyncio.Task | None = None

        async def announce(event) -> bool:
            # State first, so the brain's tts_begin finds us out of IDLE.
            await self.fsm.to(State.THINKING, f"event_{event.kind}")
            spoken = (await self.brain.speak(event.text) if event.text
                      else await self.brain.announce(event.say))
            if spoken:
                log.info("announcing %s", event.kind)
                return True
            await self.fsm.to(State.IDLE, "announce_declined")
            return False

        async def when_idle() -> None:
            # Not dropped, just held: a timer or a finished web task is still
            # worth saying once you stop talking, for a minute and a half.
            deadline = time.monotonic() + 90
            while later and time.monotonic() < deadline:
                await asyncio.sleep(0.3)
                if self.brain is not None and self.fsm.is_(State.IDLE):
                    event = later.pop(0)
                    if not await announce(event):
                        later.insert(0, event)
            later.clear()

        try:
            while True:
                event = await q.get()
                await self.send(event.to_wire())
                if not ((event.say or event.text) and self.brain is not None):
                    continue
                # With several tabs open, the web agent's results and
                # questions -- and system alerts -- belong to the one you
                # were talking to, not every tab at once.
                active = self.agent.active_session
                if (event.kind.startswith(("web_", "diag_")) and active is not None
                        and active is not self):
                    continue
                if self.fsm.is_(State.IDLE) and not later:
                    await announce(event)
                    continue
                later.append(event)
                if waiter is None or waiter.done():
                    waiter = asyncio.create_task(when_idle())
        finally:
            if waiter is not None:
                waiter.cancel()
            self.agent.events.unsubscribe(q)

    # -- diagnostics feed -------------------------------------------------

    def _voice_health(self) -> dict:
        """This connection's ears and mouth, for the Systems view."""
        brain = self.brain
        age = time.monotonic() - self._last_frame_at if self._last_frame_at else None
        return {
            "brain": brain.name if brain else None,
            "brain_error": self.brain_error,
            "state": self.fsm.state.name.lower(),
            "stt": getattr(getattr(brain, "stt", None), "name", None)
                   or ("gemini_live" if brain and brain.handles_turn_detection else None),
            "tts": getattr(getattr(brain, "tts", None), "name", None)
                   or ("gemini_live" if brain and brain.handles_turn_detection else None),
            "llm": (f"{brain.llm.name}:{brain.llm.model}" if getattr(brain, "llm", None)
                    else (self.settings.gemini_live_model if brain and brain.handles_turn_detection else None)),
            "mic": "live" if age is not None and age < 3 else "silent",
            "frames_in": self.frames_in,
            "dropped_frames": self.dropped_frames,
            "noise_dbfs": round(self.noise.value, 1) if math.isfinite(self.noise.value) else None,
        }

    async def _watch_diag(self) -> None:
        diag = self.agent.diag
        q = diag.subscribe()
        try:
            while True:
                snap = await q.get()
                await self.send({"t": "diag", "snap": {**snap, "voice": self._voice_health()}})
        finally:
            diag.unsubscribe(q)

    def _set_diag_feed(self, on: bool) -> None:
        running = self._diag_task is not None and not self._diag_task.done()
        if on and not running and self.agent is not None and self.agent.diag is not None:
            self._diag_task = asyncio.create_task(self._watch_diag())
        elif not on and running:
            self._diag_task.cancel()
            self._diag_task = None

    async def _brain_send(self, msg: dict) -> None:
        """Brain -> client, with the FSM listening in.

        The brain does not know about conversation states, and the FSM does
        not know about models. This is the one place they meet.
        """
        kind = msg.get("t")
        if kind == "tool_call":
            # A web search is legitimate thinking, not a hung reply: restart
            # the THINKING clock so the safety timeout does not kill it.
            self.fsm.touch()
        elif kind == "tts_begin":
            self.barge.arm()
            self._brain_done = False
            await self.fsm.to(State.SPEAKING, "brain_audio")
        elif kind == "tts_end":
            self._begin_drain(msg.get("turn_id"),
                              int(msg.get("total_samples") or 0))
        elif kind == "cancel" and msg.get("reason") == "barge_in":
            # Gemini heard the user talk over it.
            self._cancel_drain()
            await self.fsm.to(State.LISTENING, "barge_in")
        elif kind == "wake_rejected":
            log.info("returning to idle after an unconfirmed wake")
            self._cancel_drain()
            self.wake.reset()
            await self.fsm.to(State.IDLE, "wake_rejected")
            await self.send(msg)
            return
        elif kind == "turn_complete":
            self._brain_done = True
            if self.fsm.is_(State.IDLE):
                return  # already dropped by wake_rejected
            if not self._draining:
                # Nothing was spoken (empty transcript, stop phrase, error),
                # so there is nothing to wait for.
                await self._enter_follow_up()
            return  # internal signal; the client does not need it
        await self.send(msg)

    def _begin_drain(self, turn_id, total_samples: int) -> None:
        """Wait for the browser to finish playing before moving on."""
        self._draining = True
        self._drain_turn = turn_id
        self._cancel_drain_timer()
        seconds = total_samples / self.settings.tts_sample_rate
        grace = self.settings.client_drain_grace_ms / 1000.0
        self._drain_task = asyncio.create_task(self._drain_timeout(seconds + grace))

    async def _drain_timeout(self, seconds: float) -> None:
        """Belt and braces: if a playback report goes missing, do not hang in
        SPEAKING forever."""
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        if self._draining:
            log.info("playback report never arrived - assuming drained")
            await self._finish_drain()

    def _cancel_drain_timer(self) -> None:
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
        self._drain_task = None

    def _cancel_drain(self) -> None:
        self._draining = False
        self._drain_turn = None
        self._cancel_drain_timer()

    async def _finish_drain(self) -> None:
        self._cancel_drain()
        if self._brain_done:
            await self._enter_follow_up()

    # -- outbound ---------------------------------------------------------

    async def send(self, msg: dict) -> None:
        if self._closing or self.ws.client_state is not WebSocketState.CONNECTED:
            return
        try:
            await self.ws.send_json(msg)
        except (RuntimeError, WebSocketDisconnect):
            self._closing = True

    async def send_audio(self, frame: bytes) -> None:
        if self._closing or self.ws.client_state is not WebSocketState.CONNECTED:
            return
        try:
            await self.ws.send_bytes(frame)
        except (RuntimeError, WebSocketDisconnect):
            self._closing = True

    # -- main loop --------------------------------------------------------

    async def run(self) -> None:
        await self.send(
            {
                "t": "ready",
                "phase": 2,
                "mode": self.settings.jarvis_mode,
                "wakeWord": self.settings.wake_model,
                "micSampleRate": self.settings.mic_sample_rate,
                "ttsSampleRate": self.settings.tts_sample_rate,
                "frameSamples": self.settings.wire_frame_samples,
                "prebufferMs": self.settings.tts_prebuffer_ms,
                "settings": self.tunables.as_dict(),
            }
        )
        await self.fsm.to(State.IDLE, "connected")
        await self._build_brain()
        tools = getattr(self.brain, "tools", None)
        await self.send({"t": "brain_ready",
                         "brain": self.brain.name if self.brain else None,
                         "error": self.brain_error,
                         "tools": tools.names if tools else []})
        if self.agent is not None:
            self._events_task = asyncio.create_task(self._watch_events())

        try:
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                text = msg.get("text")
                if data is not None:
                    await self._on_mic_frame(data)
                elif text is not None:
                    await self._on_control(text)
        except WebSocketDisconnect:
            pass
        except ProtocolError as exc:
            log.warning("protocol error, closing: %s", exc)
            await self.send(
                {"t": "error", "code": "protocol", "message": str(exc), "fatal": True}
            )
        finally:
            await self.close()

    # -- audio hot path ---------------------------------------------------

    async def _on_mic_frame(self, data: bytes) -> None:
        """One 80 ms wire frame: VAD, wake word, then whatever the state wants.

        Measured cost is ~5.7 ms per frame with both models running, i.e. ~7%
        of one core, so this stays inline on the event loop. Anything heavier
        than this belongs in a worker or the audio starts to stutter.
        """
        seq, _flags, pcm = unpack_mic(data)

        if self._expected_seq is not None and seq > self._expected_seq:
            gap = seq - self._expected_seq
            self.dropped_frames += gap
            log.warning("mic seq gap: expected %d got %d (+%d)",
                        self._expected_seq, seq, gap)
        self._expected_seq = seq + 1

        self.ring.write(pcm)
        self.frames_in += 1
        self._last_level = rms_dbfs(pcm)
        now = time.monotonic()
        self._last_frame_at = now

        if self.capturing:
            self.utterance.append(pcm.copy())

        # The brain only ever hears audio once you have addressed it. In
        # Gemini mode we keep forwarding through SPEAKING too -- that is what
        # lets the model notice you talking over it.
        if self.brain is not None and self.forwarding_states():
            await self.brain.on_audio(pcm)

        # --- VAD first: it gates the wake word and drives everything else ---
        voiced_any = False
        for sub in self.vad_chunker.push(pcm):
            prob = self.vad.push(sub)
            dbfs = rms_dbfs(sub)
            voiced = prob >= self.tunables.vad_threshold
            voiced_any = voiced_any or voiced
            self.noise.update(dbfs, voiced)

            if voiced:
                self.wake.note_voice(now)

            await self._on_vad_frame(prob, voiced, dbfs, now)

        # --- wake word, only where it is allowed to fire ---
        score = 0.0
        if self.fsm.wake_enabled:
            score = self.wake.push(pcm, now)
            if self.wake.fired(score, now):
                await self._on_wake(score)

        if self.frames_in % LEVEL_REPORT_EVERY == 0:
            await self._report_level(score, voiced_any)

    def forwarding_states(self) -> bool:
        """Should this frame go to the brain?"""
        if self.fsm.is_(State.LISTENING, State.THINKING):
            return True
        # Gemini needs to keep hearing us while it talks (barge-in) and
        # during the follow-up window (so you can just carry on).
        if self.brain is not None and self.brain.handles_turn_detection:
            return self.fsm.is_(State.SPEAKING, State.FOLLOW_UP_WINDOW)
        return False

    async def _on_vad_frame(self, prob: float, voiced: bool, dbfs: float,
                            now: float) -> None:
        """Per-32 ms decisions, dispatched on the current state."""
        state = self.fsm.state
        brain_turns = self.brain is not None and self.brain.handles_turn_detection

        if state is State.LISTENING and brain_turns:
            # The model does its own endpointing, so all we police here is
            # the case where the wake word fired and nobody actually spoke.
            if voiced:
                self._heard_speech = True
            elif (not self._heard_speech
                  and self.fsm.elapsed_ms() >= self.tunables.no_speech_timeout_ms):
                await self._abandon_utterance("no_speech_timeout")
            return

        if (state is State.THINKING
                and self.fsm.elapsed_ms() >= self.settings.thinking_timeout_ms):
            # Safety net: a reply that never starts must not wedge the UI.
            log.warning("no reply after %d ms - giving up",
                        self.settings.thinking_timeout_ms)
            await self.cancel_turn("thinking_timeout")
            if self.brain is not None:
                await self.brain.cancel("thinking_timeout")
            await self._abandon_utterance("thinking_timeout")
            return

        if state is State.SPEAKING and not brain_turns:
            if not self.tunables.barge_enabled:
                return
            if self.barge.update(prob, dbfs, self.noise.value, VAD_FRAME_MS):
                await self._barge_in()
            return

        if state is State.LISTENING:
            event = self.endpointer.update(prob, VAD_FRAME_MS)
            if event == "speech_start":
                await self.send({"t": "speech_start"})
            elif event == "endpoint":
                await self._finish_utterance("endpoint")
            elif event == "no_speech_timeout":
                await self._abandon_utterance("no_speech_timeout")
            elif event == "max_len":
                await self._finish_utterance("max_len")

        elif state is State.FOLLOW_UP_WINDOW:
            # A guard right after playback stops: the speaker driver ringing
            # out and the AEC tail both look a little like speech.
            if self.fsm.elapsed_ms() < self.settings.post_tts_guard_ms:
                return

            if voiced:
                self._followup_voice_ms += VAD_FRAME_MS
                if self._followup_voice_ms >= self.tunables.followup_trigger_ms:
                    await self._begin_listening(
                        "follow_up", preroll_ms=self.settings.followup_preroll_ms
                    )
            else:
                self._followup_voice_ms = 0.0

            if now >= self._followup_deadline:
                if self.brain is not None:
                    await self.brain.end_turn()
                await self.fsm.to(State.IDLE, "follow_up_timeout")

        elif state is State.IDLE and self.converse:
            # Conversation mode: a short stretch of real speech starts a turn,
            # the same trigger the follow-up window uses, so no wake word.
            if voiced:
                self._followup_voice_ms += VAD_FRAME_MS
                if self._followup_voice_ms >= self.tunables.followup_trigger_ms:
                    await self._begin_listening(
                        "converse", preroll_ms=self.settings.followup_preroll_ms
                    )
            else:
                self._followup_voice_ms = 0.0

    async def _report_level(self, wake_score: float, voiced: bool) -> None:
        level = self._last_level
        # Rolling peak over ~4 s. A single frame's score flashes past far too
        # quickly to read off a live gauge, and the peak is what tells you
        # whether you are close to the threshold or nowhere near it.
        now = time.monotonic()
        self._peak_window = [(t, v) for t, v in self._peak_window if now - t < 4.0]
        self._peak_window.append((now, wake_score))
        peak = max((v for _, v in self._peak_window), default=0.0)
        await self.send(
            {
                "t": "level",
                "dbfs": None if math.isinf(level) else round(level, 1),
                "noise_dbfs": round(self.noise.value, 1),
                "vad": round(self.vad.last_prob, 3),
                "wake": round(wake_score, 3),
                "wake_peak": round(peak, 3),
                "voiced": voiced,
                "state": str(self.fsm.state),
                "frames": self.frames_in,
                "dropped": self.dropped_frames,
            }
        )

    # -- turn taking ------------------------------------------------------

    async def _on_wake(self, score: float) -> None:
        """A wake fired. Confident ones are acted on; marginal ones are
        treated as a maybe and confirmed against the transcript later.

        The earcon only plays for confident wakes. A provisional one that
        turns out to be nothing should leave no trace at all -- a chirp every
        time the television says something jarvis-shaped would be worse than
        missing the odd wake.
        """
        confident = score >= self.tunables.wake_confident
        provisional = self.tunables.wake_verify and not confident
        log.info("wake fired: %.3f (%s)", score,
                 "confident" if confident else "provisional")

        await self.send({"t": "wake", "score": round(score, 3),
                         "model": self.settings.wake_model,
                         "provisional": provisional})
        if self.brain is not None:
            self.brain.require_wake = provisional
        await self._begin_listening("wake_word", preroll_ms=self.tunables.preroll_ms)

    async def _begin_listening(self, reason: str, preroll_ms: int) -> None:
        """Start capturing, seeded with audio from *before* this moment.

        The wake word only fires at the end of "hey jarvis", and people run
        straight on into the question with no pause. Without pre-roll the
        first word or two of every command is simply missing.
        """
        self.wake.reset()
        self.vad.reset()
        self.endpointer.cfg = self._endpoint_config()
        self.endpointer.reset()

        if reason != "wake_word" and self.brain is not None:
            # Conversation mode, follow-ups and barge-ins are all unambiguous
            # intent -- the wake word was never involved, so there is nothing
            # to confirm. Demanding it here would reject every follow-up.
            self.brain.require_wake = False

        self.utterance = [self.ring.tail_ms(preroll_ms, self.settings.mic_sample_rate)]
        self.capturing = True
        self._followup_voice_ms = 0.0
        self._heard_speech = False
        if self.agent is not None:
            self.agent.active_session = self

        await self.fsm.to(State.LISTENING, reason)

        if self.brain is not None:
            await self.brain.begin_turn()
            # Replay the pre-roll so the brain hears the whole question,
            # including whatever landed before the wake word resolved.
            await self.brain.on_audio(self.utterance[0])

    def _drain_utterance(self) -> np.ndarray:
        pcm = (np.concatenate(self.utterance) if self.utterance
               else np.zeros(0, dtype=np.int16))
        self.utterance = []
        self.capturing = False
        return pcm

    async def _abandon_utterance(self, reason: str) -> None:
        """Wake word fired but nobody spoke. Costs nothing, so say nothing."""
        self._drain_utterance()
        self.wake.reset()
        if self.brain is not None:
            await self.brain.end_turn()
        log.info("utterance abandoned (%s)", reason)
        await self.fsm.to(State.IDLE, reason)

    async def _finish_utterance(self, reason: str) -> None:
        pcm = self._drain_utterance()
        self.utterance_count += 1
        seconds = pcm.size / self.settings.mic_sample_rate

        path = None
        if self.settings.debug_dump_audio:
            DEBUG_DIR.mkdir(exist_ok=True)
            path = DEBUG_DIR / f"utt_{self.utterance_count:03d}.wav"
            path.write_bytes(wav_bytes(pcm, self.settings.mic_sample_rate))

        log.info(
            "utterance %d: %.2fs (%s, speech %.0fms, level %.1f dBFS, "
            "heard %s)%s",
            self.utterance_count, seconds, reason, self.endpointer.speech_ms,
            rms_dbfs(pcm) if pcm.size else -120.0, self._heard_speech,
            f" -> {path.name}" if path else "",
        )
        await self.send(
            {
                "t": "utterance",
                "index": self.utterance_count,
                "seconds": round(seconds, 2),
                "speech_ms": round(self.endpointer.speech_ms),
                "reason": reason,
                "wav": path.name if path else None,
            }
        )

        await self.fsm.to(State.THINKING, reason)

        if self.brain is not None:
            # Gemini already has the audio; the pipeline starts STT here.
            await self.brain.end_turn()
            return
        self._reply_task = asyncio.create_task(self._placeholder_reply())

    async def _placeholder_reply(self) -> None:
        """Stands in for STT -> LLM -> TTS until Phase 2.

        Exists so the whole state machine -- including the follow-up window --
        can be exercised and listened to before any model is in the path.
        """
        try:
            await asyncio.sleep(1.0)
            self.active_turn_id += 1
            await self._stream_tone(self.active_turn_id, 1.2)
            await self._enter_follow_up()
        except asyncio.CancelledError:
            raise

    async def _barge_in(self) -> None:
        """The user talked over the reply. Stop, and listen from mid-word.

        Pre-roll matters as much here as at the wake word: by the time 240 ms
        of speech has confirmed the interruption, the first syllable is
        already in the past.
        """
        log.info("barge-in detected")
        self._cancel_drain()
        if self.brain is not None:
            await self.brain.cancel("barge_in")
        self._awaiting_played = True
        await self._begin_listening("barge_in",
                                    preroll_ms=self.tunables.barge_preroll_ms)

    async def _enter_follow_up(self) -> None:
        self._followup_voice_ms = 0.0
        self._followup_deadline = (
            time.monotonic() + self.tunables.followup_ms / 1000.0
        )
        self.vad.reset()
        self.wake.reset()
        await self.fsm.to(State.FOLLOW_UP_WINDOW, "reply_finished")
        await self.send({"t": "follow_up", "ms": self.tunables.followup_ms})
        # The mic-frame path closes the window, but with the mic off (typing)
        # no frames arrive and it would stay open forever -- and nothing that
        # waits for IDLE, like a timer or a finished web task, would be said.
        asyncio.create_task(self._expire_follow_up(self._followup_deadline))

    async def _expire_follow_up(self, deadline: float) -> None:
        await asyncio.sleep(max(0.0, deadline - time.monotonic()) + 0.25)
        # Only this window: a newer one has its own deadline and timer.
        if self.fsm.is_(State.FOLLOW_UP_WINDOW) and self._followup_deadline == deadline:
            if self.brain is not None:
                await self.brain.end_turn()
            await self.fsm.to(State.IDLE, "follow_up_timeout")

    # -- placeholder audio out (real TTS lands here in Phase 2) -----------

    async def _stream_tone(self, turn_id: int, seconds: float) -> None:
        sr = self.settings.tts_sample_rate
        chunk = self.settings.tts_chunk_samples
        total = int(seconds * sr)
        fade = int(0.01 * sr)

        await self.fsm.to(State.SPEAKING, "tts")
        await self.send({"t": "tts_begin", "turn_id": turn_id, "sampleRate": sr})
        sent = 0
        idx = 0
        while sent < total:
            n = min(chunk, total - sent)
            t = np.arange(sent, sent + n, dtype=np.float64) / sr
            wave_f = 0.2 * np.sin(2 * np.pi * 440.0 * t)
            if sent == 0:
                k = min(fade, n)
                wave_f[:k] *= np.linspace(0.0, 1.0, k)
            if sent + n >= total:
                k = min(fade, n)
                wave_f[-k:] *= np.linspace(1.0, 0.0, k)

            pcm = (wave_f * 32767).astype(np.int16)
            await self.send_audio(
                pack_tts(turn_id, idx, sent, pcm,
                         first=(idx == 0), last=(sent + n >= total))
            )
            sent += n
            idx += 1
            await asyncio.sleep((n / sr) * 0.8)

        await self.send({"t": "tts_end", "turn_id": turn_id, "total_samples": sent})

    async def cancel_turn(self, reason: str) -> None:
        """Client first, then teardown -- the browser flush is what is heard."""
        task = self._reply_task
        if task is None or task.done():
            return
        await self.send(
            {"t": "cancel", "turn_id": self.active_turn_id, "reason": reason}
        )
        self.active_turn_id += 1
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._reply_task = None

    # -- control ----------------------------------------------------------

    async def _on_control(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            log.warning("non-JSON control frame ignored")
            return

        kind = msg.get("t")

        if kind == "hello":
            log.info(
                "client hello: rate=%s frame=%s aec=%s outLatency=%sms",
                msg.get("sampleRate"), msg.get("frameSamples"),
                msg.get("aec"), msg.get("outputLatencyMs"),
            )
            if msg.get("sampleRate") != self.settings.mic_sample_rate:
                await self.send({
                    "t": "error", "code": "sample_rate", "fatal": False,
                    "message": "browser gave {} Hz, expected {}".format(
                        msg.get("sampleRate"), self.settings.mic_sample_rate),
                })
        elif kind == "ping":
            await self.send({"t": "pong", "ts": msg.get("ts")})
        elif kind == "config":
            changed = self.tunables.patch(msg.get("patch") or {})
            if changed:
                log.info("tunables patched: %s", ", ".join(changed))
                self.wake.threshold = self.tunables.wake_threshold
                self.vad.threshold = self.tunables.vad_threshold
                self.endpointer.cfg = self._endpoint_config()
                self.barge.cfg = self._barge_config()
            await self.send({"t": "config_ack", "changed": changed,
                             "settings": self.tunables.as_dict()})
        elif kind == "converse":
            on = bool(msg.get("on"))
            if on == self.converse:
                return
            self.converse = on
            self._followup_voice_ms = 0.0
            log.info("conversation mode %s", "on" if on else "off")
            if not on and not self.fsm.is_(State.IDLE):
                # Off means stop: drop whatever is in flight.
                self._cancel_drain()
                await self.cancel_turn("user_stop")
                if self.brain is not None:
                    await self.brain.cancel("user_stop")
                self._drain_utterance()
                if self.brain is not None:
                    await self.brain.end_turn()
                await self.fsm.to(State.IDLE, "converse_off")
            await self.send({"t": "converse", "on": on})
        elif kind == "tone_test":
            await self.cancel_turn("superseded")
            self.active_turn_id += 1
            self._reply_task = asyncio.create_task(
                self._stream_tone(self.active_turn_id,
                                  float(msg.get("seconds", 3.0)))
            )
        elif kind == "text":
            self.agent.active_session = self
            text = str(msg.get("text") or "").strip()[:2000]
            if not text:
                return
            if self.brain is None:
                await self.send({"t": "error", "code": "no_brain", "fatal": False,
                                 "message": "no brain available to answer"})
                return
            # A typed message replaces whatever is in flight, like a barge-in.
            if not self.fsm.is_(State.IDLE, State.FOLLOW_UP_WINDOW):
                self._cancel_drain()
                await self.cancel_turn("superseded")
                await self.brain.cancel("superseded")
                self._drain_utterance()
            await self.fsm.to(State.THINKING, "typed")
            try:
                accepted = await self.brain.ask(text)
            except Exception as exc:
                # Quota, network, auth: report it, but keep the session alive.
                log.exception("typed message failed")
                await self.send({"t": "error", "code": "brain", "fatal": False,
                                 "message": str(exc)[:200]})
                await self.fsm.to(State.IDLE, "ask_failed")
                return
            if not accepted:
                await self.send({"t": "error", "code": "busy", "fatal": False,
                                 "message": "still finishing the last reply"})
                await self.fsm.to(State.IDLE, "ask_declined")
        elif kind in ("web_reply", "web_stop"):
            # The web task card's Yes / No / Stop buttons.
            if not self.settings.web_agent_enabled:
                return
            web = self.agent.web_agent
            if kind == "web_stop":
                await web.stop()
            else:
                answer = str(msg.get("answer") or "").strip()[:500]
                if answer:
                    asyncio.create_task(web.reply(answer))
        elif kind == "diag_sub":
            # The Systems view is open (on) or closed: stats only flow while
            # someone is looking at them.
            if self.agent is None or self.agent.diag is None:
                await self.send({"t": "diag", "snap": None,
                                 "error": "diagnostics disabled (DIAG_ENABLED=false)"})
                return
            self._set_diag_feed(bool(msg.get("on")))
        elif kind == "stop_audio":
            self._cancel_drain()
            await self.cancel_turn("user_stop")
            await self.fsm.to(State.IDLE, "user_stop")
        elif kind == "playback_state":
            if self._awaiting_played:
                self._awaiting_played = False
                played = int(msg.get("played_samples") or 0)
                commit = getattr(self.brain, "commit_interrupted", None)
                if commit is not None:
                    commit(played)
            # The browser is the only thing that knows when sound actually
            # stopped coming out of the speakers.
            if (self._draining
                    and not msg.get("playing")
                    and int(msg.get("queued_samples") or 0) <= 0):
                await self._finish_drain()
        else:
            log.debug("unhandled control message: %s", kind)

    async def close(self) -> None:
        self._closing = True
        if self.agent is not None and self.agent.active_session is self:
            self.agent.active_session = None
        if self._events_task is not None:
            self._events_task.cancel()
        self._set_diag_feed(False)
        if self.brain is not None:
            with contextlib.suppress(Exception):
                await self.brain.close()
        if self._reply_task and not self._reply_task.done():
            self._reply_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reply_task
        log.info(
            "session closed: %d frames, %d dropped, %d utterances, wake %s",
            self.frames_in, self.dropped_frames, self.utterance_count,
            self.wake.stats,
        )
