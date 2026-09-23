"""Gemini Live: native speech-to-speech.

This is the mode that sounds human. The model takes audio in and emits audio
out, so prosody, emphasis and pacing survive -- there is no text bottleneck in
the middle flattening everything. It also does its own turn detection and
interruption.

What stays local is the wake word. Audio is only forwarded once you have said
"Jarvis", so nothing leaves the machine while you are talking to someone else,
and idle time costs no quota.

Measured here: ~660 ms to connect, first audio essentially as soon as it has
recognised the question.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import numpy as np

from ...core.runtime import AgentRuntime
from ...core.tools import ToolRegistry
from ...config import Settings
from ..voice.protocol import pack_tts
from .base import Brain, SendAudio, SendJson

log = logging.getLogger("jarvis.gemini")

OUT_RATE = 24000
PTT_TAIL_SILENCE_S = 1.0  # enough for Gemini's end-of-speech detector


class GeminiLiveBrain(Brain):
    name = "gemini_live"

    def __init__(self, settings: Settings, send_json: SendJson,
                 send_audio: SendAudio, agent: AgentRuntime | None = None) -> None:
        self.settings = settings
        self.send_json = send_json
        self.send_audio = send_audio
        self.agent = agent
        self.tools: ToolRegistry | None = agent.registry(self.name) if agent else None
        # Tool calls run as tasks, never inline in the pump: a slow search
        # must not stop us hearing Gemini say it was interrupted.
        self._tool_tasks: dict[str, asyncio.Task] = {}
        self._tool_turn = False  # the current model turn called a tool

        self._client = None
        self._session = None
        self._session_cm = None
        self._pump: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self._last_activity = 0.0

        self.turn_id = 0
        self._chunk_idx = 0
        self._sample_offset = 0
        self._turn_open = False
        self._forwarding = False
        self._heard_text = ""
        self._wake_checked = False

    @property
    def handles_turn_detection(self) -> bool:
        return True

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        from google import genai

        if not self.settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=self.settings.gemini_api_key)

    def _config(self) -> dict:
        # Built at connect time, so facts remembered mid-session reach the
        # prompt on the next reconnect (sessions idle out after a minute).
        prompt = (self.agent.system_prompt(bool(self.tools)) if self.agent
                  else self.settings.system_prompt)
        config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": prompt,
            "input_audio_transcription": {},
            "output_audio_transcription": {},
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {
                        "voice_name": self.settings.gemini_voice
                    }
                }
            },
        }
        if self.tools:
            config["tools"] = self.tools.gemini_tools()
        return config

    async def _ensure_session(self) -> None:
        """Connect if needed; sessions are kept warm between turns.

        Reconnecting costs ~660 ms, which is most of a turn's latency budget,
        so an idle session is worth holding on to for a minute.
        """
        async with self._connect_lock:
            if self._session is not None:
                return
            t0 = time.perf_counter()
            self._session_cm = self._client.aio.live.connect(
                model=self.settings.gemini_live_model, config=self._config()
            )
            self._session = await self._session_cm.__aenter__()
            self._pump = asyncio.create_task(self._pump_responses())
            log.info("gemini live connected in %.0f ms (%s)",
                     (time.perf_counter() - t0) * 1000,
                     self.settings.gemini_live_model)
            await self.send_json({"t": "brain", "mode": self.name,
                                  "model": self.settings.gemini_live_model})

    async def _teardown(self) -> None:
        for task in self._tool_tasks.values():
            task.cancel()
        self._tool_tasks.clear()
        if self._pump and not self._pump.done():
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
        self._pump = None
        if self._session_cm is not None:
            with contextlib.suppress(Exception):
                await self._session_cm.__aexit__(None, None, None)
        self._session = None
        self._session_cm = None

    # -- turn handling ----------------------------------------------------

    async def begin_turn(self) -> None:
        await self._ensure_session()
        self._heard_text = ""
        self._wake_checked = False
        self._forwarding = True
        self._last_activity = time.monotonic()

    async def on_audio(self, pcm: np.ndarray) -> None:
        if not self._forwarding or self._session is None:
            return
        from google.genai import types

        rate = self.settings.mic_sample_rate
        try:
            await self._session.send_realtime_input(
                audio=types.Blob(data=pcm.tobytes(),
                                 mime_type=f"audio/pcm;rate={rate}")
            )
        except Exception as exc:
            log.warning("gemini send failed: %s", exc)
            await self._fail(str(exc))

    async def end_turn(self) -> None:
        """Close the tap, first giving Gemini the silence it listens for.

        Gemini ends a turn only when it hears trailing silence. If the audio
        just stops (push-to-talk release) it never hears any and waits until
        audio resumes -- audio_stream_end alone did not make it answer. So
        feed it a second of silence, as a real mic would, then end the stream.
        """
        was_forwarding = self._forwarding
        self._forwarding = False
        if not was_forwarding or self._session is None:
            return
        from google.genai import types

        rate = self.settings.mic_sample_rate
        mime = f"audio/pcm;rate={rate}"
        chunk = np.zeros(self.settings.wire_frame_samples, dtype=np.int16).tobytes()
        try:
            for _ in range(int(rate * PTT_TAIL_SILENCE_S)
                           // self.settings.wire_frame_samples):
                await self._session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type=mime))
            await self._session.send_realtime_input(audio_stream_end=True)
        except Exception as exc:
            log.warning("gemini end-of-turn flush failed: %s", exc)

    async def cancel(self, reason: str) -> None:
        """Local stop. Gemini's own barge-in is handled in the pump."""
        if self._turn_open:
            await self.send_json({"t": "cancel", "turn_id": self.turn_id,
                                  "reason": reason})
            self._turn_open = False
        self.turn_id += 1

    async def close(self) -> None:
        self._forwarding = False
        await self._teardown()

    async def announce(self, prompt: str) -> bool:
        if self._turn_open or self._tool_tasks:
            return False
        await self._ensure_session()
        self.require_wake = False  # nobody woke us; there is nothing to confirm
        self._last_activity = time.monotonic()
        await self._session.send_client_content(
            turns={"role": "user", "parts": [
                {"text": f"[automatic notice, not said by the user] {prompt}"}]},
            turn_complete=True,
        )
        return True

    async def maybe_idle_close(self) -> bool:
        """Drop a session unused for a while, so it stops costing quota."""
        if self._session is None:
            return False
        idle = time.monotonic() - self._last_activity
        if idle < self.settings.gemini_session_idle_s:
            return False
        log.info("closing idle gemini session (%.0fs)", idle)
        await self._teardown()
        return True

    # -- response pump ----------------------------------------------------

    async def _pump_responses(self) -> None:
        """Forward Gemini's audio to the browser in our own wire format."""
        try:
            # receive() yields one model turn and then returns -- the socket
            # stays open -- so it has to be re-entered for every turn. Stop
            # reading and nothing after the first reply is ever heard. A round
            # that yields nothing means the socket really did close.
            while self._session is not None:
                got_any = False
                async for resp in self._session.receive():
                    got_any = True
                    await self._handle(resp)
                if not got_any:
                    break
            log.info("gemini stream closed by server")
            # Reconnect on the next turn. Not _teardown(): it would cancel
            # this very task.
            cm, self._session, self._session_cm = self._session_cm, None, None
            if cm is not None:
                with contextlib.suppress(Exception):
                    await cm.__aexit__(None, None, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("gemini pump ended: %s", exc)
            await self._fail(str(exc))

    async def _handle(self, resp) -> None:
        tc = getattr(resp, "tool_call", None)
        if tc is not None:
            for fc in tc.function_calls or []:
                self._start_tool(fc)
        tcc = getattr(resp, "tool_call_cancellation", None)
        if tcc is not None:
            # Gemini abandoned these calls (usually: the user interrupted).
            for call_id in tcc.ids or []:
                task = self._tool_tasks.pop(call_id, None)
                if task is not None:
                    task.cancel()

        sc = getattr(resp, "server_content", None)

        if sc is not None and getattr(sc, "interrupted", False):
            # Gemini heard the user talk over it and stopped generating.
            log.info("gemini reports interruption")
            await self.send_json({"t": "cancel", "turn_id": self.turn_id,
                                  "reason": "barge_in"})
            self._turn_open = False
            self.turn_id += 1
            return

        data = getattr(resp, "data", None)
        if data:
            # Last safe moment: it is about to speak, so decide now even if
            # the transcript is still short.
            if await self._rejected_as_unaddressed(force=True):
                return
            if not self._turn_open:
                self._turn_open = True
                self._chunk_idx = 0
                self._sample_offset = 0
                await self.send_json({"t": "tts_begin", "turn_id": self.turn_id,
                                      "sampleRate": OUT_RATE})
            pcm = np.frombuffer(data, dtype="<i2")
            await self.send_audio(
                pack_tts(self.turn_id, self._chunk_idx, self._sample_offset,
                         pcm, first=(self._chunk_idx == 0))
            )
            self._chunk_idx += 1
            self._sample_offset += pcm.size
            self._last_activity = time.monotonic()

        if sc is None:
            return

        it = getattr(sc, "input_transcription", None)
        if it is not None and it.text:
            self._heard_text += it.text
            if await self._rejected_as_unaddressed():
                return
            await self.send_json({"t": "transcript", "role": "user",
                                  "text": it.text, "turn_id": self.turn_id})
        ot = getattr(sc, "output_transcription", None)
        if ot is not None and ot.text:
            await self.send_json({"t": "assistant_delta",
                                  "turn_id": self.turn_id, "delta": ot.text})

        if getattr(sc, "turn_complete", False):
            if self._turn_open:
                await self.send_json({"t": "tts_end", "turn_id": self.turn_id,
                                      "total_samples": self._sample_offset})
                self._turn_open = False
            self.turn_id += 1
            self._last_activity = time.monotonic()
            if self._tool_turn:
                # A model turn that ends in a tool call is not the end of
                # the exchange: Gemini answers in a fresh turn once it has
                # the result (~0.9 s later, measured). Passing this on would
                # open the follow-up window before Jarvis has said anything.
                self._tool_turn = False
                return
            await self.send_json({"t": "turn_complete"})

    # Enough text to tell "hey Jarvis, ..." from "hey, there you are".
    MIN_VERIFY_CHARS = 18

    async def _rejected_as_unaddressed(self, force: bool = False) -> bool:
        """Confirm a marginal wake against what Gemini actually heard.

        Gemini streams its transcription in fragments, so the first one may
        be nothing but "Hey" -- deciding on that would throw away a perfectly
        good wake. Wait for enough text, or until it is about to speak,
        whichever comes first.
        """
        if not self.require_wake or self._wake_checked:
            return False

        from ..voice.text import has_wake_word

        text = self._heard_text.strip()
        if has_wake_word(text):
            self._wake_checked = True
            return False
        if not force and len(text) < self.MIN_VERIFY_CHARS:
            return False  # still too early to judge

        self._wake_checked = True
        log.info("unconfirmed wake, ignoring: %r", text[:60])
        await self.send_json({"t": "wake_rejected", "text": text[:120]})
        await self.send_json({"t": "turn_complete", "empty": True})
        return True

    # -- tools ------------------------------------------------------------

    def _start_tool(self, fc) -> None:
        self._tool_turn = True
        call_id = fc.id or f"{fc.name}-{len(self._tool_tasks)}"
        task = asyncio.create_task(self._run_tool(call_id, fc.name, dict(fc.args or {})))
        self._tool_tasks[call_id] = task
        task.add_done_callback(lambda _t, cid=call_id: self._tool_tasks.pop(cid, None))

    async def _run_tool(self, call_id: str, name: str, args: dict) -> None:
        from google.genai import types

        self._last_activity = time.monotonic()
        await self.send_json({"t": "tool_call", "turn_id": self.turn_id,
                              "name": name, "args": args})
        if self.tools is None:
            outcome_ok, output, ms = False, "error: tools are disabled", 0.0
        else:
            outcome = await self.tools.execute(name, args)
            outcome_ok, output, ms = outcome.ok, outcome.output, outcome.ms
        await self.send_json({"t": "tool_result", "turn_id": self.turn_id,
                              "name": name, "ok": outcome_ok, "ms": round(ms),
                              "preview": output[:200]})
        if self._session is None:
            return  # disconnected while the tool ran; nobody to tell
        key = "result" if outcome_ok else "error"
        try:
            await self._session.send_tool_response(function_responses=[
                types.FunctionResponse(id=call_id, name=name,
                                       response={key: output})])
        except Exception as exc:
            log.warning("gemini tool response failed: %s", exc)

    async def _fail(self, message: str) -> None:
        await self.send_json({"t": "error", "code": "gemini", "fatal": False,
                              "message": message[:200]})
        await self._teardown()
