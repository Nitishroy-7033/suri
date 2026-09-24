// The One Euro filter (Casiez et al., 2012): heavy smoothing while a point
// moves slowly -- no jitter when you hold your hand still -- and light
// smoothing when it moves fast, so a quick flick is not left trailing.

class LowPass {
  constructor() { this.y = null; }
  filter(x, a) {
    this.y = this.y === null ? x : a * x + (1 - a) * this.y;
    return this.y;
  }
}

const alpha = (cutoff, dt) => 1 / (1 + 1 / (2 * Math.PI * cutoff * dt));

export class OneEuro {
  /** minCutoff (Hz): smoothing when still. beta: how fast it lets go when moving. */
  constructor({ minCutoff = 1.2, beta = 3, dCutoff = 1 } = {}) {
    Object.assign(this, { minCutoff, beta, dCutoff });
    this.x = new LowPass();
    this.dx = new LowPass();
    this.last = null;
  }
  filter(value, tMs) {
    if (this.last === null) {
      this.last = tMs;
      this.dx.filter(0, 1);
      return this.x.filter(value, 1);
    }
    const dt = Math.max(1e-3, (tMs - this.last) / 1000);
    this.last = tMs;
    const prev = this.x.y;
    const d = this.dx.filter((value - prev) / dt, alpha(this.dCutoff, dt));
    const cutoff = this.minCutoff + this.beta * Math.abs(d);
    return this.x.filter(value, alpha(cutoff, dt));
  }
  reset() { this.x.y = null; this.dx.y = null; this.last = null; }
}

/** A One Euro filter per coordinate of a 2D point. */
export class OneEuro2 {
  constructor(opts) { this.fx = new OneEuro(opts); this.fy = new OneEuro(opts); }
  filter(x, y, tMs) { return [this.fx.filter(x, tMs), this.fy.filter(y, tMs)]; }
  set(opts) { Object.assign(this.fx, opts); Object.assign(this.fy, opts); }
  reset() { this.fx.reset(); this.fy.reset(); }
}
