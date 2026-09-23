// Accumulates the 128-sample render quantum into 1280-sample (80 ms) frames
// and converts to int16 on the audio thread.
//
// 1280 is not arbitrary: it is openWakeWord's native frame size AND exactly
// ten render quanta, so this accumulator never carries a partial quantum.

const FRAME_SAMPLES = 1280;

class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = new Int16Array(FRAME_SAMPLES);
    this._filled = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channel = input[0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      // Clamp before scaling: a sample of exactly 1.0 would wrap to -32768.
      const s = Math.max(-1, Math.min(1, channel[i]));
      this._buf[this._filled++] = s < 0 ? s * 0x8000 : s * 0x7fff;

      if (this._filled === FRAME_SAMPLES) {
        const frame = this._buf;
        // Transfer ownership: zero-copy across the thread boundary.
        this.port.postMessage(frame, [frame.buffer]);
        this._buf = new Int16Array(FRAME_SAMPLES);
        this._filled = 0;
      }
    }
    return true;
  }
}

registerProcessor("capture-processor", CaptureProcessor);
