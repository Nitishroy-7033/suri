"""STT -> LLM -> TTS, streamed and cancellable.

Tools slot into the LLM stage: a round that ends in tool calls runs them and
asks again, speaking any text from the first round while the tools work.

The whole design is about two numbers: how long before Jarvis makes a sound,
and how long before he stops when you interrupt.

For the first, nothing waits for the whole reply. Tokens feed a clause
splitter, the first clause is deliberately tiny, and TTS starts on it while
the rest is still being generated.

For the second, every stage runs in a TaskGroup that gets cancelled as one.
The rule that makes that work: no bare `except` anywhere in this file or the
services it calls. CancelledError derives from BaseException, and swallowing
it turns an interruption into a hang.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field

import numpy as np

from ...core.runtime import AgentRuntime
from ..memory.conversation import Conversation, SpokenSpan, truncate_to_heard
from ...core.tools import ToolRegistry
from ...config import Settings
from .llm import ToolCall
from ..voice.protocol import pack_tts
from ..voice.text import (ClauseSplitter, has_wake_word, is_stop_phrase,
                    strip_wake_word, text_for_speech)
from .base import Brain, SendAudio, SendJson

log = logging.getLogger("jarvis.pipeline")

OUT_RATE = 24000


@dataclass
class Turn:
    id: int
    task: asyncio.Task | None = None
    full_text: str = ""  # everything spoken, across tool rounds
    reply_text: str = ""  # the final round only; what goes into history
    tool_calls: int = 0
    spans: list[SpokenSpan] = field(default_factory=list)
    samples_sent: int = 0
    spoke: bool = False


class PipelineBrain(Brain):
    name = "pipeline"

    def __init__(self, settings: Settings, send_json: SendJson,
                 send_audio: SendAudio, agent: AgentRuntime | None = None) -> None:
        self.settings = settings
        self.send_json = send_json
        self.send_audio = send_audio
        self.agent = agent
        self.tools: ToolRegistry | None = agent.registry(self.name) if agent else None

        self.history = Conversation(
            system_prompt=settings.system_prompt,
            max_turns=settings.max_history_turns,
            ttl_seconds=settings.history_ttl_s,
        )
        self._client = None
        self.stt = None
        self.llm = None
        self.tts = None

        self.active_turn_id = 0
        self.turn: Turn | None = None
        self._buffer: list[np.ndarray] = []
        self._capturing = False

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        import httpx

        from ...core.models import ModelHub
        from ..voice.stt import GroqStt
        from ..voice.tts import EdgeTts, GroqTts

        self._client = httpx.AsyncClient(http2=False)
        self.stt = GroqStt(self.settings, self._client)

        # Whatever VOICE_LLM__* says, with its fallbacks; offline keeps to
        # the local ones (Ollama, LM Studio...).
        hub = self.agent.models if self.agent is not None else ModelHub(self.settings)
        self.llm = hub.get("voice_llm", local_only=self.settings.jarvis_mode == "offline")
        why = self.llm.unavailable()
        if why:
            raise RuntimeError(f"no voice LLM available: {why}")

        self.tts = await self._pick_tts(GroqTts, EdgeTts)
        log.info("pipeline: stt=%s llm=%s(%s) tts=%s tools=%s", self.stt.name,
                 self.llm.name, getattr(self.llm, "model", "?"), self.tts.name,
                 ",".join(self.tools.names) if self.tools else "none")
        asyncio.create_task(self.tts.warmup())

    async def _pick_tts(self, GroqTts, EdgeTts):
        """Prefer Groq's Orpheus, fall back to edge-tts.

        Orpheus needs a one-off terms acceptance, so the only honest way to
        know if it is available is to ask it for a syllable.
        """
        want = self.settings.tts_engine
        if want in ("auto", "groq"):
            try:
                engine = GroqTts(self.settings, self._client)
                await asyncio.wait_for(engine.synth("Hi."), timeout=15)
                log.info("groq tts available")
                return engine
            except Exception as exc:
                if want == "groq":
                    raise
                log.info("groq tts unavailable (%s) - using edge-tts",
                         str(exc)[:90])
        return EdgeTts(self.settings)

    async def close(self) -> None:
        await self.cancel("close")
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()

    # -- capture ----------------------------------------------------------

    async def begin_turn(self) -> None:
        self._buffer = []
        self._capturing = True

    async def on_audio(self, pcm: np.ndarray) -> None:
        if self._capturing:
            self._buffer.append(pcm.copy())

    async def end_turn(self) -> None:
        """The session's endpointer says the user has finished."""
        if not self._capturing:
            return
        self._capturing = False
        pcm = (np.concatenate(self._buffer) if self._buffer
               else np.zeros(0, dtype=np.int16))
        self._buffer = []
        if pcm.size == 0:
            return

        self.active_turn_id += 1
        turn = Turn(id=self.active_turn_id)
        self.turn = turn
        turn.task = asyncio.create_task(self._run_turn(turn, pcm))

    async def cancel(self, reason: str) -> None:
        """Stop everything, client first.

        Order is deliberate and stays this way: Python teardown takes tens of
        milliseconds, but what the user perceives is when the sound stops.
        """
        turn = self.turn
        if turn is None or turn.task is None or turn.task.done():
            return

        await self.send_json({"t": "cancel", "turn_id": turn.id, "reason": reason})
        self.active_turn_id += 1  # invalidates any chunk still in flight
        turn.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await turn.task
        # self.turn is deliberately kept: commit_interrupted() needs its
        # spans once the browser reports how much actually played, and
        # clears it then.

    def commit_interrupted(self, played_samples: int) -> None:
        """Store only what the user actually heard.

        Called once the browser reports how much audio really played. If we
        stored the full generated text instead, Jarvis would believe he had
        said things nobody heard, and the next turn would make no sense.
        """
        turn = self.turn
        if turn is None or not turn.spoke:
            return
        heard = truncate_to_heard(turn.spans, played_samples)
        if heard:
            self.history.add_assistant(heard, interrupted=True)
            log.info("kept %d of %d chars the user actually heard",
                     len(heard), len(turn.full_text))
        self.turn = None

    # -- the turn ---------------------------------------------------------

    async def _run_turn(self, turn: Turn, pcm: np.ndarray) -> None:
        t0 = time.perf_counter()
        try:
            raw = await self.stt.transcribe(pcm, self.settings.mic_sample_rate)
            t_stt = time.perf_counter()

            if self.require_wake and not has_wake_word(raw):
                # The wake word only scored marginally and the transcript does
                # not actually address Jarvis. Drop it before spending an LLM
                # call, and say nothing -- a false wake should be invisible.
                log.info("unconfirmed wake, ignoring: %r", raw[:60])
                await self.send_json({"t": "wake_rejected", "text": raw[:120]})
                await self.send_json({"t": "turn_complete", "empty": True})
                return

            text = strip_wake_word(raw)

            if len(text) < self.settings.min_transcript_chars:
                log.info("nothing usable transcribed (%.0f ms)",
                         (t_stt - t0) * 1000)
                await self.send_json({"t": "turn_complete", "empty": True})
                return

            await self.send_json({"t": "transcript", "role": "user",
                                  "text": text, "turn_id": turn.id})

            if is_stop_phrase(text):
                # No LLM call: this is what people try first, and it should
                # be instant and free.
                log.info("stop phrase: %r", text)
                await self.send_json({"t": "turn_complete", "stopped": True})
                return

            self.history.add_user(text)
            await self._respond(turn, t0, t_stt)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._turn_failed(exc)

    async def announce(self, prompt: str) -> bool:
        if self.turn is not None and self.turn.task and not self.turn.task.done():
            return False
        self.active_turn_id += 1
        turn = Turn(id=self.active_turn_id)
        self.turn = turn
        turn.task = asyncio.create_task(self._run_announce(turn, prompt))
        return True

    async def _run_announce(self, turn: Turn, prompt: str) -> None:
        t0 = time.perf_counter()
        try:
            # Marked so the model does not thank the user for saying it.
            self.history.add_user(f"[automatic notice, not said by the user] {prompt}")
            await self._respond(turn, t0, t0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._turn_failed(exc)

    async def speak(self, text: str) -> bool:
        if self.turn is not None and self.turn.task and not self.turn.task.done():
            return False
        self.active_turn_id += 1
        turn = Turn(id=self.active_turn_id)
        self.turn = turn
        turn.task = asyncio.create_task(self._run_speak(turn, text))
        return True

    async def _run_speak(self, turn: Turn, text: str) -> None:
        """Words someone else already chose: straight to TTS, no LLM call."""
        t0 = time.perf_counter()
        try:
            await self._speak_reply(turn, t0, fixed_text=text)
            if turn.reply_text.strip():
                self.history.add_assistant(turn.reply_text)
            await self.send_json({"t": "turn_complete"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._turn_failed(exc)

    async def ask(self, text: str) -> bool:
        if self.turn is not None and self.turn.task and not self.turn.task.done():
            return False
        self.active_turn_id += 1
        turn = Turn(id=self.active_turn_id)
        self.turn = turn
        turn.task = asyncio.create_task(self._run_text(turn, text))
        return True

    async def _run_text(self, turn: Turn, text: str) -> None:
        # No transcript echo: the browser already shows what was typed.
        t0 = time.perf_counter()
        try:
            self.history.add_user(text)
            await self._respond(turn, t0, t0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._turn_failed(exc)

    async def _respond(self, turn: Turn, t0: float, t_stt: float) -> None:
        if self.agent is not None:
            # Rebuilt per turn: "remember that..." should apply at once.
            self.history.system_prompt = self.agent.system_prompt(bool(self.tools))
        await self._speak_reply(turn, t_stt)

        if turn.reply_text.strip():
            self.history.add_assistant(turn.reply_text)
        await self.send_json({"t": "turn_complete"})
        log.info("turn %d: stt %.0fms, total %.0fms, %.1fs audio, %d tool call(s)",
                 turn.id, (t_stt - t0) * 1000,
                 (time.perf_counter() - t0) * 1000,
                 turn.samples_sent / OUT_RATE, turn.tool_calls)

    async def _turn_failed(self, exc: Exception) -> None:
        log.exception("turn failed")
        await self.send_json({"t": "error", "code": "pipeline",
                              "fatal": False, "message": str(exc)[:200]})
        await self.send_json({"t": "turn_complete", "failed": True})

    async def _call_tool(self, turn: Turn, call: ToolCall):
        try:
            shown = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            shown = call.arguments[:300]
        await self.send_json({"t": "tool_call", "turn_id": turn.id,
                              "name": call.name, "args": shown})
        outcome = await self.tools.execute(call.name, call.arguments)
        turn.tool_calls += 1
        await self.send_json({"t": "tool_result", "turn_id": turn.id,
                              "name": call.name, "ok": outcome.ok,
                              "ms": round(outcome.ms),
                              "preview": outcome.output[:1200]})
        return outcome

    async def _speak_reply(self, turn: Turn, t_stt: float,
                           fixed_text: str | None = None) -> None:
        """Three stages in one TaskGroup: tokens -> clauses -> audio.

        Bounded queues on purpose. Without them a long reply would be fully
        synthesised ahead of playback and then thrown away the moment the
        user interrupts -- wasted CPU exactly when the next turn needs it.
        """
        # Clauses are synthesised in parallel but emitted strictly in order.
        # edge-tts costs ~1-3 s per request almost regardless of length -- it
        # is a fixed handshake, not the text -- so doing them one after
        # another leaves audible gaps mid-reply. A few in flight hides that.
        # The semaphore keeps it bounded: synthesising far ahead of playback
        # only wastes work when the user interrupts.
        jobs: asyncio.Queue[asyncio.Task | None] = asyncio.Queue()
        gate = asyncio.Semaphore(self.settings.tts_concurrency)
        first_audio: list[float] = []
        marks: dict[str, float] = {}

        def mark(name: str) -> None:
            marks.setdefault(name, time.perf_counter())

        async def synth_one(clause: str) -> tuple[str, np.ndarray]:
            async with gate:
                spoken = text_for_speech(clause)
                if not spoken:
                    return clause, np.zeros(0, dtype=np.int16)
                mark("tts_start")
                pcm = await self.tts.synth(spoken)
                mark("tts_done")
                return clause, pcm

        splitter = ClauseSplitter(
            first_max_chars=self.settings.first_clause_max_chars,
            min_chars=self.settings.clause_min_chars,
            max_chars=self.settings.clause_max_chars,
        )

        async def say(delta: str) -> None:
            turn.full_text += delta
            await self.send_json({"t": "assistant_delta",
                                  "turn_id": turn.id, "delta": delta})
            for clause in splitter.feed(delta):
                mark("clause_first")
                await jobs.put(asyncio.create_task(synth_one(clause)))

        async def flush() -> None:
            for clause in splitter.flush():
                await jobs.put(asyncio.create_task(synth_one(clause)))

        async def generate() -> None:
            """LLM rounds until the model answers instead of calling a tool.

            Each round's text is spoken as it streams, so "let me look that
            up" plays while the search runs rather than after it.
            """
            if fixed_text is not None:
                try:
                    await say(fixed_text)
                    turn.reply_text = fixed_text
                    await flush()
                finally:
                    await jobs.put(None)
                return
            messages = self.history.for_llm()
            tools = self.tools.openai_tools() if self.tools else None
            max_rounds = self.settings.max_tool_rounds
            try:
                for rnd in range(max_rounds + 1):
                    # The last round offers no tools, so the model must answer
                    # with what it has instead of looping on searches.
                    offer = tools if tools and rnd < max_rounds else None
                    calls: list[ToolCall] = []
                    said = ""
                    async for item in self.llm.stream(messages, tools=offer):
                        if isinstance(item, ToolCall):
                            calls.append(item)
                            continue
                        mark("llm_first")
                        if not said and turn.full_text:
                            await say(" ")  # keep rounds from running together
                        said += item
                        await say(item)
                    if not calls:
                        turn.reply_text = said
                        break
                    await flush()
                    assistant = {"role": "assistant", "content": said or None,
                                 "tool_calls": [c.as_message_part() for c in calls]}
                    outcomes = await asyncio.gather(
                        *(self._call_tool(turn, c) for c in calls))
                    results = [{"role": "tool", "tool_call_id": c.id,
                                "content": o.output}
                               for c, o in zip(calls, outcomes)]
                    messages += [assistant, *results]
                    self.history.add_tool_exchange(assistant, results)
                await flush()
            finally:
                await jobs.put(None)

        async def emit() -> None:
            chunk = self.settings.tts_chunk_samples
            idx = 0
            while True:
                job = await jobs.get()
                if job is None:
                    return
                clause, pcm = await job
                # A late chunk from a turn the user already killed.
                if turn.id != self.active_turn_id:
                    return
                if not pcm.size:
                    continue

                if not turn.spoke:
                    turn.spoke = True
                    await self.send_json({"t": "tts_begin", "turn_id": turn.id,
                                          "sampleRate": OUT_RATE})
                    first_audio.append(time.perf_counter())

                start = turn.samples_sent
                for off in range(0, pcm.size, chunk):
                    part = pcm[off:off + chunk]
                    await self.send_audio(
                        pack_tts(turn.id, idx, turn.samples_sent, part,
                                 first=(idx == 0))
                    )
                    turn.samples_sent += part.size
                    idx += 1
                turn.spans.append(SpokenSpan(start, turn.samples_sent, clause))

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(generate())
                tg.create_task(emit())
        finally:
            # Any synthesis still in flight belongs to a turn nobody is
            # listening to any more.
            while not jobs.empty():
                job = jobs.get_nowait()
                if job is not None:
                    job.cancel()

        if turn.spoke:
            await self.send_json({"t": "tts_end", "turn_id": turn.id,
                                  "total_samples": turn.samples_sent})
            if first_audio:
                def since(name: str) -> str:
                    v = marks.get(name)
                    return f"{(v - t_stt) * 1000:.0f}" if v else "-"
                log.info(
                    "first audio %.0f ms after transcript "
                    "(llm %s / clause %s / tts %s->%s ms)",
                    (first_audio[0] - t_stt) * 1000, since("llm_first"),
                    since("clause_first"), since("tts_start"), since("tts_done"),
                )
                if self.agent is not None and self.agent.diag is not None:
                    self.agent.diag.metrics.record_turn(
                        first_audio_ms=(first_audio[0] - t_stt) * 1000,
                        llm_first_ms=(marks["llm_first"] - t_stt) * 1000 if "llm_first" in marks else None,
                        spoken_s=turn.samples_sent / OUT_RATE, brain=self.name)
