"""Text to speech.

edge-tts is the default: free, keyless, natural, and it has good Hindi
voices. Its catch is a fixed ~1.3 s cost per request (WebSocket handshake to
Microsoft), which is paid per clause. Only the first clause's share of that
is audible -- later clauses are synthesised while earlier audio is still
playing -- but it is why the first word takes about a second to arrive.

Groq's Orpheus would be faster and slots in behind the same interface, but it
needs a one-off terms acceptance on the Groq console.

Kokoro is deliberately absent. Measured on this CPU it runs at RTF 2.0-3.3 --
slower than realtime, so "Sure." takes over two seconds. It is not usable
here without a GPU.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from abc import ABC, abstractmethod

import numpy as np

from ...config import Settings

log = logging.getLogger("jarvis.tts")

OUT_RATE = 24000
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def looks_hindi(text: str) -> bool:
    """Devanagari means Hindi; romanised Hindi is left to the English voice,
    which handles it better than the Hindi voice handles English."""
    return bool(_DEVANAGARI.search(text))


class TtsEngine(ABC):
    name = "tts"
    sample_rate = OUT_RATE

    @abstractmethod
    async def synth(self, text: str) -> np.ndarray:
        """Return int16 mono PCM at `sample_rate`."""

    async def warmup(self) -> None:
        return None

    async def close(self) -> None:
        return None


def decode_mp3(data: bytes, target_rate: int = OUT_RATE) -> np.ndarray:
    """MP3 bytes -> int16 mono PCM, via PyAV so no system ffmpeg is needed."""
    import av

    with av.open(io.BytesIO(data)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=target_rate)
        chunks: list[np.ndarray] = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):  # flush
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.int16)
    return np.concatenate(chunks).astype(np.int16)


class EdgeTts(TtsEngine):
    name = "edge"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def voice_for(self, text: str) -> str:
        return (self.settings.tts_voice_hi if looks_hindi(text)
                else self.settings.tts_voice_en)

    async def synth(self, text: str) -> np.ndarray:
        import edge_tts

        if not text.strip():
            return np.zeros(0, dtype=np.int16)

        mp3 = bytearray()
        comm = edge_tts.Communicate(text, self.voice_for(text))
        async for event in comm.stream():
            if event["type"] == "audio":
                mp3.extend(event["data"])
        if not mp3:
            return np.zeros(0, dtype=np.int16)
        return await asyncio.to_thread(decode_mp3, bytes(mp3), self.sample_rate)

    async def warmup(self) -> None:
        """Open one connection early so the first real clause is not the one
        paying for DNS and TLS as well."""
        try:
            await asyncio.wait_for(self.synth("Hello."), timeout=15)
        except Exception as exc:
            log.warning("edge-tts warmup failed: %s", exc)


class GroqTts(TtsEngine):
    """Orpheus on Groq. Faster than edge-tts, but gated behind a one-off
    terms acceptance at console.groq.com, so construction probes for it."""

    name = "groq"
    URL = "https://api.groq.com/openai/v1/audio/speech"

    def __init__(self, settings: Settings, client) -> None:
        self.settings = settings
        self._client = client
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not set")

    async def synth(self, text: str) -> np.ndarray:
        if not text.strip():
            return np.zeros(0, dtype=np.int16)
        resp = await self._client.post(
            self.URL,
            headers={"Authorization": f"Bearer {self.settings.groq_api_key}"},
            json={
                "model": self.settings.groq_tts_model,
                "input": text,
                "voice": self.settings.groq_tts_voice,
                "response_format": "wav",
            },
            timeout=20.0,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"groq tts {resp.status_code}: {resp.text[:200]}")
        return await asyncio.to_thread(decode_wav, resp.content, self.sample_rate)


def decode_wav(data: bytes, target_rate: int = OUT_RATE) -> np.ndarray:
    import wave

    with wave.open(io.BytesIO(data)) as w:
        rate = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), "<i2")
        if w.getnchannels() == 2:
            pcm = pcm.reshape(-1, 2).mean(axis=1).astype(np.int16)
    if rate != target_rate:  # rare; keep it simple and linear
        idx = np.linspace(0, len(pcm) - 1, int(len(pcm) * target_rate / rate))
        pcm = np.interp(idx, np.arange(len(pcm)), pcm).astype(np.int16)
    return pcm
