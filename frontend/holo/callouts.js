import * as THREE from "three";

// Labels pinned to the model: a small panel off to the side with a thin line
// back to the part it names. Positions follow the projected 3D anchor every
// frame, so the label tracks the part as the model turns.

export function createCallouts(layer, stage) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("ws-lines");
  layer.append(svg);
  const items = new Map();  // key -> { el, line, dot, anchor, side }
  const tmp = new THREE.Vector3();

  function upsert(key, { title, lines = [], anchor, side = "right" }) {
    let c = items.get(key);
    if (!c) {
      const el = document.createElement("div");
      el.className = "ws-callout";
      layer.append(el);
      const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("r", "3.5");
      svg.append(line, dot);
      c = { el, line, dot };
      items.set(key, c);
    }
    c.anchor = anchor;  // () => Vector3 in world space, or null
    c.side = side;
    c.el.innerHTML = `<b></b>${lines.map(() => "<span></span>").join("")}`;
    c.el.querySelector("b").textContent = title;
    c.el.querySelectorAll("span").forEach((s, i) => { s.textContent = lines[i]; });
    return c;
  }

  function remove(key) {
    const c = items.get(key);
    if (!c) return;
    c.el.remove(); c.line.remove(); c.dot.remove();
    items.delete(key);
  }

  const off = stage.onFrame(() => {
    const W = layer.clientWidth, H = layer.clientHeight;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    for (const c of items.values()) {
      const world = c.anchor?.(tmp);
      if (!world) { c.el.hidden = true; c.line.setAttribute("points", ""); continue; }
      const p = stage.project(world);
      c.el.hidden = !p.visible;
      if (!p.visible) continue;
      const lr = layer.getBoundingClientRect();
      const ax = p.x - lr.left, ay = p.y - lr.top;
      const right = c.side === "right";
      const bx = right ? Math.min(W - 250, ax + 120) : Math.max(20, ax - 330);
      const by = Math.max(70, Math.min(H - 110, ay - 70));
      c.el.style.transform = `translate(${bx}px, ${by}px)`;
      const ex = right ? bx : bx + c.el.offsetWidth, ey = by + 14;
      const kx = right ? ex - 26 : ex + 26;
      c.line.setAttribute("points", `${ax},${ay} ${kx},${ey} ${ex},${ey}`);
      c.dot.setAttribute("cx", ax); c.dot.setAttribute("cy", ay);
    }
  });

  return {
    upsert, remove,
    clear() { for (const k of [...items.keys()]) remove(k); },
    dispose() { off(); this.clear(); svg.remove(); },
  };
}
