"""Phase 1 end-to-end: streams real synthesised speech at the live server and
asserts the state machine does the right thing.

The speech comes from Windows SAPI (see fixtures/README.md), so this runs
with no microphone and no human.

IMPORTANT: run the server with a brain that does NOT do its own turn
detection, otherwise the local endpointer is bypassed and no `utterance`
events are emitted:

    JARVIS_MODE=offline .venv/Scripts/python.exe -m uvicorn backend.main:app

Gemini Live endpoints for itself; probe_phase2.py covers that path."""
import asyncio, json, os, struct, sys, time, wave
import numpy as np
import websockets

MAGIC, MIC = 0xA1, 0x01
MIC_HDR = struct.Struct("<BBHI")
URL = os.environ.get("JARVIS_PROBE_URL", "ws://127.0.0.1:8080/ws")
SP = sys.argv[1] if len(sys.argv) > 1 else str(
    __import__("pathlib").Path(__file__).parent / "fixtures")
fails = []
seq = 0


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        fails.append(label)


def load(name):
    with wave.open(f"{SP}/{name}.wav") as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2")


async def stream(ws, pcm, events, realtime=1.0):
    """Send pcm as 1280-sample frames, draining events as we go."""
    global seq
    for i in range(0, len(pcm) - 1279, 1280):
        await ws.send(MIC_HDR.pack(MAGIC, MIC, 0, seq) + pcm[i:i + 1280].tobytes())
        seq += 1
        await asyncio.sleep(0.080 * realtime)
        while True:
            try:
                m = await asyncio.wait_for(ws.recv(), 0.001)
            except (asyncio.TimeoutError, TimeoutError):
                break
            if isinstance(m, str):
                events.append(json.loads(m))


async def silence(ws, seconds, events, realtime=1.0):
    await stream(ws, np.zeros(int(seconds * 16000), dtype=np.int16), events, realtime)


def states(events):
    return [e["to"] for e in events if e.get("t") == "state"]


def first(events, kind):
    return next((e for e in events if e.get("t") == kind), None)


async def main():
    async with websockets.connect(URL, max_size=None) as ws:
        ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
        check(ready.get("phase") >= 1, "backend ready", f"phase {ready.get('phase')}, wake {ready.get('wakeWord')}")
        await ws.send(json.dumps({"t": "hello", "proto": 1, "sampleRate": 16000,
                                  "frameSamples": 1280, "aec": True,
                                  "outputLatencyMs": 40}))

        # ---------- 1. distractor must NOT wake it ----------
        ev = []
        await silence(ws, 0.5, ev)
        await stream(ws, load("distractor"), ev)
        await silence(ws, 0.5, ev)
        check(first(ev, "wake") is None, "distractor does not trigger wake",
              '"hey there, can you tell me the weather today"')
        check("LISTENING" not in states(ev), "stays IDLE through distractor")

        # ---------- 2. wake + question -> full turn ----------
        ev = []
        await stream(ws, load("hey_jarvis_q"), ev)
        w = first(ev, "wake")
        check(w is not None, "wake fires on 'hey jarvis, what is python'",
              f"score {w['score']}" if w else "")
        check(w and w["score"] > 0.9, "wake score is decisive")
        check("LISTENING" in states(ev), "IDLE -> LISTENING")
        check(first(ev, "speech_start") is not None, "speech_start emitted")

        await silence(ws, 1.2, ev)  # the pause that ends the turn
        utt = first(ev, "utterance")
        check(utt is not None, "utterance finalised")
        check(utt and utt["reason"] == "endpoint", "ended on silence, not timeout",
              utt and utt["reason"])
        # only asserted when the server runs with DEBUG_DUMP_AUDIO=true
        if utt and utt.get("wav"):
            check(True, "utterance written to debug/", utt["wav"])
        # preroll(0.5s) + the part of the clip after the wake word + ~0.7s silence
        check(utt and 1.5 < utt["seconds"] < 4.5, "utterance length is sane",
              f"{utt['seconds']}s")

        # ---------- 3. placeholder reply, then follow-up ----------
        deadline = time.time() + 6
        while time.time() < deadline and not first(ev, "follow_up"):
            try:
                m = await asyncio.wait_for(ws.recv(), 0.3)
            except (asyncio.TimeoutError, TimeoutError):
                continue
            if isinstance(m, str):
                ev.append(json.loads(m))
        seen = states(ev)
        check("THINKING" in seen, "LISTENING -> THINKING")
        check("SPEAKING" in seen, "THINKING -> SPEAKING")
        check(first(ev, "tts_begin") is not None, "audio turn started")
        check(first(ev, "tts_end") is not None, "audio turn ended")
        check("FOLLOW_UP_WINDOW" in seen, "SPEAKING -> FOLLOW_UP_WINDOW")
        order = [s for s in seen if s in
                 ("LISTENING", "THINKING", "SPEAKING", "FOLLOW_UP_WINDOW")]
        check(order == ["LISTENING", "THINKING", "SPEAKING", "FOLLOW_UP_WINDOW"],
              "states occur in the right order", str(order))

        # ---------- 4. follow-up with NO wake word ----------
        ev2 = []
        await silence(ws, 0.4, ev2)
        await stream(ws, load("silence_words"), ev2)
        check(first(ev2, "wake") is None, "follow-up needs no wake word")
        check("LISTENING" in states(ev2), "follow-up voice re-opens LISTENING")
        await silence(ws, 1.2, ev2)
        check(first(ev2, "utterance") is not None, "follow-up utterance captured")

        # let the reply finish so the next section starts from a known state
        await asyncio.sleep(3.0)
        while True:
            try:
                await asyncio.wait_for(ws.recv(), 0.05)
            except (asyncio.TimeoutError, TimeoutError):
                break

        # ---------- 5. follow-up window expires back to IDLE ----------
        ev3 = []
        await silence(ws, 7.5, ev3, realtime=1.0)
        check("IDLE" in states(ev3), "follow-up window times out to IDLE",
              str(states(ev3)))

        # ---------- 6. wake then say nothing ----------
        ev4 = []
        await stream(ws, load("hey_jarvis"), ev4)
        check(first(ev4, "wake") is not None, "wake fires on bare 'hey jarvis'")
        await silence(ws, 4.0, ev4)
        check(first(ev4, "utterance") is None, "no utterance from a silent wake")
        check(any(e.get("reason") == "no_speech_timeout"
                  for e in ev4 if e.get("t") == "state"),
              "abandons quietly after no_speech_timeout")

    print("\n" + ("ALL CHECKS PASSED" if not fails else "FAILED: " + "; ".join(fails)))
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
