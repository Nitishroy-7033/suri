"""Barge-in guards.

Too eager and Jarvis stops every time you cough; too strict and you have to
shout over him. All four guards are pure logic, so they can be tuned here
rather than by repeatedly talking at a laptop.

Run: .venv/Scripts/python.exe tests/test_barge.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.domain.voice.vad import BargeConfig, BargeDetector

FRAME = 32.0
CFG = BargeConfig(vad_threshold=0.6, consecutive_ms=240, start_guard_ms=200,
                  rms_dbfs=-42.0, snr_db=8.0)
NOISE = -55.0


def feed(d, ms, prob, dbfs, noise=NOISE):
    hits = 0
    for _ in range(int(ms / FRAME)):
        if d.update(prob, dbfs, noise, FRAME):
            hits += 1
    return hits


def armed():
    d = BargeDetector(CFG)
    d.arm()
    feed(d, 224, 0.0, -80)  # let the start guard expire in silence
    return d


def test_start_guard_blocks_early_audio():
    """The browser's echo canceller needs a moment to converge."""
    d = BargeDetector(CFG)
    d.arm()
    assert feed(d, 192, 0.9, -20) == 0


def test_sustained_speech_triggers():
    d = armed()
    assert feed(d, 320, 0.9, -20) >= 1


def test_backchannel_does_not_trigger():
    """"mm-hm" and "yeah" should not stop a reply."""
    d = armed()
    assert feed(d, 160, 0.9, -20) == 0


def test_quiet_speech_below_floor_ignored():
    d = armed()
    assert feed(d, 500, 0.9, -50) == 0, "below the absolute level floor"


def test_loud_room_noise_ignored_via_snr():
    """A fan at -30 dB should not read as speech once the floor tracks it."""
    d = armed()
    assert feed(d, 500, 0.9, -26, noise=-30.0) == 0


def test_speech_above_a_loud_floor_still_triggers():
    d = armed()
    assert feed(d, 400, 0.9, -15, noise=-30.0) >= 1


def test_one_dropped_frame_tolerated():
    """Real speech dips below threshold between syllables."""
    d = armed()
    hits = 0
    for _ in range(4):
        hits += feed(d, 64, 0.9, -20)
        hits += feed(d, 32, 0.1, -20)  # single unvoiced frame
    assert hits >= 1, "hangover should bridge a one-frame dip"


def test_long_gap_resets_progress():
    d = armed()
    feed(d, 160, 0.9, -20)
    feed(d, 200, 0.05, -80)      # clear gap
    assert feed(d, 160, 0.9, -20) == 0, "progress should have reset"


def test_arm_resets_between_replies():
    d = armed()
    feed(d, 160, 0.9, -20)
    d.arm()
    assert feed(d, 192, 0.9, -20) == 0, "start guard applies again"


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
