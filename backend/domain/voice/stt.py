"""Speech to text.

Groq's whisper-large-v3-turbo is the default: measured at 295 ms for a 3.5 s
clip on the free tier, which is faster than anything that will run locally on
this CPU, and it handles Hindi.

Audio is wrapped in a WAV container in memory with the standard library.
There is no ffmpeg on this machine and nothing here should need one.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx
import numpy as np

from .audio import wav_bytes
from ...config import Settings

log = logging.getLogger("jarvis.stt")


class SttEngine(ABC):
    name = "stt"

    @abstractmethod
    async def transcribe(self, pcm: np.ndarray, sample_rate: int = 16000) -> str:
        ...

    async def close(self) -> None:
        return None


class GroqStt(SttEngine):
    name = "groq"
    URL = "https://api.groq.com/openai/v1/audio/transcriptions"

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self._client = client
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not set")

    async def transcribe(self, pcm: np.ndarray, sample_rate: int = 16000) -> str:
        if pcm.size == 0:
            return ""
        audio = wav_bytes(pcm, sample_rate)
        files = {"file": ("speech.wav", audio, "audio/wav")}
        data = {
            "model": self.settings.groq_stt_model,
            "response_format": "json",
            "temperature": "0",
        }
        resp = await self._client.post(
            self.URL,
            headers={"Authorization": f"Bearer {self.settings.groq_api_key}"},
            files=files,
            data=data,
            timeout=20.0,
        )
        resp.raise_for_status()
        return (resp.json().get("text") or "").strip()


class FasterWhisperStt(SttEngine):
    """Local fallback. Slower, but needs no key and no network.

    Fed numpy directly -- passing a path would make faster-whisper shell out
    to ffmpeg, which is not installed.
    """

    name = "faster_whisper"

    def __init__(self, settings: Settings) -> None:
        from faster_whisper import WhisperModel

        self.settings = settings
        self._model = WhisperModel(
            settings.whisper_model,
            device="cpu",
            compute_type=settings.whisper_compute_type,
            cpu_threads=settings.whisper_cpu_threads,
        )

    async def transcribe(self, pcm: np.ndarray, sample_rate: int = 16000) -> str:
        import asyncio

        def run() -> str:
            audio = pcm.astype(np.float32) / 32768.0
            segments, _info = self._model.transcribe(
                audio,
                beam_size=self.settings.whisper_beam_size,
                vad_filter=False,  # we already endpointed
            )
            return " ".join(seg.text for seg in segments).strip()

        return await asyncio.to_thread(run)
