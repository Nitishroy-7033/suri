"""Wake word behaviour.

The regression test here is the important one. An earlier version skipped
openWakeWord inference on frames where VAD had not recently heard speech, to
save ~6% of a core. That silently wrecked detection: the model keeps an
internal audio buffer, so skipping frames splices discontinuous audio and the
wake phrase arrives chopped up.

Run: .venv/Scripts/python.exe tests/test_wake.py   (needs the models)
"""

import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = Path(__file__).parent / "fixtures"

from backend.domain.voice.wake import WakeWordDetector  # noqa: E402


def load(name):
    with wave.open(str(FIX / f"{name}.wav")) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), "<i2")
    pad = np.zeros(6400, dtype=np.int16)
    return np.concatenate([pad, pcm, pad])


def best_score(name, skip_every=0):
    """Max score over a clip. skip_every>0 drops every Nth frame."""
    det = WakeWordDetector(threshold=0.3, cooldown_ms=0, gate_on_vad=False)
    pcm = load(name)
    best = 0.0
    for n, i in enumerate(range(0, len(pcm) - 1280, 1280)):
        if skip_every and n % skip_every != 0:
            continue  # simulate the old gate: model never sees this frame
        best = max(best, det.push(pcm[i:i + 1280]))
    return best


def test_all_wake_variants_detected():
    """It responds to the name, not one fixed phrase."""
    for clip in ["hey_jarvis", "hey_jarvis_q"]:
        score = best_score(clip)
        assert score > 0.9, f"{clip} scored only {score:.3f}"


def test_distractors_ignored():
    for clip in ["distractor", "silence_words"]:
        score = best_score(clip)
        assert score < 0.1, f"{clip} falsely scored {score:.3f}"


def test_skipping_frames_destroys_detection():
    """REGRESSION: proves why inference must run on every frame.

    This is the bug that made the wake word feel unreliable in real use.
    """
    whole = best_score("hey_jarvis")
    gated = best_score("hey_jarvis", skip_every=3)  # model sees 1 frame in 3
    assert whole > 0.9, f"baseline broken: {whole:.3f}"
    assert gated < whole * 0.9, (
        f"expected skipping frames to hurt, got whole={whole:.3f} "
        f"gated={gated:.3f}"
    )


def test_cooldown_prevents_repeat_fires():
    det = WakeWordDetector(threshold=0.3, cooldown_ms=2000, gate_on_vad=False)
    pcm = load("hey_jarvis")
    fires = sum(1 for i in range(0, len(pcm) - 1280, 1280)
                if det.fired(det.push(pcm[i:i + 1280])))
    assert fires == 1, f"expected exactly one fire, got {fires}"


def test_vad_veto_never_blocks_inference():
    """With the veto on, the model must still see every frame."""
    det = WakeWordDetector(threshold=0.3, cooldown_ms=0, gate_on_vad=True)
    pcm = load("hey_jarvis")
    frames = 0
    best = 0.0
    for i in range(0, len(pcm) - 1280, 1280):
        best = max(best, det.push(pcm[i:i + 1280]))
        frames += 1
    assert det.frames_scored == frames, "some frames were not scored"
    assert best > 0.9, f"score collapsed to {best:.3f} with veto enabled"


def test_vad_veto_rejects_trigger_without_voice():
    det = WakeWordDetector(threshold=0.3, cooldown_ms=0, gate_on_vad=True,
                           vad_lookback_ms=400)
    assert det.vad_vetoes(), "should veto when VAD has seen nothing"
    det.note_voice()
    assert not det.vad_vetoes(), "should allow right after speech"


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception as exc:
                print(f"  FAIL  {name}: {exc}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
