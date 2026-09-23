"""Wake word detection.

openWakeWord ships a pretrained "hey jarvis" model, which is the whole reason
this project can use that name without training anything.

In practice it responds to the name rather than the exact phrase -- "jarvis",
"ok jarvis", "hi jarvis" and "yo jarvis" all score 0.99+ -- so no custom model
is needed to support those.

Two Windows-specific notes:

* `inference_framework="onnx"` is mandatory. The tflite path has no wheel for
  Windows/Python 3.12 and will not resolve.
* Measured cost on this machine is ~4.6 ms per 80 ms frame -- about 6% of a
  core. Cheap enough to run inline on the event loop.

`gate_on_vad` deliberately does NOT skip inference. An earlier version did,
to save that 6%, and it broke detection badly: the model keeps an internal
audio buffer, so feeding it only the frames where VAD had recently heard
something splices discontinuous audio together and "hey jarvis" arrives
chopped up. Scores collapsed from 0.99 to around 0.5-0.6 and triggers became
intermittent. The model now sees every frame, always; VAD can only veto a
*trigger*, never an inference.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

log = logging.getLogger("jarvis.wake")


def bundled_model_dir() -> Path:
    """Where openWakeWord keeps its pretrained ONNX files."""
    import openwakeword

    return Path(openwakeword.__file__).parent / "resources" / "models"


class WakeWordDetector:
    """Thin wrapper with cooldown and optional VAD gating.

    Stateful (the underlying model keeps an audio buffer), so construct one
    per session rather than sharing a single instance across connections.
    """

    def __init__(
        self,
        model_name: str = "hey_jarvis",
        threshold: float = 0.3,
        cooldown_ms: int = 2000,
        gate_on_vad: bool = True,
        vad_lookback_ms: int = 400,
    ) -> None:
        from openwakeword.model import Model

        self.model_name = model_name
        self.threshold = threshold
        self.cooldown_ms = cooldown_ms
        self.gate_on_vad = gate_on_vad
        self.vad_lookback_ms = vad_lookback_ms

        self._model = Model(wakeword_models=[model_name], inference_framework="onnx")
        self._last_fire = 0.0
        self._last_voice = 0.0
        self.last_score = 0.0
        self.peak_score = 0.0
        self.frames_scored = 0
        self.frames_skipped = 0
        self.vetoed = 0

        log.info("wake word ready: %s (threshold %.2f)", model_name, threshold)

    def note_voice(self, now: float | None = None) -> None:
        """Tell the detector that VAD just saw speech."""
        self._last_voice = now if now is not None else time.monotonic()

    def push(self, pcm: np.ndarray, now: float | None = None) -> float:
        """Score one 1280-sample frame. Returns the wake score in [0, 1].

        Every frame is fed to the model, unconditionally -- see the note at
        the top of this file about why skipping frames breaks detection.
        """
        scores = self._model.predict(pcm)
        self.frames_scored += 1
        self.last_score = float(scores.get(self.model_name, 0.0))
        self.peak_score = max(self.peak_score, self.last_score)
        return self.last_score

    def vad_vetoes(self, now: float | None = None) -> bool:
        """Optional false-positive filter: was there any speech recently?

        Only consulted once a score is already over threshold, so it can
        never stop the model from hearing you -- it can only reject a
        trigger that fired with no voice activity anywhere near it.
        """
        if not self.gate_on_vad:
            return False
        now = now if now is not None else time.monotonic()
        return (now - self._last_voice) * 1000 > self.vad_lookback_ms

    def fired(self, score: float, now: float | None = None) -> bool:
        """Should this score trigger a wake, given cooldown and VAD veto?"""
        if score < self.threshold:
            return False
        now = now if now is not None else time.monotonic()
        if (now - self._last_fire) * 1000 < self.cooldown_ms:
            return False
        if self.vad_vetoes(now):
            self.vetoed += 1
            log.info("wake %.3f vetoed: no voice activity nearby", score)
            return False
        self._last_fire = now
        return True

    def reset(self) -> None:
        """Clear the model's internal audio buffer.

        Called on every state change out of IDLE. Without it, the tail of the
        audio that triggered a wake stays in the buffer and can re-trigger on
        the next frame.
        """
        try:
            self._model.reset()
        except AttributeError:  # older openwakeword builds
            for buf in getattr(self._model, "prediction_buffer", {}).values():
                buf.clear()
        self.last_score = 0.0

    @property
    def stats(self) -> dict:
        return {
            "scored": self.frames_scored,
            "peak": round(self.peak_score, 3),
            "vetoed": self.vetoed,
        }
