// The hand-gesture rules (frontend/holo/gestures.js), fed made-up hands.
//
// Run: node tests/holo_gestures.test.mjs
//
// Each synthetic hand is 21 landmarks in MediaPipe's normalised image
// coordinates, laid out so the geometry tests the engine uses (finger
// extended = tip further from the wrist than its middle joint, pinch =
// thumb-to-index distance over hand size) come out the way the pose says.

import { createGestureEngine } from "../frontend/holo/gestures.js";

const W = 1000, H = 800, ASPECT = 4 / 3, SIZE = 0.12;  // wrist to middle knuckle

function hand({ u = 0.5, v = 0.45, pose = "open", pinch = 0.6 } = {}) {
  const lm = Array.from({ length: 21 }, () => ({ x: u, y: v, z: 0 }));
  const set = (i, x, y) => { lm[i] = { x, y, z: 0 }; };
  set(0, u, v + SIZE);  // wrist straight below the middle knuckle
  const fingers = [[5, -0.03], [9, 0], [13, 0.03], [17, 0.055]];  // [MCP index, x offset]
  const out = {
    open: [1, 1, 1, 1], pinch: [1, 1, 1, 1], point: [1, 0, 0, 0], fist: [0, 0, 0, 0], thumb: [0, 0, 0, 0],
  }[pose];
  fingers.forEach(([mcp, dx], f) => {
    set(mcp, u + dx, v);
    set(mcp + 1, u + dx, v - 0.05);                               // PIP
    set(mcp + 2, u + dx, out[f] ? v - 0.08 : v + 0.01);            // DIP
    set(mcp + 3, u + dx, out[f] ? v - 0.11 : v + 0.02);            // tip
  });
  set(1, u - 0.05, v + 0.08); set(2, u - 0.07, v + 0.05); set(3, u - 0.08, v + 0.02);
  if (pose === "open") set(4, u - 0.12, v);
  else if (pose === "thumb") set(4, u - 0.1, v - 0.12);
  else if (pose === "pinch") {
    const tip = lm[8];
    set(4, tip.x - (pinch * SIZE) / ASPECT, tip.y);
  } else set(4, u - 0.01, v + 0.01);  // tucked in
  return lm;
}

const frame = (hands) => ({ width: W, height: H, aspect: ASPECT, hands });
const one = (opts, gesture = null, handedness = "Right") => frame([{ landmarks: hand(opts), handedness, gesture }]);

function run(engine, frames, t0 = 0, step = 33) {
  let t = t0, last;
  const events = [];
  for (const f of frames) { last = engine.update(f, (t += step)); events.push(...last.events); }
  return { last, events, t };
}

const tests = {
  "pinch grabs at 0.35 and lets go only past 0.50"() {
    const e = createGestureEngine();
    const at = (r) => e.update(one({ pose: "pinch", pinch: r }), (at.t = (at.t || 0) + 33)).hands[0].pinch;
    assert(at(0.6) === false, "open hand pinching");
    assert(at(0.3) === true, "0.30 should grab");
    assert(at(0.42) === true, "0.42 is inside the band: keep holding");
    assert(at(0.55) === false, "0.55 should let go");
    assert(at(0.42) === false, "0.42 is inside the band: stay released");
  },

  "a still hand does not jitter, a moving one follows"() {
    const e = createGestureEngine();
    let t = 0, xs = [];
    for (let i = 0; i < 60; i++) {
      const noise = (i % 2 ? 1 : -1) * 0.0006;
      xs.push(e.update(one({ u: 0.5 + noise }), (t += 33)).hands[0].x);
    }
    const settled = xs.slice(20);
    const spread = Math.max(...settled) - Math.min(...settled);
    assert(spread < 3, `cursor wandered ${spread.toFixed(2)} px on a still hand`);
    let x;
    for (let i = 0; i < 30; i++) x = e.update(one({ u: 0.35 }), (t += 33)).hands[0].x;
    assert(x - settled[0] > 150, `cursor should follow a real move (moved ${Math.round(x - settled[0])} px)`);
  },

  "the camera is mirrored: moving your hand right moves the cursor right"() {
    const e = createGestureEngine({ smoothing: 0 });
    const x1 = run(e, Array(10).fill(one({ u: 0.6 }))).last.hands[0].x;
    const x2 = run(e, Array(10).fill(one({ u: 0.4 })), 1000).last.hands[0].x;
    // Image x falls as you move to your right in a selfie view.
    assert(x2 > x1, `expected x to grow, got ${x1} -> ${x2}`);
  },

  "the middle of the camera view reaches the screen corners"() {
    const e = createGestureEngine({ smoothing: 0 });
    const r = run(e, Array(15).fill(one({ u: 0.9, v: 0.15 }))).last.hands[0];
    assert(r.x < 5 && r.y < 5, `expected the top-left corner, got ${r.x}, ${r.y}`);
  },

  "fist beats pinch, pointing is only the index out"() {
    const e = createGestureEngine();
    const fist = run(e, Array(3).fill(one({ pose: "fist" }))).last.hands[0];
    assert(fist.fist && !fist.pinch, `fist ${fist.fist} pinch ${fist.pinch}`);
    const e2 = createGestureEngine();
    const pt = run(e2, Array(3).fill(one({ pose: "point" }))).last.hands[0];
    assert(pt.pointing && !pt.fist && !pt.pinch, JSON.stringify({ pt: pt.pointing, f: pt.fist, p: pt.pinch }));
    const e3 = createGestureEngine();
    const open = run(e3, Array(3).fill(one({ pose: "open" }))).last.hands[0];
    assert(open.palm && !open.pointing, "open hand should be a palm");
  },

  "a fast sideways open hand swipes, once per cooldown"() {
    const e = createGestureEngine();
    const frames = [];
    for (let i = 0; i <= 6; i++) frames.push(one({ u: 0.75 - i * 0.07 }));  // your hand moving right
    const { events, t } = run(e, frames);
    assert(events.filter((x) => x.startsWith("swipe")).join() === "swipe_right", events.join());
    const back = [];
    for (let i = 0; i <= 6; i++) back.push(one({ u: 0.33 + i * 0.07 }));
    const again = run(e, back, t);
    assert(!again.events.some((x) => x.startsWith("swipe")), "a second swipe inside the cooldown should not count");
    const later = run(e, back.slice().reverse(), again.t + 1000);
    assert(later.events.some((x) => x.startsWith("swipe")), "after the cooldown it should swipe again");
  },

  "a slow drift is not a swipe"() {
    const e = createGestureEngine();
    const frames = [];
    for (let i = 0; i <= 40; i++) frames.push(one({ u: 0.75 - i * 0.01 }));
    const { events } = run(e, frames);
    assert(!events.some((x) => x.startsWith("swipe")), events.join());
  },

  "thumbs up held half a second confirms, once"() {
    const e = createGestureEngine();
    const up = one({ pose: "thumb" }, { name: "Thumb_Up", score: 0.9 });
    const short = run(e, Array(10).fill(up));  // 330 ms
    assert(!short.events.includes("confirm"), "confirmed too soon");
    const long = run(e, Array(20).fill(up), short.t);
    assert(long.events.filter((x) => x === "confirm").length === 1, long.events.join());
    const e2 = createGestureEngine();
    const weak = run(e2, Array(30).fill(one({ pose: "thumb" }, { name: "Thumb_Up", score: 0.3 })));
    assert(!weak.events.includes("confirm"), "a low-confidence thumbs up must not confirm");
  },

  "opening the hand after a pinch fires palm"() {
    const e = createGestureEngine();
    const r = run(e, [...Array(4).fill(one({ pose: "pinch", pinch: 0.2 })), ...Array(3).fill(one({ pose: "open" }))]);
    assert(r.events.includes("palm"), r.events.join());
  },

  "a short dropout keeps the hand, a long one loses it"() {
    const e = createGestureEngine();
    const r = run(e, Array(5).fill(one({ pose: "pinch", pinch: 0.2 })));
    const gap = e.update(frame([]), r.t + 120).hands;
    assert(gap.length === 1 && gap[0].visible && gap[0].pinch, "a 120 ms gap should not drop a drag");
    const gone = e.update(frame([]), r.t + 400).hands;
    assert(gone.length === 1 && gone[0].visible === false, "after 400 ms the hand is gone");
    assert(e.update(frame([]), r.t + 450).hands.length === 0, "and is reported gone only once");
  },

  "two hands with the same label still get two ids"() {
    const e = createGestureEngine();
    const f = frame([
      { landmarks: hand({ u: 0.7 }), handedness: "Right" },
      { landmarks: hand({ u: 0.3 }), handedness: "Right" },
    ]);
    const ids = e.update(f, 33).hands.map((h) => h.id);
    assert(new Set(ids).size === 2, ids.join());
  },
};

function assert(ok, msg) { if (!ok) throw new Error(msg); }

let passed = 0, failed = 0;
for (const [name, fn] of Object.entries(tests)) {
  try { fn(); console.log(`  PASS  ${name}`); passed++; }
  catch (err) { console.log(`  FAIL  ${name}: ${err.message}`); failed++; }
}
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
