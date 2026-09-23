"""Does Jarvis understand and answer in Hindi?

Uses conversation mode, so the wake word is not part of what is being
tested -- this measures comprehension and the spoken reply only.
"""
import asyncio, json, os, struct, sys, time, wave
import numpy as np
import websockets

MAGIC, MIC = 0xA1, 0x01
MIC_HDR = struct.Struct("<BBHI")
TTS_HDR = struct.Struct("<BBHIII")
URL = os.environ.get("JARVIS_PROBE_URL", "ws://127.0.0.1:8080/ws")
ev, audio = [], []


def load(name):
    with wave.open(f"tests/fixtures/{name}.wav") as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2")


class Mic:
    def __init__(self, ws):
        self.ws, self.seq = ws, 0
        self.pending = np.zeros(0, dtype=np.int16)
        self.task = asyncio.create_task(self._run())

    def say(self, pcm):
        self.pending = np.concatenate([self.pending, pcm])

    async def _run(self):
        silence = np.zeros(1280, dtype=np.int16)
        while True:
            if self.pending.size >= 1280:
                f, self.pending = self.pending[:1280], self.pending[1280:]
            else:
                f = silence
            await self.ws.send(MIC_HDR.pack(MAGIC, MIC, 0, self.seq) + f.tobytes())
            self.seq += 1
            await asyncio.sleep(0.080)


async def rx(ws):
    async for m in ws:
        if isinstance(m, str):
            ev.append(json.loads(m))
        else:
            audio.append((len(m) - 16) // 2)


def first(k):
    return next((e for e in ev if e.get("t") == k), None)


async def wait(k, t):
    end = time.time() + t
    while time.time() < end:
        if first(k):
            return True
        await asyncio.sleep(0.05)
    return False


async def main():
    clip = sys.argv[1] if len(sys.argv) > 1 else "hi_jarvis_q"
    async with websockets.connect(URL, max_size=None) as ws:
        t = asyncio.create_task(rx(ws))
        await wait("ready", 10)
        await ws.send(json.dumps({"t": "hello", "proto": 1, "sampleRate": 16000,
                                  "frameSamples": 1280, "aec": True,
                                  "outputLatencyMs": 40}))
        await wait("brain_ready", 10)
        print(f"  brain : {(first('brain_ready') or {}).get('brain')}")

        mic = Mic(ws)
        await asyncio.sleep(0.4)
        pcm = load(clip)
        await ws.send(json.dumps({"t": "converse", "on": True}))
        await asyncio.sleep(0.3)
        mic.say(pcm)
        await asyncio.sleep(len(pcm) / 16000 + 1.5)

        ok = await wait("tts_begin", 25)
        await wait("tts_end", 30)
        tr = first("transcript")
        said = "".join(e["delta"] for e in ev if e.get("t") == "assistant_delta")
        print(f"  heard : {(tr or {}).get('text', '(none)').strip()[:100]!r}")
        print(f"  said  : {said.strip()[:160]!r}")
        print(f"  audio : {sum(audio)/24000:.2f}s")
        deva = sum(1 for c in said if "ऀ" <= c <= "ॿ")
        print(f"  reply contains {deva} devanagari chars -> "
              f"{'answered in Hindi' if deva > 5 else 'answered in Latin script'}")
        print(f"  RESULT: {'spoke a reply' if ok and audio else 'NO REPLY'}")
        mic.task.cancel(); t.cancel()

asyncio.run(main())
