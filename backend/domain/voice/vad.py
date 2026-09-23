"""Voice activity detection and endpointing.

Silero VAD runs the actual model; `Endpointer` is deliberately pure logic with
no ONNX in sight, because deciding when someone has finished talking is the
part most worth unit-testing and the part you will retune most often.

The ONNX file comes bundled with openWakeWord, so there is no need for the
`silero-vad` pip package -- which would drag in ~200 MB of PyTorch that
nothing else here uses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

log = logging.getLogger("jarvis.vad")

EndpointEvent = Literal["none", "speech_start", "endpoint", "no_speech_timeout", "max_len"]


class VadGate:
    """Silero VAD over 512-sample (32 ms) frames at 16 kHz.

    Stateful across frames (it is an LSTM), so one instance per session and
    call `reset()` between utterances.
    """

    STATE_SHAPE = (2, 1, 64)

    def __init__(self, model_path: str | Path | None = None, threshold: float = 0.5) -> None:
        import onnxruntime as ort

        if model_path is None:
            from .wake import bundled_model_dir

            model_path = bundled_model_dir() / "silero_vad.onnx"

        opts = ort.SessionOptions()
        # This model is tiny. Threading it adds scheduling jitter and makes it
        # slower, not faster -- measured at ~0.46 ms per frame single-threaded.
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._sess = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._sr = np.array(16000, dtype=np.int64)
        self.threshold = threshold
        self.reset()
        log.info("silero VAD ready (threshold %.2f)", threshold)

    def reset(self) -> None:
        self._h = np.zeros(self.STATE_SHAPE, dtype=np.float32)
        self._c = np.zeros(self.STATE_SHAPE, dtype=np.float32)
        self.last_prob = 0.0

    def push(self, frame: np.ndarray) -> float:
        """Score one 512-sample frame. Accepts int16 or float32."""
        if frame.dtype == np.int16:
            x = (frame.astype(np.float32) / 32768.0).reshape(1, -1)
        else:
            x = frame.astype(np.float32, copy=False).reshape(1, -1)

        out, self._h, self._c = self._sess.run(
            None, {"input": x, "sr": self._sr, "h": self._h, "c": self._c}
        )
        self.last_prob = float(out[0][0])
        return self.last_prob


class NoiseFloor:
    """Slow EMA of the level during non-speech frames.

    Barge-in uses this rather than a fixed dBFS threshold, so the assistant
    stays interruptible in a quiet room without becoming trigger-happy next to
    a fan or an air conditioner.
    """

    def __init__(self, alpha: float = 0.02, initial_dbfs: float = -60.0) -> None:
        self.alpha = alpha
        self.value = initial_dbfs

    def update(self, dbfs: float, voiced: bool) -> float:
        if not voiced and dbfs > -90.0:
            self.value = (1 - self.alpha) * self.value + self.alpha * dbfs
        return self.value


@dataclass
class EndpointConfig:
    vad_threshold: float = 0.5
    min_speech_ms: int = 200
    end_silence_ms: int = 700
    end_silence_short_ms: int = 500
    short_utterance_ms: int = 700
    no_speech_timeout_ms: int = 3000
    max_utterance_ms: int = 15000


class Endpointer:
    """Decides when the user has finished speaking.

    Two silence thresholds rather than one: short replies ("yes", "stop",
    "louder") get 500 ms so the assistant feels snappy, while longer sentences
    get 700 ms so a mid-sentence breath does not cut you off.
    """

    def __init__(self, cfg: EndpointConfig) -> None:
        self.cfg = cfg
        self.reset()

    def reset(self) -> None:
        self.speech_ms = 0.0
        self.silence_ms = 0.0
        self.elapsed_ms = 0.0
        self._announced_start = False

    @property
    def end_silence_needed(self) -> float:
        if self.speech_ms < self.cfg.short_utterance_ms:
            return self.cfg.end_silence_short_ms
        return self.cfg.end_silence_ms

    def update(self, prob: float, frame_ms: float = 32.0) -> EndpointEvent:
        self.elapsed_ms += frame_ms
        voiced = prob >= self.cfg.vad_threshold

        if voiced:
            self.speech_ms += frame_ms
            self.silence_ms = 0.0
        elif self.speech_ms > 0:
            # Only count silence once the user has actually started. Leading
            # silence is handled by the no-speech timeout instead.
            self.silence_ms += frame_ms

        if not self._announced_start and self.speech_ms >= self.cfg.min_speech_ms:
            self._announced_start = True
            return "speech_start"

        if self.elapsed_ms >= self.cfg.max_utterance_ms:
            return "max_len"

        if self.speech_ms >= self.cfg.min_speech_ms:
            if self.silence_ms >= self.end_silence_needed:
                return "endpoint"
        elif self.elapsed_ms >= self.cfg.no_speech_timeout_ms:
            # Wake word fired but nobody said anything. Bail out before
            # spending an STT call on it -- this is the main cost control for
            # wake-word false positives.
            return "no_speech_timeout"

        return "none"


@dataclass
class BargeConfig:
    vad_threshold: float = 0.6  # higher than listening: echo lifts the floor
    consecutive_ms: int = 240
    start_guard_ms: int = 200
    rms_dbfs: float = -42.0
    snr_db: float = 8.0


class BargeDetector:
    """Decides whether the user is really interrupting.

    Four guards, all of which must pass. This is where naive implementations
    fall apart: too eager and the assistant stops every time you cough or say
    "mm-hm"; too strict and you have to shout over it.

    1. Start guard -- ignore the first 200 ms of playback while the browser's
       echo canceller converges.
    2. Sustain -- 240 ms of continuous speech. This is what rejects
       backchannels ("yeah", "right", "mm-hm"), which should not stop a reply.
       It costs 240 ms of interrupt latency, which is the right trade.
    3. Absolute level -- above a floor, so room tone cannot trigger it.
    4. SNR -- clearly above the tracked noise floor, so a fan or an air
       conditioner does not slowly become "speech".

    Pure logic, no model: easy to unit-test and easy to retune by ear.
    """

    def __init__(self, cfg: BargeConfig) -> None:
        self.cfg = cfg
        self.armed_ms = 0.0
        self.voiced_ms = 0.0
        self._hangover = 0

    def arm(self) -> None:
        """Call when playback starts."""
        self.armed_ms = 0.0
        self.voiced_ms = 0.0
        self._hangover = 0

    def update(self, prob: float, dbfs: float, noise_dbfs: float,
               frame_ms: float = 32.0) -> bool:
        self.armed_ms += frame_ms
        if self.armed_ms < self.cfg.start_guard_ms:
            return False

        loud_enough = dbfs > self.cfg.rms_dbfs
        above_noise = dbfs > noise_dbfs + self.cfg.snr_db
        voiced = prob >= self.cfg.vad_threshold

        if voiced and loud_enough and above_noise:
            self.voiced_ms += frame_ms
            self._hangover = 1  # tolerate one dropped frame mid-word
        elif self._hangover > 0:
            self._hangover -= 1
            self.voiced_ms += frame_ms
        else:
            self.voiced_ms = 0.0

        if self.voiced_ms >= self.cfg.consecutive_ms:
            self.voiced_ms = 0.0
            return True
        return False
