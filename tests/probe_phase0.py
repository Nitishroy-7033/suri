"""Drives the backend exactly like the browser will, so Phase 0 can be
verified without clicking anything."""
import asyncio, json, os, struct, sys, time
import numpy as np
import websockets

MAGIC, MIC, TTS = 0xA1, 0x01, 0x02
MIC_HDR = struct.Struct("<BBHI")
TTS_HDR = struct.Struct("<BBHIII")
URL = os.environ.get("JARVIS_PROBE_URL", "ws://127.0.0.1:8080/ws")
fails = []

def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        fails.append(label)

def mic_frame(seq, pcm):
    return MIC_HDR.pack(MAGIC, MIC, 0, seq) + pcm.tobytes()

async def main():
    async with websockets.connect(URL, max_size=None) as ws:
        ready = json.loads(await asyncio.wait_for(ws.recv(), 5))
        check(ready.get("t") == "ready", "handshake: ready received")
        check(ready.get("micSampleRate") == 16000, "mic rate 16000")
        check(ready.get("ttsSampleRate") == 24000, "tts rate 24000")
        check(ready.get("frameSamples") == 1280, "wire frame 1280")
        check("vad_threshold" in (ready.get("settings") or {}), "tunables exposed")

        await ws.send(json.dumps({"t": "hello", "proto": 1, "sampleRate": 16000,
                                  "frameSamples": 1280, "aec": True,
                                  "outputLatencyMs": 40}))

        levels = []
        for seq in range(12):
            t = np.arange(seq * 1280, (seq + 1) * 1280) / 16000.0
            pcm = (0.1 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
            await ws.send(mic_frame(seq, pcm))
            await asyncio.sleep(0.005)
        deadline = time.perf_counter() + 2
        while time.perf_counter() < deadline and len(levels) < 5:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 0.4))
            except asyncio.TimeoutError:
                break
            if m.get("t") == "level":
                levels.append(m)
        check(len(levels) >= 5, "level reports arriving", f"{len(levels)} reports")
        if levels:
            last = levels[-1]
            check(last["dropped"] == 0, "no dropped frames", f"recv={last['frames']}")
            # amplitude 0.1 sine -> RMS 0.0707 -> -23.0 dBFS
            check(-24.0 < last["dbfs"] < -22.0, "level ~ -23 dBFS (RMS of 0.1 sine)",
                  f"{last['dbfs']} dB")

        await ws.send(json.dumps({"t": "tone_test", "seconds": 1.0}))
        begin = end = None
        offsets, sizes, firsts, lasts = [], [], [], []
        deadline = time.perf_counter() + 8
        while time.perf_counter() < deadline:
            msg = await asyncio.wait_for(ws.recv(), 3)
            if isinstance(msg, bytes):
                magic, mt, flags, turn, idx, off = TTS_HDR.unpack_from(msg, 0)
                check_magic = magic == MAGIC and mt == TTS
                if not check_magic:
                    fails.append("tts framing magic")
                offsets.append(off)
                sizes.append((len(msg) - 16) // 2)
                firsts.append(flags & 1)
                lasts.append((flags >> 1) & 1)
            else:
                m = json.loads(msg)
                if m.get("t") == "tts_begin":
                    begin = m
                elif m.get("t") == "tts_end":
                    end = m
                    break
        check(begin is not None, "tts_begin received")
        check(end is not None, "tts_end received")
        check(len(offsets) == 5, "1 s tone -> 5 chunks of 240 ms", f"{len(offsets)}")
        check(sizes[:4] == [5760] * 4, "chunks are 5760 samples", str(sizes))
        contiguous = all(offsets[i] + sizes[i] == offsets[i + 1]
                         for i in range(len(offsets) - 1))
        check(contiguous, "sample offsets contiguous (gapless)", str(offsets))
        check(sum(sizes) == 24000, "total = 1 s @ 24 kHz", str(sum(sizes)))
        check(firsts[0] == 1 and sum(firsts) == 1, "exactly one FIRST flag")
        check(lasts[-1] == 1 and sum(lasts) == 1, "exactly one LAST flag")
        check(bool(end) and end["total_samples"] == 24000, "tts_end total matches")

        await ws.send(json.dumps({"t": "tone_test", "seconds": 5.0}))
        got = 0
        while got < 3:
            msg = await asyncio.wait_for(ws.recv(), 3)
            if isinstance(msg, bytes):
                got += 1
        t_cancel = time.perf_counter()
        await ws.send(json.dumps({"t": "stop_audio"}))
        cancel_msg, extra, latency_ms = None, 0, 0.0
        while time.perf_counter() - t_cancel < 1.5:
            try:
                msg = await asyncio.wait_for(ws.recv(), 0.5)
            except asyncio.TimeoutError:
                break
            if isinstance(msg, bytes):
                if cancel_msg:
                    extra += 1
            else:
                m = json.loads(msg)
                if m.get("t") == "cancel":
                    cancel_msg = m
                    latency_ms = (time.perf_counter() - t_cancel) * 1000
        check(cancel_msg is not None, "cancel acknowledged", f"{latency_ms:.0f} ms")
        check(extra == 0, "no audio after cancel", f"{extra} stray frames")

    print("\n" + ("ALL CHECKS PASSED" if not fails else "FAILED: " + ", ".join(fails)))
    return 1 if fails else 0

sys.exit(asyncio.run(main()))
