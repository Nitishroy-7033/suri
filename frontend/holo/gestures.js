import { OneEuro2 } from "./filters.js";

// Hands in, pointers out. Plain geometry on MediaPipe's 21 landmarks per
// hand -- no model here, and no DOM, so tests can feed it made-up hands.
//
// Per hand, each frame:
//   x, y       where the cursor is, in screen pixels (mirrored: move your
//              hand right and it goes right), smoothed and dead-zoned
//   pinch      thumb and index together -- grab at 0.35 hand sizes, let go
//              only past 0.50, so the edge never flickers
//   fist       all four fingers curled (moves the model)
//   pointing   only the index out (hover; hold still to select)
//   palm       everything open (lets go of everything)
// And one-off events: swipe_left / swipe_right (open hand, fast sideways),
// confirm (thumbs up held half a second), palm (a hand opens).
//
// Distances are divided by the hand's own size (wrist to middle knuckle), so
// it works the same close to the camera or across the room.

export const L = {
  WRIST: 0, THUMB_TIP: 4, INDEX_MCP: 5, INDEX_PIP: 6, INDEX_TIP: 8,
  MIDDLE_MCP: 9, MIDDLE_PIP: 10, MIDDLE_TIP: 12, RING_PIP: 14, RING_TIP: 16,
  PINKY_MCP: 17, PINKY_PIP: 18, PINKY_TIP: 20,
};
export const BONES = [
  [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17],
];

export const DEFAULTS = {
  pinchOn: 0.35, pinchOff: 0.5,
  // The middle of the camera's view maps to the whole screen, so the far
  // corners are reachable without the hand leaving the frame.
  region: { x0: 0.15, x1: 0.85, y0: 0.12, y1: 0.78 },
  deadZonePx: 2.5,
  lostMs: 250,           // a hand missing this long is gone (short dropouts are not)
  swipeMs: 320, swipeMinFrac: 0.26, swipeCooldownMs: 800,
  confirmHoldMs: 500,
  smoothing: 0.5,        // 0 = raw, 1 = very steady
};

export function createGestureEngine(opts = {}) {
  const cfg = { ...DEFAULTS, ...opts, region: { ...DEFAULTS.region, ...opts.region } };
  const hands = new Map();  // id -> tracked state
  let lastSwipe = -1e9;

  const filterOpts = () => ({ minCutoff: 3.2 - cfg.smoothing * 2.8, beta: 4 - cfg.smoothing * 2.5 });

  function geometry(lm, aspect) {
    // Normalised image coordinates stretch x by the aspect ratio; undo that
    // so distances mean the same in every direction.
    const p = (i) => [lm[i].x * aspect, lm[i].y];
    const d = (a, b) => { const [ax, ay] = p(a), [bx, by] = p(b); return Math.hypot(ax - bx, ay - by); };
    const size = Math.max(1e-4, d(L.WRIST, L.MIDDLE_MCP));
    const extended = (tip, pip) => d(tip, L.WRIST) > d(pip, L.WRIST) * 1.12;
    const fingers = [
      extended(L.INDEX_TIP, L.INDEX_PIP), extended(L.MIDDLE_TIP, L.MIDDLE_PIP),
      extended(L.RING_TIP, L.RING_PIP), extended(L.PINKY_TIP, L.PINKY_PIP),
    ];
    const thumbOut = d(L.THUMB_TIP, L.INDEX_MCP) / size > 0.55 && d(L.THUMB_TIP, L.PINKY_MCP) / size > 0.9;
    return { pinchRatio: d(L.THUMB_TIP, L.INDEX_TIP) / size, fingers, thumbOut, size };
  }

  function toScreen(u, v, W, H) {
    const r = cfg.region;
    const fx = Math.min(1, Math.max(0, ((1 - u) - r.x0) / (r.x1 - r.x0)));  // mirrored
    const fy = Math.min(1, Math.max(0, (v - r.y0) / (r.y1 - r.y0)));
    return [fx * W, fy * H];
  }

  // MediaPipe labels hands "Left"/"Right", but sometimes both the same; then
  // tell them apart by which side of the picture they are on.
  function ids(list) {
    const labels = list.map((h) => h.handedness || "Hand");
    if (list.length === 2 && labels[0] === labels[1]) {
      const [a, b] = list.map((h) => 1 - h.landmarks[0].x);
      return a < b ? ["Left", "Right"] : ["Right", "Left"];
    }
    return labels;
  }

  return {
    get config() { return cfg; },
    set(patch) {
      Object.assign(cfg, patch);
      for (const h of hands.values()) h.filter.set(filterOpts());
    },
    /**
     * frame: { hands: [{ landmarks: [{x,y,z}...21], handedness, gesture: {name, score} }],
     *          width, height (screen px), aspect (video width / height) }
     * Returns { hands: [...state], events: [...names] }.
     */
    update(frame, now) {
      const W = frame.width, H = frame.height, aspect = frame.aspect || 4 / 3;
      const events = [];
      const seen = new Set();
      const list = (frame.hands || []).filter((h) => h.landmarks?.length === 21);
      const names = ids(list);

      list.forEach((raw, i) => {
        const id = names[i];
        seen.add(id);
        let h = hands.get(id);
        if (!h) {
          h = { id, filter: new OneEuro2(filterOpts()), pinch: false, fist: false, palm: false,
                x: 0, y: 0, trail: [], thumbSince: 0, confirmed: false, fresh: true };
          hands.set(id, h);
        }
        h.lastSeen = now;
        const g = geometry(raw.landmarks, aspect);
        const [curled, out] = [g.fingers.filter((f) => !f).length, g.fingers.filter(Boolean).length];

        // Fist wins over pinch: in a fist the thumb rests near the index too.
        const named = raw.gesture?.score > 0.55 ? raw.gesture.name : "";
        h.fist = h.fist ? curled >= 3 : curled === 4 && (named === "Closed_Fist" || !g.thumbOut);
        const wasPinch = h.pinch;
        h.pinch = !h.fist && (h.pinch ? g.pinchRatio < cfg.pinchOff : g.pinchRatio < cfg.pinchOn);
        h.pointing = !h.pinch && !h.fist && g.fingers[0] && curled >= 3;
        const palm = !h.pinch && !h.fist && out === 4 && (g.thumbOut || named === "Open_Palm");
        if (palm && !h.palm && !h.fresh) events.push("palm");
        h.palm = palm;
        h.pinchRatio = g.pinchRatio;

        // The cursor: the fingertip, or between thumb and finger mid-pinch
        // (where the two meet, so grabbing does not make it jump).
        const lm = raw.landmarks;
        const tip = lm[L.INDEX_TIP], th = lm[L.THUMB_TIP];
        const u = h.pinch || wasPinch ? (tip.x + th.x) / 2 : tip.x;
        const v = h.pinch || wasPinch ? (tip.y + th.y) / 2 : tip.y;
        const [sx, sy] = toScreen(u, v, W, H);
        const [fx, fy] = h.filter.filter(sx, sy, now);
        if (h.fresh || Math.hypot(fx - h.x, fy - h.y) > cfg.deadZonePx) { h.x = fx; h.y = fy; }

        // Screen positions of every joint, for drawing the skeleton.
        h.joints = lm.map((p) => toScreen(p.x, p.y, W, H));

        // Swipe: an open hand moving fast sideways.
        const [px] = toScreen(lm[L.MIDDLE_MCP].x, lm[L.MIDDLE_MCP].y, W, H);
        h.trail.push([now, px]);
        while (h.trail.length && now - h.trail[0][0] > cfg.swipeMs) h.trail.shift();
        if (!h.pinch && !h.fist && out >= 3 && h.trail.length > 3 && now - lastSwipe > cfg.swipeCooldownMs) {
          const dx = px - h.trail[0][1];
          if (Math.abs(dx) > cfg.swipeMinFrac * W) {
            events.push(dx > 0 ? "swipe_right" : "swipe_left");
            lastSwipe = now;
            h.trail.length = 0;
          }
        }

        // Thumbs up, held: confirm, once per hold.
        if (named === "Thumb_Up" && !h.pinch) {
          if (!h.thumbSince) h.thumbSince = now;
          if (!h.confirmed && now - h.thumbSince >= cfg.confirmHoldMs) { events.push("confirm"); h.confirmed = true; }
        } else { h.thumbSince = 0; h.confirmed = false; }
        h.thumbHeld = h.thumbSince ? Math.min(1, (now - h.thumbSince) / cfg.confirmHoldMs) : 0;
        h.fresh = false;
      });

      const out = [];
      for (const [id, h] of hands) {
        const visible = seen.has(id) || now - h.lastSeen < cfg.lostMs;
        if (!visible) { hands.delete(id); out.push({ id, visible: false }); continue; }
        out.push({
          id, visible: true, x: h.x, y: h.y, pinch: h.pinch, fist: h.fist, pointing: h.pointing,
          palm: h.palm, pinchRatio: h.pinchRatio, joints: h.joints, thumbHeld: h.thumbHeld,
          tracked: seen.has(id),
        });
      }
      return { hands: out, events };
    },
    reset() { hands.clear(); },
  };
}
