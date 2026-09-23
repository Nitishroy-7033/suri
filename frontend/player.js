// Gapless scheduled playback with an instant, click-free flush.
//
// Two details here are load-bearing and easy to get wrong:
//
//  1. Scheduling uses a running cursor (`_nextStart`), not
//     `source.start()` at "now". Starting each chunk at the current time
//     leaves a sub-millisecond gap per chunk, which reads as a faint tick
//     every 240 ms.
//
//  2. Flush ramps the gain to zero over 15 ms instead of calling stop()
//     outright. Cutting a waveform mid-cycle produces an audible click that
//     users read as a bug -- and barge-in triggers this on every single
//     interruption, so it would be constant.

const RAMP_SECONDS = 0.015;

export class PcmPlayer {
  constructor({ sampleRate = 24000, prebufferMs = 150, onReport = null } = {}) {
    this.sampleRate = sampleRate;
    this.prebuffer = prebufferMs / 1000;
    this.onReport = onReport;

    this.ctx = new AudioContext({ sampleRate, latencyHint: "interactive" });
    this.gain = this.ctx.createGain();
    this.gain.gain.value = 1;
    // Tap for the grid visualiser. It sits after the gain node so a barge-in
    // fade shows up in the picture as well as in the speakers.
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.72;
    this._spectrum = new Uint8Array(this.analyser.frequencyBinCount);
    this.gain.connect(this.analyser).connect(this.ctx.destination);

    this._sources = new Set();
    this._cancelled = new Set();
    this._turnId = -1;
    this._nextStart = 0;
    this._turnStart = 0;
    this._enqueued = 0; // samples handed to WebAudio for the current turn
    this._underruns = 0;
    this._reportTimer = null;
  }

  async resume() {
    if (this.ctx.state !== "running") await this.ctx.resume();
  }

  get outputLatency() {
    // ctx.currentTime runs ahead of what is actually audible. On Windows
    // WASAPI this is typically 20-80 ms. Ignoring it makes playedSamples
    // over-report, which later makes the backend believe Jarvis said one or
    // two more words than the user heard.
    return this.ctx.outputLatency || this.ctx.baseLatency || 0.02;
  }

  get playedSamples() {
    if (this._turnStart === 0) return 0;
    const elapsed = this.ctx.currentTime - this.outputLatency - this._turnStart;
    const played = Math.round(elapsed * this.sampleRate);
    return Math.max(0, Math.min(played, this._enqueued));
  }

  get queuedSamples() {
    return Math.max(0, this._enqueued - this.playedSamples);
  }

  get playing() {
    return this._sources.size > 0 && this.queuedSamples > 0;
  }

  beginTurn(turnId) {
    this._turnId = turnId;
    this._cancelled.delete(turnId);
    this._nextStart = 0;
    this._turnStart = 0;
    this._enqueued = 0;
    this.gain.gain.cancelScheduledValues(this.ctx.currentTime);
    this.gain.gain.setValueAtTime(1, this.ctx.currentTime);
  }

  enqueue({ turnId, pcm }) {
    if (this._cancelled.has(turnId)) return; // late chunk from a killed turn
    if (turnId !== this._turnId) this.beginTurn(turnId);

    const buf = this.ctx.createBuffer(1, pcm.length, this.sampleRate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;

    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.gain);

    const now = this.ctx.currentTime;
    let start = this._nextStart;
    if (start < now) {
      // We ran dry. Re-prebuffer rather than scheduling in the past, which
      // WebAudio would silently collapse to "now" and stack chunks.
      if (this._nextStart !== 0) this._underruns++;
      start = now + this.prebuffer;
    }
    if (this._turnStart === 0) this._turnStart = start;

    src.start(start);
    this._nextStart = start + buf.duration;
    this._enqueued += pcm.length;

    this._sources.add(src);
    src.onended = () => {
      this._sources.delete(src);
      if (this._sources.size === 0) this.report();
    };
  }

  flush(turnId) {
    const t = this.ctx.currentTime;
    const played = this.playedSamples;

    this.gain.gain.cancelScheduledValues(t);
    this.gain.gain.setValueAtTime(this.gain.gain.value, t);
    this.gain.gain.linearRampToValueAtTime(0.0001, t + RAMP_SECONDS);

    for (const src of this._sources) {
      try {
        src.stop(t + RAMP_SECONDS + 0.001);
      } catch {
        /* already stopped */
      }
    }
    this._sources.clear();
    if (turnId !== undefined && turnId !== null) this._cancelled.add(turnId);

    this._nextStart = 0;
    this.gain.gain.setValueAtTime(1, t + RAMP_SECONDS + 0.005);
    this.report(played);
    return played;
  }

  report(playedOverride = null) {
    if (!this.onReport) return;
    this.onReport({
      t: "playback_state",
      turn_id: this._turnId,
      played_samples: playedOverride ?? this.playedSamples,
      queued_samples: this.queuedSamples,
      playing: this.playing,
      underruns: this._underruns,
    });
  }

  startReporting(intervalMs = 250) {
    this.stopReporting();
    this._reportTimer = setInterval(() => {
      if (this.playing) this.report();
    }, intervalMs);
  }

  stopReporting() {
    if (this._reportTimer) clearInterval(this._reportTimer);
    this._reportTimer = null;
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

  get stats() {
    return { underruns: this._underruns, sources: this._sources.size };
  }
}
