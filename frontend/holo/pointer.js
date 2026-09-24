// One pointer model for the mouse and both hands.
//
// Whatever is under a pointer when it goes down decides what a drag does:
//   window title bar / corner  -> move / resize the window  (wm.begin)
//   a [data-drag] item          -> drag and drop it          (dnd)
//   a slider                    -> set it                    (hands only)
//   the stage                   -> turn the model (fist: move it)
//   anything else               -> on release, press it      (hands only)
// Two hands down on the stage scale and roll the model; two hands on one
// window resize it. The mouse keeps its native clicks, so only drags are
// handled for it here; a hand has no native anything, so this presses
// buttons, flips switches and opens selects for it.

const DRAG_START_PX = 8;
const DWELL_MS = 800;
const ROT_PER_PX = 0.0085;

export function createPointers({ root, wm, stage, dnd, isStage, onSelect, onHover, onActivate }) {
  const hands = new Map();  // id -> pointer state
  let two = null;           // an active two-hand gesture
  let hoverEl = null, hoverPart = null;

  function under(x, y) {
    return document.elementFromPoint(x, y);
  }

  function dragPayload(el) {
    const src = el?.closest?.("[data-drag]");
    if (!src) return null;
    try { return { el: src, data: JSON.parse(src.dataset.drag) }; } catch { return null; }
  }

  // ---------------- pressing things for a hand ----------------

  function activate(el) {
    if (!el) return;
    const range = el.closest?.('input[type="range"]');
    if (range) return;  // handled while dragging
    const select = el.closest?.("select");
    if (select) { openPicker(select); return; }
    const field = el.closest?.('input:not([type="checkbox"]):not([type="radio"]):not([type="range"]), textarea');
    if (field) { field.focus(); return; }
    const target = el.closest?.('button, a[href], label, summary, [role="button"], [role="radio"], [role="tab"], input, [data-act], .chip');
    if (target && !target.disabled) { target.click(); onActivate?.(target); }
  }

  // A hand cannot open a native <select>, so show its options as big tiles.
  function openPicker(select) {
    closePicker();
    const r = select.getBoundingClientRect();
    const box = document.createElement("div");
    box.className = "ws-picker";
    box.style.left = `${Math.max(8, r.left)}px`;
    box.style.top = `${Math.min(innerHeight - 40, r.bottom + 6)}px`;
    for (const o of select.options) {
      if (o.disabled) continue;
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = o.textContent;
      b.classList.toggle("on", o.value === select.value);
      b.onclick = () => {
        select.value = o.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        closePicker();
      };
      box.append(b);
    }
    root.append(box);
    setTimeout(() => addEventListener("pointerdown", closeOutside, true), 0);
  }
  function closeOutside(e) { if (!e.target.closest?.(".ws-picker")) closePicker(); }
  function closePicker() {
    root.querySelector(".ws-picker")?.remove();
    removeEventListener("pointerdown", closeOutside, true);
  }

  function setRange(range, x) {
    const r = range.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (x - r.left) / r.width));
    const min = Number(range.min || 0), max = Number(range.max || 100), step = Number(range.step || 1) || 1;
    const v = Math.round((min + f * (max - min)) / step) * step;
    if (String(v) !== range.value) {
      range.value = String(v);
      range.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  // ---------------- shared drag logic ----------------

  function down(p, x, y, { virtual, fist = false, button = 0 }) {
    p.down = true; p.x0 = p.x = x; p.y0 = p.y = y; p.moved = false; p.t0 = performance.now();
    p.vx = p.vy = 0; p.lastT = p.t0;
    const el = under(x, y);
    p.el = el;
    const win = wm.begin(el, x, y);
    if (win) { p.mode = "win"; p.drag = win; return true; }
    const item = dragPayload(el);
    if (item) { p.mode = "item"; p.item = item; return true; }
    const range = virtual && el?.closest?.('input[type="range"]');
    if (range) { p.mode = "range"; p.range = range; setRange(range, x); return true; }
    if (isStage(el)) { p.mode = fist || button === 2 ? "move" : "turn"; return true; }
    p.mode = "press";
    return false;
  }

  function move(p, x, y) {
    const dx = x - p.x, dy = y - p.y;
    const now = performance.now(), dt = Math.max(1, now - p.lastT);
    p.x = x; p.y = y; p.lastT = now;
    if (!p.moved && Math.hypot(x - p.x0, y - p.y0) > DRAG_START_PX) p.moved = true;
    if (two) return;
    if (p.mode === "win") p.drag.move(x, y);
    else if (p.mode === "turn") {
      stage.rotateBy(dx * ROT_PER_PX, dy * ROT_PER_PX);
      p.vx = 0.7 * p.vx + 0.3 * (dx * ROT_PER_PX * 1000 / dt);
      p.vy = 0.7 * p.vy + 0.3 * (dy * ROT_PER_PX * 1000 / dt);
    } else if (p.mode === "move") stage.moveBy(dx, dy);
    else if (p.mode === "range") setRange(p.range, x);
    else if (p.mode === "item" && p.moved) {
      if (!dnd.active) dnd.start(p.item.data, x, y, p.item.el);
      dnd.move(x, y);
    }
  }

  function up(p, x, y, { virtual }) {
    const mode = p.mode;
    p.down = false; p.mode = null;
    if (mode === "win") p.drag.end();
    else if (mode === "turn") {
      if (p.moved && performance.now() - p.lastT < 80) stage.fling(p.vx, p.vy);
      else if (!p.moved) onSelect?.(stage.pick(x, y)?.part ?? null, { x, y });
    } else if (mode === "item") {
      if (dnd.active) dnd.end(x, y);
      else if (virtual) activate(p.item.el);
    } else if (mode === "press" && virtual && !p.moved) activate(p.el);
  }

  // ---------------- two hands ----------------

  function startTwo() {
    const [a, b] = [...hands.values()].filter((h) => h.down);
    if (!a || !b) return;
    const winA = a.el?.closest?.(".ws-win"), winB = b.el?.closest?.(".ws-win");
    if (winA && winA === winB) {
      const w = wm.get(winA.dataset.id);
      if (!w) return;
      two = { kind: "win", a, b, win: w, base: { x: w.x, y: w.y, w: w.w, h: w.h }, d0: dist(a, b) };
    } else if (isStage(a.el) && isStage(b.el)) {
      two = { kind: "stage", a, b, d: dist(a, b), ang: angle(a, b) };
    }
  }
  function moveTwo() {
    const { a, b } = two;
    if (two.kind === "win") {
      wm.scaleFrom(two.win, two.base, Math.max(0.4, dist(a, b) / Math.max(20, two.d0)));
      return;
    }
    const d = dist(a, b), ang = angle(a, b);
    if (two.d > 20) stage.scaleBy(d / two.d);
    let da = ang - two.ang;
    if (da > Math.PI) da -= 2 * Math.PI; else if (da < -Math.PI) da += 2 * Math.PI;
    stage.rollBy(-da);
    two.d = d; two.ang = ang;
  }
  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  const angle = (a, b) => Math.atan2(b.y - a.y, b.x - a.x);

  // ---------------- hover and dwell (hands) ----------------

  function hover(p, x, y, pointing) {
    const el = under(x, y);
    const target = el?.closest?.('button, a[href], label, [role="button"], [role="radio"], input, select, textarea, [data-drag], .ws-win-bar, .chip, [data-act]');
    if (target !== hoverEl) {
      hoverEl?.classList.remove("hand-hover");
      hoverEl = target;
      hoverEl?.classList.add("hand-hover");
    }
    // Raycasting a big model every camera frame costs; 15 times a second is plenty.
    let part = p.lastPart ?? null;
    if (!isStage(el)) part = null;
    else if (!p.lastPick || performance.now() - p.lastPick > 66) {
      part = stage.pick(x, y)?.part ?? null;
      p.lastPick = performance.now();
    }
    p.lastPart = part;
    const item = dragPayload(target)?.data ?? null;
    const key0 = item?.path ?? part;
    if (key0 !== hoverPart) { hoverPart = key0; onHover?.(part, item); }

    // Pointing and holding still selects what is under the finger.
    const key = part ? `part:${part}` : target ? target : null;
    if (!pointing || !key) { p.dwell = null; return; }
    if (!p.dwell || p.dwell.key !== key) { p.dwell = { key, t: performance.now(), done: false }; return; }
    if (!p.dwell.done && performance.now() - p.dwell.t > DWELL_MS) {
      p.dwell.done = true;
      if (part) onSelect?.(part, { x, y, dwell: true });
      else if (target?.closest?.("[data-drag]")) activate(target);
    }
  }
  const dwellProgress = (p) => (p?.dwell && !p.dwell.done ? Math.min(1, (performance.now() - p.dwell.t) / DWELL_MS) : 0);

  // ---------------- the mouse ----------------

  let mouse = null;
  root.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "touch" && !e.isPrimary) return;
    if (e.button !== 0 && e.button !== 2) return;
    const p = { id: "mouse" };
    if (down(p, e.clientX, e.clientY, { virtual: false, button: e.button })) {
      if (p.mode !== "press") {
        mouse = p;
        // Not for items: with capture, Chrome sends the click to the root,
        // and a tap on a file would never open it. The root spans the whole
        // screen, so it sees the moves anyway.
        if (p.mode !== "item") {
          root.setPointerCapture?.(e.pointerId);
          e.preventDefault();
        }
      }
    }
  });
  root.addEventListener("pointermove", (e) => { if (mouse) move(mouse, e.clientX, e.clientY); });
  const mouseUp = (e) => { if (mouse) { up(mouse, e.clientX, e.clientY, { virtual: false }); mouse = null; } };
  root.addEventListener("pointerup", mouseUp);
  root.addEventListener("pointercancel", mouseUp);
  root.addEventListener("contextmenu", (e) => { if (isStage(e.target)) e.preventDefault(); });
  root.addEventListener("wheel", (e) => {
    if (!isStage(e.target)) return;
    e.preventDefault();
    stage.zoom(Math.pow(1.0015, -e.deltaY));
  }, { passive: false });
  root.addEventListener("dblclick", (e) => { if (isStage(e.target)) stage.reset(); });

  // ---------------- hands ----------------

  return {
    /**
     * Called once per tracking frame for each hand the camera sees.
     * { x, y } screen pixels (mirrored already), pinch, fist, pointing, visible.
     */
    hand(id, s) {
      let p = hands.get(id);
      if (!s.visible) {
        if (p?.down) up(p, p.x, p.y, { virtual: true });
        if (p) hands.delete(id);
        if (two && (two.a === p || two.b === p)) two = null;
        if (!hands.size) { hoverEl?.classList.remove("hand-hover"); hoverEl = null; }
        return;
      }
      if (!p) { p = { id, down: false, x: s.x, y: s.y }; hands.set(id, p); }
      const grab = s.pinch || s.fist;
      if (grab && !p.down) {
        down(p, s.x, s.y, { virtual: true, fist: s.fist && !s.pinch });
        if ([...hands.values()].filter((h) => h.down).length === 2) startTwo();
      } else if (!grab && p.down) {
        if (two && (two.a === p || two.b === p)) two = null;
        up(p, s.x, s.y, { virtual: true });
      } else if (p.down) {
        move(p, s.x, s.y);
        if (two) moveTwo();
      } else {
        p.x = s.x; p.y = s.y;
        hover(p, s.x, s.y, s.pointing);
      }
    },
    /** Open palm: let go of everything. */
    releaseAll() {
      for (const p of hands.values()) if (p.down) up(p, p.x, p.y, { virtual: true });
      two = null;
      if (dnd.active) dnd.cancel();
    },
    dwell: (id) => dwellProgress(hands.get(id)),
    isDown: (id) => !!hands.get(id)?.down,
    closePicker,
  };
}
