// Microphone capture at 16 kHz with the browser's echo canceller on.
//
// echoCancellation is the whole reason this project runs in a browser rather
// than as a desktop Python app. Without it the mic hears Jarvis through the
// laptop speakers and he interrupts himself on every reply; with it, barge-in
// works on open speakers with no headphones.
//
// AEC lives in the device capture pipeline upstream of WebAudio, so asking
// for a 16 kHz AudioContext does not disable it.

export class MicCapture {
  constructor({ sampleRate = 16000, onFrame = null } = {}) {
    this.sampleRate = sampleRate;
    this.onFrame = onFrame;
    this.ctx = null;
    this.stream = null;
    this.node = null;
    this.seq = 0;
    this.actualSampleRate = null;
    this.aecActive = null;
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });

    const track = this.stream.getAudioTracks()[0];
    const settings = track.getSettings?.() ?? {};
    this.aecActive = settings.echoCancellation ?? null;

    this.ctx = new AudioContext({ sampleRate: this.sampleRate });
    // Chrome honours the requested rate; if a browser refuses, everything
    // downstream (wake word, VAD, Whisper) would be silently wrong, so the
    // caller checks this and the backend warns too.
    this.actualSampleRate = this.ctx.sampleRate;

    await this.ctx.audioWorklet.addModule("./worklets/capture-processor.js");

    const source = this.ctx.createMediaStreamSource(this.stream);
    // Read-only tap for the visualiser. An AnalyserNode with nothing
    // connected downstream still runs, so this never reaches the speakers --
    // which it must not, or AEC would be fighting our own monitor path.
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.7;
    this._spectrum = new Uint8Array(this.analyser.frequencyBinCount);
    source.connect(this.analyser);
    this.node = new AudioWorkletNode(this.ctx, "capture-processor", {
      numberOfInputs: 1,
      numberOfOutputs: 0,
      channelCount: 1,
    });

    this.node.port.onmessage = (ev) => {
      if (this.onFrame) this.onFrame(ev.data, this.seq);
      this.seq = (this.seq + 1) >>> 0;
    };

    source.connect(this.node);
    return {
      sampleRate: this.actualSampleRate,
      aec: this.aecActive,
      label: track.label,
    };
  }

  async stop() {
    if (this.node) this.node.port.onmessage = null;
    if (this.stream) for (const t of this.stream.getTracks()) t.stop();
    if (this.ctx) await this.ctx.close();
    this.ctx = null;
    this.stream = null;
    this.node = null;
    this.analyser = null;
  }

  // Spectrum for the visualiser, folded down to `n` bands. Only the lower
  // half of the FFT is used: above ~half Nyquist there is nothing but hiss,
  // and including it leaves the outer bars permanently flat.
  bands(n = 8) {
    if (!this.analyser) return null;
    this.analyser.getByteFrequencyData(this._spectrum);
    const usable = Math.floor(this._spectrum.length * 0.55);
    const per = Math.max(1, Math.floor(usable / n));
    const out = new Float32Array(n);
    for (let b = 0; b < n; b++) {
      let sum = 0;
      for (let k = 0; k < per; k++) sum += this._spectrum[b * per + k];
      out[b] = Math.min(1, sum / per / 190);
    }
    return out;
  }

  get active() {
    return this.ctx !== null && this.ctx.state === "running";
  }
}
