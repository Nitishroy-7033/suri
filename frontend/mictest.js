// Mic test: a live meter, the checks that matter for Jarvis, a record-and-
// play-back loop and a speaker test. It opens its own stream with the same
// constraints the real capture uses, so what passes here works there.

const DB_FLOOR = -60;
const HEARD_DB = -38;   // speech at a normal distance lands well above this
const QUIET_DB = -52;   // peak under this after a few seconds: probably muted
const CLIP_DB = -1.5;

export class MicTest {
  constructor(dialog, { deviceId = () => null, playTone = null, onBusy = null } = {}) {
    this.dlg = dialog;
    this.deviceId = deviceId;
    this.playTone = playTone;
    this.onBusy = onBusy;  // (true|false): lets the app mute the real mic while testing
    this.$ = (sel) => dialog.querySelector(sel);
    this.stream = null;
    this.ctx = null;
    this.raf = 0;

    this.$("[data-close]").onclick = () => this.close();
    this.$("#mt-record").onclick = () => this.record();
    this.$("#mt-speaker").onclick = () => this.playTone?.();
    dialog.addEventListener("close", () => this.stop());
    dialog.addEventListener("click", (e) => { if (e.target === dialog) this.close(); });
  }

  async open() {
    this.dlg.showModal();
    this.onBusy?.(true);
    this.reset();
    try {
      await this.start();
    } catch (err) {
      this.check("device", "bad", err.name === "NotAllowedError"
        ? "Blocked — allow the microphone from the icon in the address bar"
        : err.name === "NotFoundError" ? "No microphone found"
        : err.message);
      for (const k of ["level", "rate", "aec"]) this.check(k, "wait", "Needs microphone access");
    }
  }

  close() { if (this.dlg.open) this.dlg.close(); }

  reset() {
    for (const k of ["device", "rate", "aec", "level"]) this.check(k, "wait", "…");
    this.$("#mt-db").textContent = "−∞";
    this.$("#mt-fill").style.width = "0%";
    this.$("#mt-peak").style.left = "0%";
    this.$("#mt-playback").hidden = true;
    this.$("#mt-record").disabled = true;
    this.$("#mt-record-label").textContent = "Record 3 seconds";
  }

  async start() {
    const id = this.deviceId();
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        ...(id ? { deviceId: { exact: id } } : {}),
        channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      },
    });
    const track = this.stream.getAudioTracks()[0];
    const s = track.getSettings?.() ?? {};
    this.check("device", "ok", track.label || "Default microphone");

    this.ctx = new AudioContext({ sampleRate: 16000 });
    const rate = this.ctx.sampleRate;
    this.check("rate", rate === 16000 ? "ok" : "bad",
      rate === 16000 ? "16 kHz — matches the wake word and speech models"
        : `${rate} Hz — expected 16 kHz; try Chrome or Edge`);
    this.check("aec", s.echoCancellation === false ? "warn" : "ok",
      s.echoCancellation === false
        ? "Off — use headphones, or Jarvis may hear itself"
        : `On${s.noiseSuppression ? " · noise suppression on" : ""}`);

    const src = this.ctx.createMediaStreamSource(this.stream);
    const an = this.ctx.createAnalyser();
    an.fftSize = 1024;
    src.connect(an);
    this.meter(an);
    this.$("#mt-record").disabled = false;
  }

  meter(an) {
    const buf = new Float32Array(an.fftSize);
    let peak = DB_FLOOR, heard = false, clipped = false;
    const started = performance.now();
    this.check("level", "wait", "Say something — “Hey Jarvis, testing”");
    const tick = () => {
      an.getFloatTimeDomainData(buf);
      let sum = 0, max = 0;
      for (const v of buf) { sum += v * v; max = Math.max(max, Math.abs(v)); }
      const db = Math.max(DB_FLOOR, 20 * Math.log10(Math.sqrt(sum / buf.length) || 1e-9));
      const maxDb = 20 * Math.log10(max || 1e-9);
      peak = Math.max(peak - 0.15, db);  // slow fall so the marker is readable
      const pct = (x) => `${((x - DB_FLOOR) / -DB_FLOOR) * 100}%`;
      this.$("#mt-fill").style.width = pct(db);
      this.$("#mt-fill").dataset.hot = maxDb > CLIP_DB ? "1" : "0";
      this.$("#mt-peak").style.left = pct(peak);
      this.$("#mt-db").textContent = db <= DB_FLOOR ? "−∞" : db.toFixed(0);

      if (maxDb > CLIP_DB && !clipped) {
        clipped = true;
        this.check("level", "warn", "Too loud — it's clipping; move back a little or lower the input volume");
      } else if (db > HEARD_DB && !heard && !clipped) {
        heard = true;
        this.check("level", "ok", "We can hear you clearly");
      } else if (!heard && !clipped && performance.now() - started > 6000 && peak < QUIET_DB) {
        this.check("level", "bad", "Very quiet — check the mic isn't muted, or pick another one in Settings");
      }
      this.raf = requestAnimationFrame(tick);
    };
    tick();
  }

  async record() {
    if (!this.stream) return;
    const btn = this.$("#mt-record"), label = this.$("#mt-record-label");
    btn.disabled = true;
    const chunks = [];
    const rec = new MediaRecorder(this.stream);
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = () => {
      const audio = this.$("#mt-playback");
      if (audio.src) URL.revokeObjectURL(audio.src);
      audio.src = URL.createObjectURL(new Blob(chunks, { type: rec.mimeType }));
      audio.hidden = false;
      audio.play().catch(() => {});
      label.textContent = "Record again";
      btn.disabled = false;
    };
    rec.start();
    for (let s = 3; s > 0; s--) {
      label.textContent = `Recording… ${s}`;
      await new Promise((r) => setTimeout(r, 1000));
    }
    if (rec.state === "recording") rec.stop();
  }

  check(key, status, text) {
    const row = this.$(`[data-check="${key}"]`);
    if (!row) return;
    row.dataset.status = status;
    row.querySelector("span").textContent = text;
  }

  stop() {
    cancelAnimationFrame(this.raf);
    if (this.stream) for (const t of this.stream.getTracks()) t.stop();
    this.ctx?.close().catch(() => {});
    this.stream = null;
    this.ctx = null;
    const audio = this.$("#mt-playback");
    audio.pause();
    this.onBusy?.(false);
  }
}
