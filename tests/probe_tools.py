"""Live: both brains actually call tools against the real APIs.

Needs GROQ_API_KEY and GEMINI_API_KEY in .env and a network connection; no
server and no microphone. TTS is faked -- this checks the thinking, not the
voice.

Run: .venv/Scripts/python.exe tests/probe_tools.py
"""

import asyncio
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.runtime import AgentRuntime
from backend.config import Settings

results = []


def check(ok, name, detail=""):
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


class SilentTts:
    name = "silent"

    async def synth(self, text):
        return np.zeros(2400, np.int16)


async def probe_pipeline(settings, agent):
    import httpx

    from backend.domain.brain.pipeline import PipelineBrain, Turn
    from backend.domain.brain.llm import GroqLlm

    print("\npipeline (Groq):")
    sent = []

    async def send_json(m):
        sent.append(m)

    async def send_audio(_):
        pass

    brain = PipelineBrain(settings, send_json, send_audio, agent)
    async with httpx.AsyncClient() as client:
        brain.llm, brain.tts = GroqLlm(settings, client), SilentTts()
        for question, want in [
            ("What time is it right now, exactly?", "get_datetime"),
            ("Search the web: who won the most recent Formula 1 race?", "web_search"),
        ]:
            sent.clear()
            brain.history.add_user(question)
            brain.active_turn_id += 1
            turn = Turn(id=brain.active_turn_id)
            t0 = time.perf_counter()
            await brain._respond(turn, t0, t0)
            used = [m["name"] for m in sent if m["t"] == "tool_call"]
            ok = [m["ok"] for m in sent if m["t"] == "tool_result"]
            answer = "".join(m["delta"] for m in sent if m["t"] == "assistant_delta")
            check(want in used and all(ok), f"{question!r} -> {want}",
                  f"used {used}, {(time.perf_counter() - t0) * 1000:.0f} ms")
            check(bool(answer.strip()), "  spoken answer", repr(answer[:110]))


async def probe_gemini(settings, agent):
    from backend.domain.brain.gemini_live import GeminiLiveBrain

    print("\ngemini live:")
    sent = []
    done = asyncio.Event()

    async def send_json(m):
        sent.append(m)
        if m["t"] == "turn_complete":
            done.set()

    audio = []

    async def send_audio(b):
        audio.append(len(b))

    brain = GeminiLiveBrain(settings, send_json, send_audio, agent)
    await brain.start()
    try:
        await brain._ensure_session()
        t0 = time.perf_counter()
        await brain._session.send_client_content(
            turns={"role": "user", "parts": [{"text": "What time is it right now?"}]},
            turn_complete=True)
        await asyncio.wait_for(done.wait(), 30)  # the answer turn, not the tool turn
        used = [m["name"] for m in sent if m["t"] == "tool_call"]
        check("get_datetime" in used, "time question -> get_datetime", f"used {used}")
        check(sum(audio) > 0, "  spoke after the tool",
              f"{sum(audio) / 48000:.1f}s audio, "
              f"{(time.perf_counter() - t0) * 1000:.0f} ms total")
    finally:
        await brain.close()


async def main():
    settings = Settings()
    agent = AgentRuntime(settings)
    await agent.start()
    try:
        print("tools offered:", ", ".join(agent.registry("probe").names))
        if settings.groq_api_key:
            await probe_pipeline(settings, agent)
        if settings.gemini_api_key:
            await probe_gemini(settings, agent)
    finally:
        await agent.close()
    print(f"\n{sum(results)} passed, {len(results) - sum(results)} failed")
    sys.exit(0 if results and all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
