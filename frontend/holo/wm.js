// Floating hologram windows: move, resize, focus, minimise to the dock,
// close, tidy into a grid. Windows are plain HTML (sharp text), tilted a
// little on a curved wall so the ones at the sides face you.
//
// A window either owns fresh content or *adopts* an element that already
// exists elsewhere on the page -- the chat, the settings drawer, the Systems
// gauges -- and gives it back when closed, so those keep working exactly as
// they do outside the workshop.
//
// All moving goes through drag handles returned by begin(); the pointer
// layer (pointer.js) calls them for the mouse and for hands alike.

const LAYOUT_KEY = "jarvis-holo-layout";
const MIN_W = 240, MIN_H = 150;

export function createWM({ layer, dock, onChange }) {
  const wins = new Map();  // id -> win
  let z = 10;
  let curve = true;
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}"); } catch {}
  const persist = () => {
    for (const w of wins.values()) saved[w.id] = { x: w.x, y: w.y, w: w.w, h: w.h };
    try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(saved)); } catch {}
  };
  const changed = () => onChange?.();

  function bounds() {
    const r = layer.getBoundingClientRect();
    return { w: r.width || innerWidth, h: r.height || innerHeight };
  }

  function place(w) {
    const b = bounds();
    w.w = Math.max(MIN_W, Math.min(w.w, b.w - 16));
    w.h = Math.max(MIN_H, Math.min(w.h, b.h - 70));
    w.x = Math.max(8 - w.w * 0.6, Math.min(w.x, b.w - w.w * 0.4));
    w.y = Math.max(52, Math.min(w.y, b.h - 60));
    const el = w.el;
    el.style.left = `${w.x}px`; el.style.top = `${w.y}px`;
    el.style.width = `${w.w}px`; el.style.height = `${w.h}px`;
    // Curved wall: a window left of centre turns right to face you, and so on.
    const off = (w.x + w.w / 2) / b.w - 0.5;
    el.style.setProperty("--tilt", curve ? `${(-off * 16).toFixed(2)}deg` : "0deg");
  }

  function spot(wantW, wantH) {
    // Cascade new windows from the upper left, clear of the model in the middle.
    const b = bounds(), n = [...wins.values()].filter((w) => !w.min).length;
    const left = n % 2 === 0;
    return { x: left ? 24 + (n >> 1) * 28 : b.w - wantW - 24 - (n >> 1) * 28, y: 70 + (n >> 1) * 34 };
  }

  function focus(id) {
    const w = wins.get(id);
    if (!w) return;
    if (w.min) restore(id);
    w.el.style.zIndex = String(++z);
    for (const o of wins.values()) o.el.classList.toggle("focused", o === w);
    changed();
  }

  /**
   * { id, kind, title, content (Element), adopt (bool), w, h, onClose, path }
   * Opening an id that exists just brings it forward.
   */
  function open(spec) {
    if (wins.has(spec.id)) { focus(spec.id); return wins.get(spec.id); }
    const el = document.createElement("section");
    el.className = "ws-win";
    el.dataset.id = spec.id;
    el.dataset.kind = spec.kind || "panel";
    el.innerHTML = `
      <header class="ws-win-bar" data-zone="bar">
        <span class="ws-win-title"></span><span class="grow"></span>
        <button class="ws-win-btn" data-act="min" title="Minimise" aria-label="Minimise">&#x2013;</button>
        <button class="ws-win-btn" data-act="close" title="Close" aria-label="Close">&#x2715;</button>
      </header>
      <div class="ws-win-body"></div>
      <i class="ws-win-resize" data-zone="resize" aria-hidden="true"></i>
      <i class="ws-corner tl"></i><i class="ws-corner tr"></i><i class="ws-corner bl"></i><i class="ws-corner br"></i>`;
    el.querySelector(".ws-win-title").textContent = spec.title || spec.id;
    const body = el.querySelector(".ws-win-body");
    let home = null;
    if (spec.content) {
      if (spec.adopt) home = { parent: spec.content.parentNode, next: spec.content.nextSibling };
      body.append(spec.content);
    }
    const s = saved[spec.id];
    const w = {
      id: spec.id, kind: spec.kind || "panel", title: spec.title || spec.id, path: spec.path || null,
      el, body, home, content: spec.content, onClose: spec.onClose, onSwipe: spec.onSwipe, min: false,
      w: s?.w ?? spec.w ?? 380, h: s?.h ?? spec.h ?? 420, x: 0, y: 0,
    };
    Object.assign(w, s ? { x: s.x, y: s.y } : spot(w.w, w.h));
    el.querySelector('[data-act="close"]').onclick = () => close(w.id);
    el.querySelector('[data-act="min"]').onclick = () => minimise(w.id);
    el.addEventListener("pointerdown", () => focus(w.id), true);
    layer.append(el);
    wins.set(w.id, w);
    place(w);
    el.classList.add("opening");
    setTimeout(() => el.classList.remove("opening"), 400);
    focus(w.id);
    spec.onOpen?.(w);
    return w;
  }

  function close(id) {
    const w = wins.get(id);
    if (!w) return;
    persist();
    wins.delete(id);
    w.dockChip?.remove();
    if (w.home && w.content) {
      // Back where it came from, as if the workshop had never borrowed it.
      w.home.parent.insertBefore(w.content, w.home.next && w.home.next.parentNode === w.home.parent ? w.home.next : null);
    }
    w.el.classList.add("closing");
    setTimeout(() => w.el.remove(), 220);
    w.onClose?.(w);
    changed();
  }

  function minimise(id) {
    const w = wins.get(id);
    if (!w || w.min) return;
    w.min = true;
    w.el.hidden = true;
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "ws-dock-chip";
    chip.textContent = w.title;
    chip.onclick = () => restore(id);
    dock.append(chip);
    w.dockChip = chip;
    changed();
  }

  function restore(id) {
    const w = wins.get(id);
    if (!w || !w.min) return;
    w.min = false;
    w.el.hidden = false;
    w.dockChip?.remove();
    w.dockChip = null;
    focus(id);
  }

  /** Tidy every open window into a grid around the model. */
  function arrange() {
    const list = [...wins.values()].filter((w) => !w.min);
    if (!list.length) return;
    const b = bounds();
    const cols = list.length <= 2 ? list.length : Math.ceil(Math.sqrt(list.length));
    const rows = Math.ceil(list.length / cols);
    const gap = 16, top = 64, cw = (b.w - gap * (cols + 1)) / cols, ch = (b.h - top - 80 - gap * rows) / rows;
    list.forEach((w, i) => {
      w.w = Math.min(cw, 560); w.h = Math.min(ch, 520);
      w.x = gap + (i % cols) * (cw + gap) + (cw - w.w) / 2;
      w.y = top + Math.floor(i / cols) * (ch + gap);
      place(w);
    });
    persist();
    changed();
  }

  /**
   * Start moving or resizing a window from a point on it. Returns
   * { move(x, y), end() } or null if that point is not a handle.
   */
  function begin(el, x, y) {
    const winEl = el.closest?.(".ws-win");
    if (!winEl) return null;
    const w = wins.get(winEl.dataset.id);
    if (!w) return null;
    const zone = el.closest("[data-zone]")?.dataset.zone;
    if (!zone || el.closest("button")) return null;
    focus(w.id);
    const start = { x, y, wx: w.x, wy: w.y, ww: w.w, wh: w.h };
    w.el.classList.add("dragging");
    return {
      win: w,
      move(nx, ny) {
        if (zone === "bar") { w.x = start.wx + nx - start.x; w.y = start.wy + ny - start.y; }
        else { w.w = start.ww + nx - start.x; w.h = start.wh + ny - start.y; }
        place(w);
      },
      end() { w.el.classList.remove("dragging"); persist(); changed(); },
    };
  }

  /** Two-hand resize: scale a window about its centre. */
  function scaleFrom(w, base, f) {
    const cx = base.x + base.w / 2, cy = base.y + base.h / 2;
    w.w = base.w * f; w.h = base.h * f;
    w.x = cx - w.w / 2; w.y = cy - w.h / 2;
    place(w);
  }

  function find(query) {
    const q = String(query || "").toLowerCase().trim();
    if (!q) return null;
    const list = [...wins.values()];
    return list.find((w) => w.id === q || w.kind === q) ||
      list.find((w) => w.title.toLowerCase().includes(q) || (w.path || "").toLowerCase().includes(q)) || null;
  }

  addEventListener("resize", () => { for (const w of wins.values()) place(w); });

  return {
    open, close, focus, minimise, restore, arrange, begin, scaleFrom, find,
    get: (id) => wins.get(id),
    has: (id) => wins.has(id),
    list: () => [...wins.values()],
    closeAll() { for (const id of [...wins.keys()]) close(id); },
    setCurve(on) { curve = !!on; for (const w of wins.values()) place(w); },
    focused: () => [...wins.values()].find((w) => w.el.classList.contains("focused") && !w.min) || null,
    setTitle(id, title) { const w = wins.get(id); if (w) { w.title = title; w.el.querySelector(".ws-win-title").textContent = title; } },
  };
}
