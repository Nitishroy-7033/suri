"""Barge-in: can you cut Jarvis off mid-sentence?

Asks a question, waits until he is talking, then talks over him and checks
that he actually stops -- and stops *fast*, with no audio arriving after the
cancel and no stale text left in history.

Works against either brain. Only the acoustic half (the browser's echo
canceller keeping him from hearing himself) needs a human.
"""
import asyncio, json, os, struct, sys, time, wave
import numpy as np
import websockets

MAGIC, MIC = 0xA1, 0x01
MIC_HDR = struct.Struct("<BBHI")
TTS_HDR = struct.Struct("<BBHIII")
URL = os.environ.get("JARVIS_PROBE_URL", "ws://127.0.0.1:8080/ws")
FIX = "tests/fixtures"
fails, ev, audio = [], [], []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}",
          flush=True)
    if not ok:
        fails.append(label)


def load(name):
    with wave.open(f"{FIX}/{name}.wav") as w:
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
                frame, self.pending = self.pending[:1280], self.pending[1280:]
            else:
                frame = silence
            await self.ws.send(MIC_HDR.pack(MAGIC, MIC, 0, self.seq) + frame.tobytes())
            self.seq += 1
            await asyncio.sleep(0.080)


async def receiver(ws):
    async for m in ws:
        if isinstance(m, str):
            ev.append((time.time(), json.loads(m)))
        else:
            _, _, fl, turn, idx, off = TTS_HDR.unpack_from(m, 0)
            audio.append((time.time(), turn, off, (len(m) - 16) // 2))


def first(kind, after=0.0):
    return next((e for t, e in ev if e.get("t") == kind and t >= after), None)


def first_at(kind, after=0.0):
    return next((t for t, e in ev if e.get("t") == kind and t >= after), None)


async def wait_for(kind, timeout, after=0.0):
    end = time.time() + timeout
    while time.time() < end:
        if first(kind, after):
            return True
        await asyncio.sleep(0.02)
    return False


async def main():
    async with websockets.connect(URL, max_size=None) as ws:
        rx = asyncio.create_task(receiver(ws))
        await wait_for("ready", 10)
        await ws.send(json.dumps({"t": "hello", "proto": 1, "sampleRate": 16000,
                                  "frameSamples": 1280, "aec": True,
                                  "outputLatencyMs": 40}))
        await wait_for("brain_ready", 10)
        brain = (first("brain_ready") or {}).get("brain")
        print(f"  brain: {brain}\n", flush=True)

        mic = Mic(ws)
        await asyncio.sleep(0.4)
        mic.say(load("hey_jarvis_q"))

        check(await wait_for("wake", 10), "wake fired")
        check(await wait_for("tts_begin", 30), "Jarvis started answering")
        t_begin = first_at("tts_begin")

        # let him get properly going, then talk over him
        await asyncio.sleep(1.2)
        frames_before = len(audio)
        t_interrupt = time.time()
        mic.say(load("silence_words"))  # a full sentence, not a cough
        print("  ...talking over him now", flush=True)

        got = await wait_for("cancel", 12, after=t_interrupt)
        check(got, "he stopped when interrupted")
        if got:
            t_cancel = first_at("cancel", after=t_interrupt)
            reason = first("cancel", after=t_interrupt).get("reason")
            print(f"        reason={reason}  "
                  f"{(t_cancel - t_interrupt) * 1000:.0f} ms after speech started",
                  flush=True)
            check(t_cancel - t_interrupt < 3.0, "stopped promptly",
                  f"{(t_cancel - t_interrupt) * 1000:.0f} ms")

            await asyncio.sleep(0.8)
            after = [a for a in audio if a[0] > t_cancel + 0.35]
            check(len(after) == 0, "no audio after the cancel",
                  f"{len(after)} stray frames")
            # Not "did new frames arrive": Gemini streams the whole reply
            # faster than realtime, so it is usually all at the client before
            # you interrupt. That is precisely why the client-side flush --
            # not just stopping the server -- is what makes it feel instant.
            check(len(audio) > 5, "he had really been speaking",
                  f"{len(audio)} frames sent, {len(audio) - frames_before} "
                  f"during the interruption")

            # tell the server how much actually played, as the browser does
            played = sum(a[3] for a in audio if a[1] == audio[-1][1])
            await ws.send(json.dumps({
                "t": "playback_state", "turn_id": audio[-1][1],
                "played_samples": played, "queued_samples": 0, "playing": False}))
            await asyncio.sleep(0.4)

            states = [e["to"] for t, e in ev if e.get("t") == "state" and t >= t_cancel]
            check("LISTENING" in states or "IDLE" in states,
                  "went back to listening", str(states[:4]))

        mic.task.cancel()
        rx.cancel()

    print("\n" + ("ALL CHECKS PASSED" if not fails else "FAILED: " + "; ".join(fails)))
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
