import { BONES } from "./gestures.js";

// Hand pictures for the guide and the tutorial, drawn the way the workshop
// draws your real hand: MediaPipe's 21 joints and the bones between them.
// Each pose is hand-placed in a 100 x 120 box (a right hand, palm to the
// camera, as it looks on screen); [x, y] per joint, in landmark order:
//   0 wrist · 1-4 thumb · 5-8 index · 9-12 middle · 13-16 ring · 17-20 pinky

const PALM = { 0: [50, 112], 5: [33, 62], 9: [47, 58], 13: [61, 61], 17: [73, 67] };
const UP = {  // fingers straight
  6: [31, 43], 7: [30, 31], 8: [29, 21],
  10: [47, 36], 11: [47, 23], 12: [47, 12],
  14: [62, 40], 15: [63, 28], 16: [64, 19],
  18: [76, 51], 19: [78, 42], 20: [79, 34],
};
const CURLED = {  // fingers folded into the palm
  6: [32, 48], 7: [37, 57], 8: [39, 66],
  10: [47, 44], 11: [51, 54], 12: [52, 63],
  14: [61, 48], 15: [64, 57], 16: [64, 65],
  18: [73, 55], 19: [75, 62], 20: [74, 69],
};
const THUMB_OUT = { 1: [38, 98], 2: [26, 87], 3: [17, 75], 4: [10, 64] };
const THUMB_IN = { 1: [38, 98], 2: [31, 86], 3: [35, 76], 4: [41, 71] };
const THUMB_UP = { 1: [38, 98], 2: [29, 80], 3: [25, 62], 4: [23, 45] };

const pick = (src, ids) => Object.fromEntries(ids.map((i) => [i, src[i]]));
const INDEX = [6, 7, 8], OTHERS = [10, 11, 12, 14, 15, 16, 18, 19, 20];

export const POSES = {
  open: { ...PALM, ...UP, ...THUMB_OUT },
  point: { ...PALM, ...pick(UP, INDEX), ...pick(CURLED, OTHERS), ...THUMB_IN },
  // Index bends down to meet the thumb; the rest stay relaxed.
  pinch: { ...PALM, ...pick(UP, OTHERS), 6: [30, 45], 7: [24, 44], 8: [19, 49], 1: [38, 98], 2: [27, 84], 3: [20, 68], 4: [18, 51] },
  fist: { ...PALM, ...CURLED, ...THUMB_IN },
  thumb: { ...PALM, ...CURLED, ...THUMB_UP },
};

/** Where the cursor sits for a pose: the fingertip, or where pinch meets. */
export function tipOf(pose) {
  if (pose === "pinch") return [18.5, 50];
  if (pose === "thumb") return [23, 45];
  if (pose === "fist") return [50, 72];
  return POSES[pose][8];
}

/** One hand as SVG markup. `flip` mirrors it into a left hand. */
export function handSvg(pose, { flip = false, cls = "", ring = true } = {}) {
  const pts = POSES[pose] || POSES.open;
  const P = (i) => pts[i];
  const bones = BONES.map(([a, b]) =>
    `<line x1="${P(a)[0]}" y1="${P(a)[1]}" x2="${P(b)[0]}" y2="${P(b)[1]}"/>`).join("");
  const joints = Object.entries(pts).map(([i, [x, y]]) =>
    `<circle cx="${x}" cy="${y}" r="${[4, 8, 12, 16, 20].includes(+i) ? 2.6 : 2}"/>`).join("");
  const [tx, ty] = tipOf(pose);
  const tip = ring ? `<circle class="tip${pose === "pinch" || pose === "fist" ? " grab" : ""}" cx="${tx}" cy="${ty}" r="7"/>` : "";
  return `<g class="hand ${cls}"${flip ? ` transform="translate(100 0) scale(-1 1)"` : ""}>
    <g class="bones">${bones}</g><g class="joints">${joints}</g>${tip}</g>`;
}

/**
 * A gesture picture for the guide: one or two hands plus motion marks, in a
 * <svg> the CSS animates by its data-anim (holo.css, "gesture pictures").
 */
export function gestureSvg(kind) {
  const one = (pose, extra = "") => `<svg class="ws-gfx" data-anim="${kind}" viewBox="-20 -10 140 140" aria-hidden="true">${extra}${handSvg(pose)}</svg>`;
  const two = (pose, extra = "") => `<svg class="ws-gfx" data-anim="${kind}" viewBox="-70 -10 240 140" aria-hidden="true">${extra}
    <g class="left">${handSvg(pose, { flip: true })}</g><g class="right" transform="translate(100 0)">${handSvg(pose)}</g></svg>`;
  const arrow = (d) => `<path class="motion" d="${d}"/>`;
  switch (kind) {
    case "point": return one("point", `<circle class="dwell" cx="29" cy="21" r="12"/>`);
    case "pinch": return one("pinch");
    case "drag": return one("pinch", arrow("M-12 30 h-8 M112 30 h8") + arrow("M-14 26 l-6 4 6 4 M114 26 l6 4 -6 4"));
    case "fist": return one("fist", arrow("M50 -4 v-4 M50 128 v4 M-14 70 h-4 M114 70 h4"));
    case "spread": return two("pinch", arrow("M20 20 h-24 M-4 16 l-6 4 6 4 M80 20 h24 M104 16 l6 4 -6 4"));
    case "twist": return two("pinch", `<path class="motion" d="M10 -2 A60 60 0 0 1 90 -2 M84 -8 l6 6 -8 3"/>`);
    case "palm": return one("open", `<circle class="burst" cx="47" cy="70" r="30"/>`);
    case "swipe": return one("open", arrow("M112 40 h10 M112 60 h16 M112 80 h10"));
    case "thumb": return one("thumb", `<circle class="hold" cx="23" cy="45" r="13"/>`);
    default: return one("open");
  }
}
