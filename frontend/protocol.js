// Mirrors backend/protocol.py by hand. Change one, change the other in the
// same commit -- there is no codegen here on purpose.

export const MAGIC = 0xa1;
export const PROTO_VERSION = 1;

export const MSG_MIC_PCM = 0x01;
export const MSG_TTS_PCM = 0x02;

export const MIC_FLAG_VOICED = 1 << 0;
export const MIC_FLAG_PLAYING = 1 << 1;

export const TTS_FLAG_FIRST = 1 << 0;
export const TTS_FLAG_LAST = 1 << 1;

const MIC_HEADER_BYTES = 8;
const TTS_HEADER_BYTES = 16;

// magic:u8, type:u8, flags:u16, seq:u32  +  int16 PCM @ 16 kHz
export function packMic(seq, flags, pcm /* Int16Array */) {
  const out = new ArrayBuffer(MIC_HEADER_BYTES + pcm.byteLength);
  const view = new DataView(out);
  view.setUint8(0, MAGIC);
  view.setUint8(1, MSG_MIC_PCM);
  view.setUint16(2, flags, true);
  view.setUint32(4, seq >>> 0, true);
  new Int16Array(out, MIC_HEADER_BYTES).set(pcm);
  return out;
}

// magic:u8, type:u8, flags:u16, turn:u32, chunk:u32, sampleOffset:u32
export function unpackTts(buffer) {
  const view = new DataView(buffer);
  if (view.getUint8(0) !== MAGIC) {
    throw new Error(`bad magic 0x${view.getUint8(0).toString(16)}`);
  }
  if (view.getUint8(1) !== MSG_TTS_PCM) {
    throw new Error(`unexpected binary type ${view.getUint8(1)}`);
  }
  const flags = view.getUint16(2, true);
  return {
    flags,
    first: (flags & TTS_FLAG_FIRST) !== 0,
    last: (flags & TTS_FLAG_LAST) !== 0,
    turnId: view.getUint32(4, true),
    chunkIdx: view.getUint32(8, true),
    sampleOffset: view.getUint32(12, true),
    // Copy rather than view: the socket buffer gets reused, and we hand this
    // to WebAudio which reads it asynchronously.
    pcm: new Int16Array(buffer.slice(TTS_HEADER_BYTES)),
  };
}
