// Full-screen backdrop and the telemetry scope.
//
// The backdrop is one <canvas> behind everything, with one renderer per
// theme. Each renderer gets an "energy" (0..1) derived from the FSM state
// and mic level, so the room itself reacts: rain falls faster while Jarvis
// speaks, the contour lines swell with your voice.
//
// Budget matters more than looks here -- the page is also running audio
// capture, a wake word and a VAD. So: capped at 30 fps, device pixel ratio
// capped at 1.5, and nothing drawn at all while the tab is hidden.

const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const TAU = Math.PI * 2;

// How lively the backdrop is in each state, before mic level is added.
const ENERGY = {
  DISCONNECTED: 0.05, CONNECTING: 0.2, OFFLINE: 0.15, IDLE: 0.3,
  LISTENING: 0.55, THINKING: 0.85, SPEAKING: 1, FOLLOW_UP_WINDOW: 0.45,
};

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// "#rrggbb" -> "rgba(r,g,b,a)"; anything else is passed through.
function rgba(hex, a) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
}

export class Backdrop {
  constructor(canvas, { fps = 30 } = {}) {
    this.cv = canvas;
    this.ctx = canvas.getContext("2d");
    this.minFrame = 1000 / fps;
    this.state = "OFFLINE";
    this.level = 0;
    this.energy = 0.15;
    this.theme = null;
    this.anchor = null;       // element the HUD rings centre on
    this.prev = 0;
    this.t0 = performance.now();
    this._resize = this._resize.bind(this);
    this._tick = this._tick.bind(this);
    addEventListener("resize", this._resize);
    this._resize();
    requestAnimationFrame(this._tick);
  }

  setState(name) { this.state = name; }
  setLevel(x) { this.level = clamp01(x); }

  setTheme(name) {
    this.theme = name;
    this.colors = {
      bg: cssVar("--bg"),
      a: cssVar("--fx-1"),
      b: cssVar("--fx-2"),
      listen: cssVar("--c-listen"),
      think: cssVar("--c-think"),
      speak: cssVar("--c-speak"),
    };
    this._seed();
  }

  // The colour of "now": what the grid is showing, echoed in the room.
  get tint() {
    const c = this.colors;
    if (this.state === "THINKING") return c.think;
    if (this.state === "SPEAKING" || this.state === "FOLLOW_UP_WINDOW") return c.speak;
    return c.a;
  }

  _resize() {
    const dpr = Math.min(1.5, devicePixelRatio || 1);
    this.dpr = dpr;
    this.w = innerWidth;
    this.h = innerHeight;
    this.cv.width = Math.round(this.w * dpr);
    this.cv.height = Math.round(this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (this.theme) this._seed();
  }

  _seed() {
    const { w, h } = this;
    this.ctx.clearRect(0, 0, w, h);
    if (this.theme === "matrix") {
      this.fontSize = w < 700 ? 14 : 17;
      const cols = Math.ceil(w / this.fontSize);
      this.drops = Array.from({ length: cols }, () => Math.random() * -h / this.fontSize);
      this.speeds = Array.from({ length: cols }, () => 0.35 + Math.random() * 0.65);
    } else if (this.theme === "bento") {
      this.blobs = [0, 1, 2].map((i) => ({ p: Math.random() * TAU, s: 0.05 + i * 0.03 }));
    } else {
      this.blips = Array.from({ length: 18 }, () => ({
        a: Math.random() * TAU, d: 0.35 + Math.random() * 0.65, life: Math.random(),
      }));
    }
  }

  _tick(now) {
    requestAnimationFrame(this._tick);
    if (document.hidden || now - this.prev < this.minFrame) return;
    const dt = Math.min(0.1, (now - this.prev) / 1000);
    this.prev = now;
    if (!this.theme) return;
    // Ease towards the target so state changes swell instead of snapping.
    const target = clamp01((ENERGY[this.state] ?? 0.2) + this.level * 0.5);
    this.energy += (target - this.energy) * 0.06;
    const t = (now - this.t0) / 1000;
    if (this.theme === "matrix") this._matrix(dt);
    else if (this.theme === "paper") this._paper(t);
    else if (this.theme === "bento") this._bento(t);
    else this._arc(t);
  }

  // --- 1. Matrix: digital rain --------------------------------------------
  _matrix(dt) {
    const { ctx, w, h, fontSize: fs } = this;
    // Translucent wipe rather than a clear: that is what leaves the trails.
    ctx.fillStyle = rgba(this.colors.bg, 0.14);
    ctx.fillRect(0, 0, w, h);
    ctx.font = `${fs}px "Share Tech Mono", monospace`;
    const glyphs = "アイウエオカキクケコサシスセソタチツテトナニヌネノ0123456789JARVIS<>/*+=";
    const speed = 8 + this.energy * 26;
    const tint = this.tint;
    for (let i = 0; i < this.drops.length; i++) {
      const y = this.drops[i] * fs;
      const ch = glyphs[(Math.random() * glyphs.length) | 0];
      // Bright head, tinted body.
      ctx.fillStyle = Math.random() < 0.08 ? "#e8ffe8" : tint;
      ctx.globalAlpha = 0.14 + this.energy * 0.26;
      ctx.fillText(ch, i * fs, y);
      this.drops[i] += this.speeds[i] * speed * dt;
      if (y > h && Math.random() > 0.975) this.drops[i] = Math.random() * -20;
    }
    ctx.globalAlpha = 1;
  }

  // --- 2. Arc HUD: rings, ticks and a radar sweep around the core --------
  _arc(t) {
    const { ctx, w, h } = this;
    ctx.clearRect(0, 0, w, h);
    const r0 = this.anchor?.getBoundingClientRect();
    const cx = r0 ? r0.left + r0.width / 2 : w / 2;
    const cy = r0 ? r0.top + r0.height / 2 : h / 2;
    const base = r0 ? r0.width * 0.36 : Math.min(w, h) * 0.2;
    const e = this.energy;
    const tint = this.tint;

    // Faint perspective grid across the room.
    ctx.strokeStyle = rgba(this.colors.a, 0.045);
    ctx.lineWidth = 1;
    const step = 48;
    ctx.beginPath();
    for (let x = (cx % step); x < w; x += step) { ctx.moveTo(x, 0); ctx.lineTo(x, h); }
    for (let y = (cy % step); y < h; y += step) { ctx.moveTo(0, y); ctx.lineTo(w, y); }
    ctx.stroke();

    // Concentric tick rings, alternating direction.
    for (let k = 0; k < 3; k++) {
      const R = base * (1.55 + k * 0.32);
      const n = 72 + k * 24;
      const rot = t * (k % 2 ? -0.05 : 0.08) * (0.5 + e);
      ctx.strokeStyle = rgba(k === 0 ? tint : this.colors.a, 0.1 + e * 0.12);
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        if ((i + k) % 9 === 0) continue;
        const a = rot + (i / n) * TAU;
        const len = i % 6 === 0 ? 10 : 4;
        ctx.moveTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
        ctx.lineTo(cx + Math.cos(a) * (R + len), cy + Math.sin(a) * (R + len));
      }
      ctx.stroke();
    }

    // Radar sweep.
    const R = base * 2.3;
    const sweep = t * (0.6 + e * 1.4);
    const g = ctx.createConicGradient?.(sweep, cx, cy);
    if (g) {
      g.addColorStop(0, rgba(tint, 0.16 * (0.4 + e)));
      g.addColorStop(0.08, rgba(tint, 0));
      g.addColorStop(1, rgba(tint, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, TAU);
      ctx.fill();
    }

    // Contacts that light up as the sweep passes them.
    for (const b of this.blips) {
      b.life -= 0.004;
      if (b.life <= 0) Object.assign(b, { a: Math.random() * TAU, d: 0.35 + Math.random() * 0.65, life: 1 });
      const x = cx + Math.cos(b.a) * R * b.d, y = cy + Math.sin(b.a) * R * b.d;
      const lag = ((sweep - b.a) % TAU + TAU) % TAU;
      const glow = Math.max(0, 1 - lag / 1.6) * b.life;
      ctx.fillStyle = rgba(tint, 0.15 + glow * 0.75);
      ctx.fillRect(x - 1.5, y - 1.5, 3, 3);
    }
  }

  // --- 3. Paper: slow ink contour lines, like a topographic map --------
  _paper(t) {
    const { ctx, w, h } = this;
    ctx.clearRect(0, 0, w, h);
    const e = this.energy;
    const amp = 8 + e * 26;
    const n = 26;
    ctx.lineWidth = 1;
    for (let k = 0; k < n; k++) {
      const y0 = (h * (k + 0.5)) / n;
      // Every fifth line carries the state colour; the rest are pencil.
      const accent = k % 5 === 2;
      ctx.strokeStyle = rgba(accent ? this.tint : this.colors.a, accent ? 0.14 + e * 0.12 : 0.05);
      ctx.beginPath();
      for (let x = -10; x <= w + 10; x += 14) {
        const y = y0
          + amp * Math.sin(x * 0.0042 + t * 0.25 + k * 0.55)
          + amp * 0.45 * Math.sin(x * 0.011 - t * (0.35 + e * 0.6) + k * 1.3);
        x < 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
  }

  // --- 4. Bento: three soft light pools drifting behind the tiles --------
  _bento(t) {
    const { ctx, w, h } = this;
    ctx.clearRect(0, 0, w, h);
    const e = this.energy;
    const hues = [this.tint, this.colors.b, this.colors.speak];
    const R = Math.max(w, h) * (0.42 + e * 0.1);
    this.blobs.forEach((b, i) => {
      const a = b.p + t * b.s * (1 + e);
      const x = w * (0.5 + 0.38 * Math.cos(a + i * 2.1));
      const y = h * (0.5 + 0.32 * Math.sin(a * 1.3 + i));
      const g = ctx.createRadialGradient(x, y, 0, x, y, R);
      g.addColorStop(0, rgba(hues[i], 0.07 + e * 0.07));
      g.addColorStop(1, rgba(hues[i], 0));
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
    });
  }
}

// Rolling strip chart of wake score, VAD and input level. Cheap: redrawn
// only when a new sample arrives, not on every animation frame.
export class Scope {
  constructor(canvas, { samples = 250 } = {}) {
    this.cv = canvas;
    this.ctx = canvas.getContext("2d");
    this.n = samples;
    this.series = { wake: [], vad: [], lvl: [] };
    this.threshold = 0.5;
    new ResizeObserver(() => this._resize()).observe(canvas);
    this._resize();
  }

  _resize() {
    const dpr = Math.min(2, devicePixelRatio || 1);
    const r = this.cv.getBoundingClientRect();
    this.w = Math.max(1, r.width); this.h = Math.max(1, r.height);
    this.cv.width = Math.round(this.w * dpr);
    this.cv.height = Math.round(this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  push(wake, vad, lvl) {
    for (const [k, v] of [["wake", wake], ["vad", vad], ["lvl", lvl]]) {
      const s = this.series[k];
      s.push(clamp01(v));
      if (s.length > this.n) s.shift();
    }
    this.draw();
  }

  draw() {
    const { ctx, w, h } = this;
    ctx.clearRect(0, 0, w, h);
    const colors = {
      wake: cssVar("--c-think"), vad: cssVar("--c-speak"), lvl: cssVar("--c-listen"),
    };
    // Threshold guide.
    ctx.strokeStyle = rgba(colors.wake, 0.35);
    ctx.setLineDash([3, 4]);
    ctx.beginPath();
    const ty = h - this.threshold * (h - 4) - 2;
    ctx.moveTo(0, ty); ctx.lineTo(w, ty); ctx.stroke();
    ctx.setLineDash([]);

    const dx = w / (this.n - 1);
    for (const k of ["lvl", "vad", "wake"]) {
      const s = this.series[k];
      if (s.length < 2) continue;
      const x0 = w - (s.length - 1) * dx;
      ctx.beginPath();
      for (let i = 0; i < s.length; i++) {
        const x = x0 + i * dx, y = h - s[i] * (h - 4) - 2;
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      if (k === "lvl") {
        // Level as a filled area underneath, the two scores as lines.
        ctx.lineTo(w, h); ctx.lineTo(x0, h); ctx.closePath();
        ctx.fillStyle = rgba(colors.lvl, 0.18);
        ctx.fill();
      } else {
        ctx.strokeStyle = colors[k];
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    }
  }
}
