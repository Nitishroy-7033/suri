"""Unit tests for the sample-level plumbing.

Run: .venv/Scripts/python.exe -m pytest tests -q
(or plain `python tests/test_audio.py` -- there is a __main__ runner below)
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.domain.voice.audio import Chunker, RingBuffer, f32_to_i16, i16_to_f32, rms_dbfs, wav_bytes
from backend.domain.voice.protocol import MAGIC, ProtocolError, pack_tts, unpack_mic
from backend.domain.voice.protocol import MIC_HEADER, MsgType


def test_ring_tail_basic():
    r = RingBuffer(10)
    r.write(np.arange(4, dtype=np.int16))
    assert r.tail(4).tolist() == [0, 1, 2, 3]
    assert r.tail(100).tolist() == [0, 1, 2, 3]  # clamped to what exists


def test_ring_wraps_and_keeps_order():
    r = RingBuffer(10)
    r.write(np.arange(8, dtype=np.int16))
    r.write(np.arange(8, 16, dtype=np.int16))  # forces a wrap
    assert r.total_written == 16
    assert r.tail(10).tolist() == list(range(6, 16))
    assert r.tail(3).tolist() == [13, 14, 15]


def test_ring_oversized_write_keeps_tail():
    r = RingBuffer(5)
    r.write(np.arange(12, dtype=np.int16))
    assert r.tail(5).tolist() == [7, 8, 9, 10, 11]


def test_chunker_512_out_of_1280():
    """The 1280 -> 512 mismatch must produce 2,3,2,3... with nothing lost."""
    c = Chunker(512)
    counts = []
    total = 0
    for _ in range(8):
        frames = list(c.push(np.ones(1280, dtype=np.int16)))
        counts.append(len(frames))
        total += sum(f.size for f in frames)
        assert all(f.size == 512 for f in frames)

    assert counts == [2, 3, 2, 3, 2, 3, 2, 3]
    # 8 * 1280 = 10240 samples in; 20 * 512 = 10240 out, zero loss.
    assert total == 8 * 1280


def test_chunker_preserves_sample_order():
    c = Chunker(4)
    out = []
    for frame in c.push(np.arange(10, dtype=np.int16)):
        out.extend(frame.tolist())
    for frame in c.push(np.arange(10, 20, dtype=np.int16)):
        out.extend(frame.tolist())
    assert out == list(range(20))  # 20 is divisible by 4, so nothing pending


def test_conversions_roundtrip():
    pcm = np.array([-32768, -1, 0, 1, 32767], dtype=np.int16)
    back = f32_to_i16(i16_to_f32(pcm))
    assert np.abs(back.astype(int) - pcm.astype(int)).max() <= 1


def test_f32_to_i16_clips_instead_of_wrapping():
    loud = np.array([-4.0, 4.0], dtype=np.float32)
    out = f32_to_i16(loud)
    assert out.tolist() == [-32768, 32767]


def test_rms_dbfs_levels():
    assert rms_dbfs(np.zeros(100, dtype=np.int16)) == float("-inf")
    full = np.full(1000, 32767, dtype=np.int16)
    assert -0.1 < rms_dbfs(full) < 0.1  # full-scale DC ~ 0 dBFS
    half = np.full(1000, 3277, dtype=np.int16)
    assert -21 < rms_dbfs(half) < -19  # ~ -20 dBFS


def test_wav_bytes_is_a_real_wav():
    import io
    import wave

    pcm = np.arange(-1000, 1000, dtype=np.int16)
    data = wav_bytes(pcm, 16000)
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert np.frombuffer(w.readframes(w.getnframes()), "<i2").tolist() == pcm.tolist()


def _mic_frame(seq, pcm, flags=0):
    return MIC_HEADER.pack(MAGIC, MsgType.MIC_PCM, flags, seq) + pcm.tobytes()


def test_unpack_mic_roundtrip():
    pcm = np.arange(1280, dtype=np.int16)
    seq, flags, out = unpack_mic(_mic_frame(7, pcm, flags=3))
    assert (seq, flags) == (7, 3)
    assert out.tolist() == pcm.tolist()


def test_unpack_mic_rejects_bad_magic():
    pcm = np.zeros(4, dtype=np.int16)
    bad = bytes([0x00]) + _mic_frame(1, pcm)[1:]
    try:
        unpack_mic(bad)
    except ProtocolError as exc:
        assert "magic" in str(exc)
    else:
        raise AssertionError("expected ProtocolError")


def test_pack_tts_header_and_payload():
    pcm = np.arange(100, dtype=np.int16)
    frame = pack_tts(3, 2, 5760, pcm, first=True, last=False)
    assert len(frame) == 16 + 200
    assert frame[0] == MAGIC
    assert frame[1] == MsgType.TTS_PCM
    assert int.from_bytes(frame[4:8], "little") == 3      # turn_id
    assert int.from_bytes(frame[8:12], "little") == 2     # chunk_idx
    assert int.from_bytes(frame[12:16], "little") == 5760  # sample_offset
    assert np.frombuffer(frame[16:], "<i2").tolist() == pcm.tolist()


def test_pack_tts_rejects_wrong_dtype():
    try:
        pack_tts(0, 0, 0, np.zeros(10, dtype=np.float32))
    except ProtocolError:
        pass
    else:
        raise AssertionError("expected ProtocolError for float32 pcm")


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception as exc:  # noqa: BLE001 - test runner
                print(f"  FAIL  {name}: {exc}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
