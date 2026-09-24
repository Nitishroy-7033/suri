// The hands tutorial's steps and how each one knows you did it. Pure: it is
// fed what hands.js already produces -- per-frame hand states, gesture
// events, part selections -- so tests can drive it without a camera.
//
// A step's detector returns progress 0..1; reaching 1 completes it.

const HOLD = (ms) => (s, now, on) => {
  if (!on) { s.since = null; return 0; }
  s.since ??= now;
  return Math.min(1, (now - s.since) / ms);
};

const seen = (hands) => hands.filter((h) => h.visible && h.tracked !== false);

export const STEPS = [
  {
    id: "see", title: "Show your hand",
    say: "Hold one hand up, palm towards the camera, about an arm's length away. Its skeleton appears on screen when the camera sees it.",
    gfx: "palm",
    frame: (s, hands, now) => HOLD(700)(s, now, seen(hands).length > 0),
  },
  {
    id: "point", title: "Point to aim",
    say: "Point with just your index finger. The ring on screen follows your fingertip: that's your cursor.",
    gfx: "point",
    frame: (s, hands, now) => HOLD(700)(s, now, seen(hands).some((h) => h.pointing)),
  },
  {
    id: "pinch", title: "Pinch to grab",
    say: "Over the hologram, touch your thumb and index finger together, then let go. A pinch is a click: it presses buttons and grabs things.",
    gfx: "pinch",
    frame: (s, hands) => {
      const pinching = seen(hands).some((h) => h.pinch);
      if (pinching) s.pinched = true;
      return s.pinched ? (pinching ? 0.6 : 1) : 0;
    },
  },
  {
    id: "drag", title: "Pinch and drag to turn",
    say: "Pinch over the hologram and move your hand sideways: the model turns with you. Let go mid-move to give it a spin.",
    gfx: "drag",
    frame: (s, hands) => {
      const h = seen(hands).find((x) => x.pinch);
      if (!h) { s.from = null; return s.best || 0; }
      s.from ??= [h.x, h.y];
      s.best = Math.max(s.best || 0, Math.min(1, Math.hypot(h.x - s.from[0], h.y - s.from[1]) / 220));
      return s.best;
    },
  },
  {
    id: "zoom", title: "Two hands to zoom",
    say: "Pinch with both hands and pull them apart to make the model bigger, or together to shrink it. Turn the line between your hands to roll it.",
    gfx: "spread",
    frame: (s, hands) => {
      const both = seen(hands).filter((h) => h.pinch);
      if (both.length < 2) { s.d0 = null; return s.best || 0; }
      const d = Math.hypot(both[0].x - both[1].x, both[0].y - both[1].y);
      s.d0 ??= Math.max(d, 1);
      s.best = Math.max(s.best || 0, Math.min(1, Math.abs(d / s.d0 - 1) / 0.35));
      return s.best;
    },
  },
  {
    id: "fist", title: "Fist to move",
    say: "Make a fist and move it: the model slides across the room with your hand.",
    gfx: "fist",
    frame: (s, hands) => {
      const h = seen(hands).find((x) => x.fist);
      if (!h) { s.from = null; return s.best || 0; }
      s.from ??= [h.x, h.y];
      s.best = Math.max(s.best || 0, Math.min(1, Math.hypot(h.x - s.from[0], h.y - s.from[1]) / 150));
      return s.best;
    },
  },
  {
    id: "palm", title: "Open palm to let go",
    say: "Open your hand wide, all five fingers. That lets go of whatever you're holding and stops the spin.",
    gfx: "palm",
    gesture: (s, name) => (name === "palm" ? 1 : null),
    frame: (s, hands, now) => HOLD(500)(s, now, seen(hands).some((h) => h.palm)),
  },
  {
    id: "swipe", title: "Swipe for the next model",
    say: "With an open hand, sweep quickly left or right across the camera. Left brings the next model, right the one before. In a folder window it turns the page.",
    gfx: "swipe",
    gesture: (s, name) => (name === "swipe_left" || name === "swipe_right" ? 1 : null),
  },
  {
    id: "select", title: "Point and hold to select",
    say: "Point at one part of the model and hold still. The ring fills up, and after a second the part lights up with its name. Then ask: “Jarvis, what's this?”",
    gfx: "point",
    select: (s, part, info) => (part ? (info?.dwell ? 1 : 0.7) : null),
    frame: (s, hands) => (s.touched ? 0.7 : 0),
  },
  {
    id: "thumb", title: "Thumbs up to confirm",
    say: "Give a thumbs up and hold it for half a second. That's how you say yes to a question on screen, like moving a file to the Recycle Bin.",
    gfx: "thumb",
    gesture: (s, name) => (name === "confirm" ? 1 : null),
    frame: (s, hands) => Math.max(0, ...seen(hands).map((h) => h.thumbHeld || 0)) * 0.95,
  },
];

/** Walks through the steps: feed it frames and events, read its progress. */
export function createCoach(steps = STEPS) {
  let i = 0, scratch = {}, progress = 0, finished = false;
  const done = () => progress >= 1;

  function bump(p) {
    if (p == null || finished) return;
    progress = Math.max(progress, p);
  }

  return {
    get index() { return i; },
    get step() { return steps[i]; },
    get total() { return steps.length; },
    get progress() { return progress; },
    get done() { return done(); },
    get finished() { return finished; },
    frame(hands, now) { if (!done()) bump(steps[i].frame?.(scratch, hands, now) ?? null); return done(); },
    gesture(name) { if (!done()) bump(steps[i].gesture?.(scratch, name) ?? null); return done(); },
    select(part, info) {
      if (done()) return true;
      const p = steps[i].select?.(scratch, part, info);
      if (p != null) { scratch.touched = true; bump(p); }
      return done();
    },
    next() {
      if (i >= steps.length - 1) { finished = true; return false; }
      i++; scratch = {}; progress = 0;
      return true;
    },
    back() { if (i > 0) { i--; scratch = {}; progress = 0; } },
    reset() { i = 0; scratch = {}; progress = 0; finished = false; },
  };
}
