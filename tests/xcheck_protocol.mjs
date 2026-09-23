import { writeFileSync, readFileSync } from "node:fs";
import { packMic, unpackTts } from "file:///E:/voice-ai/frontend/protocol.js";

const dir = process.argv[2];

// JS -> Python: a mic frame with a known ramp
const pcm = new Int16Array(1280);
for (let i = 0; i < 1280; i++) pcm[i] = ((i * 37) % 65536) - 32768;
writeFileSync(`${dir}/mic_from_js.bin`, Buffer.from(packMic(4242, 3, pcm)));

// Python -> JS: read the tts frame Python produced
const buf = readFileSync(`${dir}/tts_from_py.bin`);
const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
const f = unpackTts(ab);
const sum = f.pcm.reduce((a, b) => a + b, 0);
console.log(JSON.stringify({
  turnId: f.turnId, chunkIdx: f.chunkIdx, sampleOffset: f.sampleOffset,
  first: f.first, last: f.last, n: f.pcm.length, sum,
  head: Array.from(f.pcm.slice(0, 4)),
}));
