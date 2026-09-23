"""Phase 2: does Jarvis actually answer out loud?

A real microphone never stops sending, so neither does this probe -- it
streams silence between clips. Gemini's VAD relies on hearing that silence to
know the question has ended.
"""
import asyncio, json, os, struct, sys, time, wave
import numpy as np
import websockets

MAGIC, MIC = 0xA1, 0x01
MIC_HDR = struct.Struct("<BBHI")
TTS_HDR = struct.Struct("<BBHIII")
URL = os.environ.get("JARVIS_PROBE_URL", "ws://127.0.0.1:8080/ws")
FIX = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures"
fails, ev, audio = [], [], []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}",
          flush=True)
    if not ok:
        fails.append(label)


def load(name):
    with wave.open(f"{FIX}/{name}.wav") as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2")


def first(kind):
    return next((e for e in ev if e.get("t") == kind), None)


class Mic:
    """Continuous 16 kHz sender; queue speech into it, silence otherwise."""

    def __init__(self, ws):
        self.ws = ws
        self.seq = 0
        self.pending = np.zeros(0, dtype=np.int16)
        self.task = asyncio.create_task(self._run())

    def say(self, pcm):
        self.pending = np.concatenate([self.pending, pcm])

    async def _run(self):
        silence = np.zeros(1280, dtype=np.int16)
        while True:
            if self.pending.size >= 1280:
                frame, self.pending = self.pending[:1280], self.pending[1280:]
            else:
                frame = silence
            await self.ws.send(
                MIC_HDR.pack(MAGIC, MIC, 0, self.seq) + frame.tobytes())
            self.seq += 1
            await asyncio.sleep(0.080)


async def receiver(ws):
    async for m in ws:
        if isinstance(m, str):
            ev.append(json.loads(m))
        else:
            _, _, fl, turn, idx, off = TTS_HDR.unpack_from(m, 0)
            audio.append((turn, idx, off, (len(m) - 16) // 2))


async def wait_for(kind, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if first(kind):
            return True
        await asyncio.sleep(0.05)
    return False


async def main():
    async with websockets.connect(URL, max_size=None) as ws:
        rx = asyncio.create_task(receiver(ws))
        await wait_for("ready", 10)
        check(first("ready").get("phase") == 2, "phase 2 backend")
        await ws.send(json.dumps({"t": "hello", "proto": 1, "sampleRate": 16000,
                                  "frameSamples": 1280, "aec": True,
                                  "outputLatencyMs": 40}))
        await wait_for("brain_ready", 10)
        br = first("brain_ready")
        check(br and br.get("brain") in ("gemini_live", "pipeline"),
              "a brain connected",
              br and (br.get("brain") or br.get("error", "")))

        mic = Mic(ws)
        await asyncio.sleep(0.5)
        t0 = time.time()
        mic.say(load("hey_jarvis_q"))

        check(await wait_for("wake", 8), "wake fired")
        t_wake = time.time()
        check(await wait_for("transcript", 20), "user speech transcribed")
        tr = first("transcript")
        if tr:
            print(f"        heard: {tr['text'].strip()!r}", flush=True)
            check("python" in tr["text"].lower(), "transcript is correct")

        check(await wait_for("tts_begin", 20), "spoken reply started")
        t_audio = time.time()
        check(await wait_for("tts_end", 30), "reply finished cleanly")

        deltas = "".join(e["delta"] for e in ev if e.get("t") == "assistant_delta")
        print(f"        said : {deltas.strip()[:120]!r}", flush=True)
        check(len(deltas.strip()) > 10, "reply text captured")
        check(len(audio) > 5, "audio frames received", f"{len(audio)} frames")
        samples = sum(a[3] for a in audio)
        check(samples > 24000, "at least 1 s of speech", f"{samples/24000:.2f}s")

        by_turn = {}
        for turn, idx, off, n in audio:
            by_turn.setdefault(turn, []).append((off, n))
        ok = all(c[i][0] + c[i][1] == c[i + 1][0]
                 for c in (sorted(v) for v in by_turn.values())
                 for i in range(len(c) - 1))
        check(ok, "audio offsets contiguous (gapless)")

        states = [e["to"] for e in ev if e.get("t") == "state"]
        check("SPEAKING" in states, "reached SPEAKING", str(states))
        check(await wait_for("follow_up", 15), "reached follow-up window")

        print(f"\n  wake -> first audio: {t_audio - t_wake:.1f}s"
              f"   (total from start of speech: {t_audio - t0:.1f}s)", flush=True)
        mic.task.cancel()
        rx.cancel()

    print("\n" + ("ALL CHECKS PASSED" if not fails else "FAILED: " + "; ".join(fails)))
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
