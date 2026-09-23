// Dot-matrix grid visualiser -- a vanilla port of LiveKit's
// AgentAudioVisualizerGrid, which is React-only and assumes a LiveKit
// session. Here it is driven by our own FSM state and by AnalyserNodes on
// the real mic and playback graphs.
//
// Two decisions worth knowing:
//
//  1. Dots are <span>s in a CSS grid, not a <canvas>. At 15x15 that is 225
//     nodes, and writing only the opacities that actually changed costs
//     less than a canvas redraw -- which matters because this page is also
//     running a wake word and a VAD on the audio thread.
//
//  2. Every state renders *something*, including OFFLINE. A grid that goes
//     blank reads as "broken", where a slow sweep reads as "waiting".

const TAU = Math.PI * 2;
const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);

// Shortest angular distance, so a sweep crossing +/-PI does not flicker.
function angleGap(a, b) {
  let d = Math.abs(((a - b) % TAU + TAU) % TAU);
  return d > Math.PI ? TAU - d : d;
}

// Per-state colour and floor brightness. The floor keeps the full square
// visible behind the lit shape, which is what gives the lo-fi look.
//
// The backend FSM only knows the last five. DISCONNECTED, CONNECTING and
// OFFLINE describe the link and the microphone, which the server cannot see
// -- but they are the three the user spends most of their time looking at,
// so collapsing them into one dead grid would waste the whole top of the
// page.
//
// Colours are CSS custom properties rather than hex, so each theme in
// styles.css repaints the grid without this file knowing themes exist.
const STATES = {
  DISCONNECTED:     { color: "var(--c-dead)",   floor: 0.045, mode: "static" },
  CONNECTING:       { color: "var(--c-listen)", floor: 0.05, mode: "sweep" },
  OFFLINE:          { color: "var(--c-muted)",  floor: 0.06, mode: "wave" },
  IDLE:             { color: "var(--c-listen)", floor: 0.06, mode: "idle" },
  LISTENING:        { color: "var(--c-listen)", floor: 0.07, mode: "level" },
  THINKING:         { color: "var(--c-think)",  floor: 0.07, mode: "think" },
  SPEAKING:         { color: "var(--c-speak)",  floor: 0.07, mode: "bands" },
  FOLLOW_UP_WINDOW: { color: "var(--c-speak)",  floor: 0.06, mode: "level" },
};

export class DotGrid {
  constructor(el, { rowCount = 15, columnCount = 15, radius = null, fps = 30 } = {}) {
    this.el = el;
    this.rows = rowCount;
    this.cols = columnCount;
    this.cx = (columnCount - 1) / 2;
    this.cy = (rowCount - 1) / 2;
    // Default radius reaches the mid-sides but not the corners, so the
    // corners sit at the floor -- the circular mask in the original.
    this.radius = radius ?? Math.min(rowCount, columnCount) / 2 + 0.2;
    this.minFrame = 1000 / fps;

    this.state = "DISCONNECTED";
    this.level = 0;          // 0..1, smoothed mic loudness
    this.bands = null;       // Float32Array|null, current spectrum
    this.getBands = null;    // () => Float32Array|null

    this.el.style.setProperty("--grid-cols", columnCount);
    this.dots = [];
    this.last = [];
    this.mask = [];
    for (let r = 0; r < rowCount; r++) {
      for (let c = 0; c < columnCount; c++) {
        const dot = document.createElement("span");
        this.el.append(dot);
        this.dots.push(dot);
        this.last.push(-1);
        const d = Math.hypot(c - this.cx, r - this.cy);
        this.mask.push(clamp01((this.radius - d) / 2 + 0.5));
      }
    }

    this.setState("DISCONNECTED");
    this.t0 = performance.now();
    this.prev = 0;
    this._tick = this._tick.bind(this);
    requestAnimationFrame(this._tick);
  }

  setState(name) {
    if (!(name in STATES)) name = "DISCONNECTED";
    this.state = name;
    this.el.style.setProperty("--dot", STATES[name].color);
    this.el.dataset.state = name;
  }

  // dBFS from the backend's level messages, mapped to a usable 0..1 and
  // eased. Raw frame-to-frame dBFS is far too jumpy to drive a shape.
  setLevel(dbfs) {
    if (dbfs === null) return void (this.level = 0);
    this.level += (clamp01((dbfs + 55) / 50) - this.level) * 0.35;
  }

  _tick(now) {
    requestAnimationFrame(this._tick);
    if (now - this.prev < this.minFrame) return;
    this.prev = now;

    const t = (now - this.t0) / 1000;
    const cfg = STATES[this.state];
    this.bands = this.getBands ? this.getBands() : null;
    if (cfg.mode === "bands" && !this.bands) return this._paint(cfg, t, "level");
    this._paint(cfg, t, cfg.mode);
  }

  _paint(cfg, t, mode) {
    const { rows, cols, cx, cy } = this;
    // A single sweep angle / disc radius per frame, not per dot.
    const sweep = mode === "think" ? t * 2.6 : t * 0.9;
    const disc =
      1.2 + this.level * (this.radius - 1.2) + 0.35 * Math.sin(t * 1.7);
    const idle = 2.4 + 1.2 * Math.sin(t * 1.1);

    let i = 0;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++, i++) {
        const dx = c - cx, dy = r - cy;
        const d = Math.hypot(dx, dy);
        let v = 0;

        switch (mode) {
          case "bands": {
            // Low frequencies in the centre, mirrored outwards, drawn as a
            // centred vertical bar per column -- symmetric like the original.
            const n = this.bands.length;
            const b = this.bands[Math.min(n - 1, Math.round((Math.abs(dx) / cx) * (n - 1)))];
            v = clamp01(b * (rows / 2) - Math.abs(dy) + 0.5);
            break;
          }
          case "level":
            v = clamp01(disc - d + 0.5);
            break;
          case "idle":
            v = clamp01(idle - d + 0.5) * 0.8;
            break;
          case "think": {
            // Two opposed arms, faded out at the hub so it reads as rotation
            // rather than a blinking centre.
            const a = Math.atan2(dy, dx);
            const gap = Math.min(angleGap(a, sweep), angleGap(a, sweep + Math.PI));
            v = clamp01(1 - gap / 0.85) * clamp01(d / 2.5);
            break;
          }
          case "sweep": {
            const a = Math.atan2(dy, dx);
            v = clamp01(1 - angleGap(a, sweep) / 0.5) * clamp01(d / 2.5) * 0.85;
            break;
          }
          case "wave":
            // Rings travelling outwards: alive enough to show the socket is
            // up, calm enough to read as "not listening to you".
            v = (0.5 + 0.5 * Math.sin(d * 0.9 - t * 2.2)) * 0.3;
            break;
          default:
            v = 0; // static -- nothing but the floor
        }

        const o = Math.max(cfg.floor, v * this.mask[i]);
        if (Math.abs(o - this.last[i]) > 0.012) {
          this.dots[i].style.opacity = o.toFixed(3);
          this.last[i] = o;
        }
      }
    }
  }
}
