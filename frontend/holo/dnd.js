// Drag and drop between hologram panels, driven by pointer.js -- the same
// for the mouse and a pinching hand (HTML5 drag-and-drop can't be driven by a
// hand, so it isn't used). A drag carries a payload like
// {kind:"file", path, name, mime} and lands on one of:
//   the Jarvis orb   -> ask about it
//   the trash zone   -> delete (after a confirmation)
//   a window         -> that window decides (onto a folder: not allowed)
//   anywhere else    -> "space": open it there (a 3D file loads on the stage)

export function createDnd({ root, targets, onDrop }) {
  let cur = null;  // { payload, ghost, hot }

  function hit(x, y) {
    for (const t of targets()) {
      if (!t.el || t.el.hidden) continue;
      const r = t.el.getBoundingClientRect();
      const pad = t.pad ?? 0;
      if (x >= r.left - pad && x <= r.right + pad && y >= r.top - pad && y <= r.bottom + pad) return t;
    }
    return null;
  }

  function setHot(t) {
    if (cur.hot === t) return;
    cur.hot?.el.classList.remove("drop-hot");
    cur.hot = t;
    t?.el.classList.add("drop-hot");
  }

  return {
    get active() { return !!cur; },
    start(payload, x, y, sourceEl) {
      const ghost = document.createElement("div");
      ghost.className = "ws-ghost";
      ghost.textContent = payload.name || payload.path || payload.kind;
      root.append(ghost);
      cur = { payload, ghost, hot: null, source: sourceEl };
      sourceEl?.classList.add("drag-source");
      root.classList.add("dragging-item");
      this.move(x, y);
    },
    move(x, y) {
      if (!cur) return;
      cur.ghost.style.transform = `translate(${x + 14}px, ${y + 10}px)`;
      const t = hit(x, y);
      setHot(t && (!t.accepts || t.accepts(cur.payload)) ? t : null);
    },
    end(x, y) {
      if (!cur) return;
      const { payload, hot } = cur;
      this.cancel();
      // The mouse still fires a click on release; it must not open the item too.
      const swallow = (e) => { e.stopPropagation(); e.preventDefault(); };
      addEventListener("click", swallow, { capture: true, once: true });
      setTimeout(() => removeEventListener("click", swallow, { capture: true }), 60);
      onDrop(payload, hot?.name ?? "space", { x, y, target: hot });
    },
    cancel() {
      if (!cur) return;
      cur.hot?.el.classList.remove("drop-hot");
      cur.source?.classList.remove("drag-source");
      cur.ghost.remove();
      root.classList.remove("dragging-item");
      cur = null;
    },
  };
}
