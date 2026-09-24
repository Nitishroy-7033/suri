// The hands tutorial's step detection (frontend/holo/tutorial-steps.js), and
// that the guide's shortcut table matches what the keyboard really does
// (frontend/holo/keys.js).
//
// Run: node tests/holo_tutorial.test.mjs

import { createCoach, STEPS } from "../frontend/holo/tutorial-steps.js";
import { WORKSHOP_KEYS, actFor } from "../frontend/holo/keys.js";
import { POSES, gestureSvg } from "../frontend/holo/poses.js";

const hand = (o = {}) => ({ id: "R", visible: true, tracked: true, x: 500, y: 400, pinch: false, fist: false,
  pointing: false, palm: false, thumbHeld: 0, ...o });

function at(id) {
  const c = createCoach();
  while (c.step.id !== id) c.next();
  return c;
}

const tests = {
  "the steps are the ones the guide promises"() {
    const ids = STEPS.map((s) => s.id);
    assert(ids.join() === "see,point,pinch,drag,zoom,fist,palm,swipe,select,thumb", ids.join());
    for (const s of STEPS) assert(s.title && s.say && s.gfx, s.id);
  },

  "show a hand: held in view, not a flicker"() {
    const c = at("see");
    c.frame([hand()], 0);
    c.frame([], 300);  // lost it
    c.frame([hand()], 400);
    assert(!c.frame([hand()], 900), "only 500 ms since it came back");
    assert(c.frame([hand()], 1150), "700 ms in view completes it");
  },

  "point needs the pointing pose, not just a hand"() {
    const c = at("point");
    for (let t = 0; t < 2000; t += 100) c.frame([hand()], t);
    assert(!c.done);
    c.frame([hand({ pointing: true })], 2000);
    assert(c.frame([hand({ pointing: true })], 2800));
  },

  "pinch completes on letting go"() {
    const c = at("pinch");
    assert(!c.frame([hand({ pinch: true })], 0) && c.progress > 0.5);
    assert(c.frame([hand()], 100));
  },

  "drag: a pinch that travels 220 px, and moving while open doesn't count"() {
    const c = at("drag");
    c.frame([hand({ x: 100 })], 0);
    c.frame([hand({ x: 600 })], 50);
    assert(c.progress === 0, "moving an open hand");
    c.frame([hand({ x: 600, pinch: true })], 100);
    c.frame([hand({ x: 700, pinch: true })], 150);
    assert(!c.done && c.progress > 0.4);
    assert(c.frame([hand({ x: 830, pinch: true })], 200));
  },

  "zoom needs both hands pinching and a real change in distance"() {
    const c = at("zoom");
    c.frame([hand({ id: "L", x: 400, pinch: true }), hand({ x: 600 })], 0);
    assert(c.progress === 0, "one pinching hand is not a zoom");
    c.frame([hand({ id: "L", x: 400, pinch: true }), hand({ x: 600, pinch: true })], 50);
    c.frame([hand({ id: "L", x: 380, pinch: true }), hand({ x: 620, pinch: true })], 100);
    assert(!c.done, "20% apart is not enough");
    assert(c.frame([hand({ id: "L", x: 360, pinch: true }), hand({ x: 640, pinch: true })], 150));
  },

  "fist must move to count"() {
    const c = at("fist");
    c.frame([hand({ fist: true, y: 300 })], 0);
    assert(!c.frame([hand({ fist: true, y: 300 })], 500));
    assert(c.frame([hand({ fist: true, y: 460 })], 600));
  },

  "palm: the gesture event, or an open hand held"() {
    const c = at("palm");
    assert(c.gesture("palm"));
    const c2 = at("palm");
    c2.frame([hand({ palm: true })], 0);
    assert(c2.frame([hand({ palm: true })], 600));
  },

  "swipe only on a swipe"() {
    const c = at("swipe");
    assert(!c.gesture("palm") && !c.gesture("confirm"));
    assert(c.gesture("swipe_left"));
  },

  "select: a dwell on a part, a click only gets most of the way"() {
    const c = at("select");
    assert(!c.select(null, {}));
    assert(!c.select("Piston", { x: 1, y: 1 }) && c.progress >= 0.7);
    assert(c.select("Piston", { dwell: true }));
  },

  "thumbs up: the confirm event, with the hold shown as progress"() {
    const c = at("thumb");
    c.frame([hand({ thumbHeld: 0.5 })], 0);
    assert(!c.done && c.progress > 0.4);
    assert(c.gesture("confirm"));
  },

  "next, back and finishing"() {
    const c = createCoach();
    c.next(); c.next();
    assert(c.index === 2 && c.progress === 0);
    c.back();
    assert(c.index === 1);
    while (c.next());
    assert(c.finished && c.index === STEPS.length - 1);
    c.reset();
    assert(c.index === 0 && !c.finished);
  },

  "every shortcut in the guide does something"() {
    for (const s of WORKSHOP_KEYS) {
      for (const k of s.keys) {
        const key = { "Space": " ", "←": "ArrowLeft", "→": "ArrowRight", "−": "-" }[k] ?? k;
        const hit = actFor({ key });
        assert(hit && hit.act === s.act, `${k} should do ${s.act}, got ${JSON.stringify(hit)}`);
      }
    }
    assert(actFor({ key: "G" }).act === "hands", "letters work with shift too");
    assert(actFor({ key: "m" }) === null, "M is the mic (app.js), not the workshop");
    assert(actFor({ key: "ArrowLeft" }).dir === -1 && actFor({ key: "=" }).dir === 1);
  },

  "every pose and picture draws"() {
    for (const [name, pts] of Object.entries(POSES)) assert(Object.keys(pts).length === 21, `${name} has ${Object.keys(pts).length} joints`);
    for (const g of ["point", "pinch", "drag", "fist", "spread", "twist", "palm", "swipe", "thumb"]) {
      const svg = gestureSvg(g);
      assert(svg.startsWith("<svg") && svg.includes(`data-anim="${g}"`) && (svg.match(/<line/g) || []).length >= 21, g);
    }
  },
};

function assert(ok, msg = "assertion failed") { if (!ok) throw new Error(msg); }

let passed = 0, failed = 0;
for (const [name, fn] of Object.entries(tests)) {
  try { fn(); console.log(`  PASS  ${name}`); passed++; }
  catch (err) { console.log(`  FAIL  ${name}: ${err.message}`); failed++; }
}
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
