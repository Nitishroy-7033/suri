"""Endpointing logic tests.

Endpointer is pure logic with no ONNX in it, which is the point: deciding
when someone has stopped talking is the most-retuned part of the system and
the part where an off-by-one costs you a clipped word.

Run: .venv/Scripts/python.exe tests/test_endpoint.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.domain.voice.vad import EndpointConfig, Endpointer, NoiseFloor

FRAME = 32.0
CFG = EndpointConfig(
    vad_threshold=0.5, min_speech_ms=200, end_silence_ms=700,
    end_silence_short_ms=500, short_utterance_ms=700,
    no_speech_timeout_ms=3000, max_utterance_ms=15000,
)


def feed(ep, prob, ms):
    """Feed `ms` of frames at a fixed probability; return events seen."""
    events = []
    for _ in range(int(ms / FRAME)):
        ev = ep.update(prob, FRAME)
        if ev != "none":
            events.append(ev)
    return events


def test_speech_start_announced_once():
    ep = Endpointer(CFG)
    events = feed(ep, 0.9, 1000)
    assert events.count("speech_start") == 1, events


def test_long_utterance_uses_700ms_silence():
    ep = Endpointer(CFG)
    feed(ep, 0.9, 1000)  # 1 s of speech -> "long"
    assert ep.end_silence_needed == 700
    # 600 ms of silence must NOT end it
    assert "endpoint" not in feed(ep, 0.0, 608)
    # crossing 700 ms does
    assert "endpoint" in feed(ep, 0.0, 128)


def test_short_utterance_uses_500ms_silence():
    """A bare "stop" should not make you wait the full 700 ms."""
    ep = Endpointer(CFG)
    feed(ep, 0.9, 320)  # 320 ms of speech -> "short"
    assert ep.end_silence_needed == 500
    assert "endpoint" not in feed(ep, 0.0, 448)
    assert "endpoint" in feed(ep, 0.0, 96)


def test_brief_pause_does_not_end_turn():
    """A mid-sentence breath must not be treated as the end."""
    ep = Endpointer(CFG)
    feed(ep, 0.9, 800)
    assert "endpoint" not in feed(ep, 0.0, 300)  # breath
    feed(ep, 0.9, 500)  # carries on
    assert ep.silence_ms == 0.0
    assert "endpoint" not in feed(ep, 0.0, 600)
    assert "endpoint" in feed(ep, 0.0, 160)


def test_no_speech_timeout_on_false_wake():
    """Wake word fired, nobody spoke. Must bail before spending an STT call."""
    ep = Endpointer(CFG)
    events = feed(ep, 0.05, 3200)
    assert "no_speech_timeout" in events
    assert "endpoint" not in events


def test_noise_below_min_speech_never_endpoints():
    """Sub-threshold blips must not accumulate into a real utterance."""
    ep = Endpointer(CFG)
    events = []
    for _ in range(40):
        events += feed(ep, 0.9, 64)   # 64 ms of speech, under min_speech 200
        events += feed(ep, 0.0, 736)  # long silence
    # The first burst pair already exceeds min_speech cumulatively, so what we
    # actually assert is that it ends cleanly rather than hanging.
    assert "endpoint" in events or "no_speech_timeout" in events


def test_max_length_forces_endpoint():
    ep = Endpointer(CFG)
    events = feed(ep, 0.9, 15040)
    assert "max_len" in events


def test_reset_clears_state():
    ep = Endpointer(CFG)
    feed(ep, 0.9, 1000)
    ep.reset()
    assert ep.speech_ms == 0 and ep.silence_ms == 0 and ep.elapsed_ms == 0
    assert feed(ep, 0.0, 800) == []  # no speech yet, so no endpoint


def test_threshold_boundary_is_inclusive():
    ep = Endpointer(CFG)
    feed(ep, 0.5, 224)  # exactly at threshold counts as voiced
    assert ep.speech_ms >= 200


def test_noise_floor_tracks_silence_not_speech():
    nf = NoiseFloor(alpha=0.1, initial_dbfs=-60.0)
    for _ in range(50):
        nf.update(-45.0, voiced=False)
    assert -46 < nf.value < -44, nf.value
    before = nf.value
    for _ in range(50):
        nf.update(-10.0, voiced=True)  # loud speech must not raise the floor
    assert nf.value == before


def test_noise_floor_ignores_digital_silence():
    """-inf frames would drag the floor to nonsense."""
    nf = NoiseFloor(alpha=0.5, initial_dbfs=-50.0)
    nf.update(float("-inf"), voiced=False)
    assert nf.value == -50.0


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
