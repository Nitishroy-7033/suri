"""Sample-level plumbing: ring buffer, re-chunking, format conversion.

Deliberately dependency-light -- numpy and the standard library only. There is
no resampler in here and there should never be one: the browser opens its
capture context at 16 kHz and its playback context at 24 kHz, so Chrome's
native resampler does that work for free and Python never pays for it.
"""

from __future__ import annotations

import io
import wave
from collections.abc import Iterator

import numpy as np


def i16_to_f32(pcm: np.ndarray) -> np.ndarray:
    """int16 PCM -> float32 in [-1, 1). What Silero and Whisper want."""
    return pcm.astype(np.float32) / 32768.0


def f32_to_i16(pcm: np.ndarray) -> np.ndarray:
    """float32 -> int16, clipped. What goes on the wire."""
    return np.clip(pcm * 32768.0, -32768, 32767).astype(np.int16)


def rms_dbfs(pcm: np.ndarray) -> float:
    """Frame level in dBFS. Returns -inf for digital silence."""
    if pcm.size == 0:
        return float("-inf")
    x = i16_to_f32(pcm) if pcm.dtype == np.int16 else pcm
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return 20.0 * np.log10(rms) if rms > 1e-12 else float("-inf")


def wav_bytes(pcm: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Wrap int16 PCM in a WAV container, in memory.

    Used to hand audio to Groq's transcription endpoint. Written with the
    stdlib on purpose -- this machine has no ffmpeg, and nothing in this
    project should require one.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()


class RingBuffer:
    """Fixed-capacity circular buffer of int16 samples, indexed absolutely.

    Runs in every state, always. It is what makes pre-roll possible: the wake
    word only fires at the *end* of "hey jarvis", and people run straight on
    into "...what's the weather", so by the time we decide to listen we already
    needed the last half second. Same trick covers the first syllable of a
    barge-in.
    """

    def __init__(self, capacity_samples: int) -> None:
        self._buf = np.zeros(capacity_samples, dtype=np.int16)
        self._cap = capacity_samples
        self.total_written = 0  # absolute sample counter, never wraps

    def write(self, pcm: np.ndarray) -> None:
        n = pcm.size
        if n == 0:
            return

        if n >= self._cap:
            # A single write bigger than the ring: only the tail can survive.
            # Account for the dropped head in total_written first so that the
            # write cursor stays congruent -- writing straight into _buf[:]
            # here would leave the cursor pointing at the wrong offset and
            # tail() would hand back correctly-valued samples in the wrong
            # rotation.
            self.total_written += n - self._cap
            pcm = pcm[-self._cap :]
            n = self._cap

        start = self.total_written % self._cap
        end = start + n
        if end <= self._cap:
            self._buf[start:end] = pcm
        else:
            split = self._cap - start
            self._buf[start:] = pcm[:split]
            self._buf[: end - self._cap] = pcm[split:]
        self.total_written += n

    def tail(self, n_samples: int) -> np.ndarray:
        """The most recent n samples, oldest first."""
        n = min(n_samples, self._cap, self.total_written)
        if n == 0:
            return np.zeros(0, dtype=np.int16)

        end = self.total_written % self._cap
        start = end - n
        if start >= 0:
            return self._buf[start:end].copy()
        return np.concatenate((self._buf[start:], self._buf[:end]))

    def tail_ms(self, ms: float, sample_rate: int = 16000) -> np.ndarray:
        return self.tail(int(ms * sample_rate / 1000))


class Chunker:
    """Re-chunk a stream into fixed-size frames, carrying the remainder.

    The wire frame is 1280 samples (80 ms) because that is openWakeWord's
    native frame and exactly ten AudioWorklet render quanta. Silero wants 512,
    which does not tile into 1280 -- you get 2, 3, 2, 3, ... frames per wire
    frame. This class is where that mismatch is absorbed, and it is the only
    place it should be.
    """

    def __init__(self, frame_samples: int) -> None:
        self.frame_samples = frame_samples
        self._pending = np.zeros(0, dtype=np.int16)

    def push(self, pcm: np.ndarray) -> Iterator[np.ndarray]:
        buf = np.concatenate((self._pending, pcm)) if self._pending.size else pcm
        n = self.frame_samples
        count = buf.size // n
        for i in range(count):
            yield buf[i * n : (i + 1) * n]
        self._pending = buf[count * n :].copy()

    def reset(self) -> None:
        self._pending = np.zeros(0, dtype=np.int16)
