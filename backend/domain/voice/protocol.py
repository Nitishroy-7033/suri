"""Binary + JSON wire protocol.

Mirrored by hand in frontend/protocol.js. If you change a struct here, change
it there in the same commit -- there is no codegen and no schema negotiation
beyond the magic byte.

Binary framing
--------------
Mic  (client -> server):  8 byte header + 2560 bytes = int16[1280] @ 16 kHz
TTS  (server -> client): 16 byte header + N bytes    = int16[N/2] @ 24 kHz

All integers little-endian. Byte 0 is a magic value so that a stale browser
tab reconnecting after a protocol change fails loudly instead of feeding us
garbage PCM.
"""

from __future__ import annotations

import struct
from enum import IntEnum

import numpy as np

MAGIC = 0xA1
PROTO_VERSION = 1


class MsgType(IntEnum):
    MIC_PCM = 0x01
    TTS_PCM = 0x02


# magic:u8, type:u8, flags:u16, seq:u32
MIC_HEADER = struct.Struct("<BBHI")
# magic:u8, type:u8, flags:u16, turn_id:u32, chunk_idx:u32, sample_offset:u32
TTS_HEADER = struct.Struct("<BBHIII")

MIC_FLAG_VOICED = 1 << 0
MIC_FLAG_PLAYING = 1 << 1

TTS_FLAG_FIRST = 1 << 0
TTS_FLAG_LAST = 1 << 1


class ProtocolError(ValueError):
    """Malformed frame. Always fatal for the connection -- never recover."""


def unpack_mic(data: bytes) -> tuple[int, int, np.ndarray]:
    """Return (seq, flags, int16 PCM view) from a raw mic frame."""
    if len(data) < MIC_HEADER.size:
        raise ProtocolError(f"mic frame too short: {len(data)} bytes")

    magic, mtype, flags, seq = MIC_HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise ProtocolError(f"bad magic 0x{magic:02X} (stale client?)")
    if mtype != MsgType.MIC_PCM:
        raise ProtocolError(f"unexpected binary type 0x{mtype:02X} from client")

    payload = data[MIC_HEADER.size :]
    if len(payload) % 2:
        raise ProtocolError("mic payload is not an even number of bytes")

    return seq, flags, np.frombuffer(payload, dtype="<i2")


def pack_tts(
    turn_id: int,
    chunk_idx: int,
    sample_offset: int,
    pcm: np.ndarray,
    *,
    first: bool = False,
    last: bool = False,
) -> bytes:
    """Build an audio frame for the client.

    `sample_offset` is the cumulative sample index of pcm[0] within this turn.
    It is what lets the backend work out exactly how much of a reply the user
    actually heard when they interrupt.
    """
    if pcm.dtype != np.int16:
        raise ProtocolError(f"tts pcm must be int16, got {pcm.dtype}")

    flags = (TTS_FLAG_FIRST if first else 0) | (TTS_FLAG_LAST if last else 0)
    header = TTS_HEADER.pack(
        MAGIC, MsgType.TTS_PCM, flags, turn_id, chunk_idx, sample_offset
    )
    return header + pcm.tobytes()
