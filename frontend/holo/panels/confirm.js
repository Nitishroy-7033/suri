// "Move these to the Recycle Bin?" -- the only way a delete goes ahead.
// The server made the request (fs_delete, or a drop on the trash) and waits;
// Yes here (a click, Enter, or a thumbs up held half a second) or your
// spoken yes sends it on. No, Esc, or closing the window drops it.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const open = new Map();  // request id -> { key handler }

/** A drop on the trash, or the preview's Recycle Bin button. */
export async function requestDelete(ctx, paths) {
  try {
    const res = await ctx.api("/api/fs/trash", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paths }),
    });
    show(ctx, await res.json());
  } catch (err) {
    ctx.toast(`Can't delete that: ${err.detail || err.message}`, 6000);
  }
}

/** The server's fs_confirm event (or the POST's answer): show the question. */
export function show(ctx, { id, items = [] }) {
  if (!id || open.has(id)) return;
  const box = document.createElement("div");
  box.className = "ws-confirm";
  box.innerHTML = `
    <p class="ws-confirm-q">Move ${items.length === 1 ? "this" : `these ${items.length} items`} to the Recycle Bin?</p>
    <ul>${items.map((i) => `<li><b>${esc(i.name)}</b><small>${esc(i.path)}</small></li>`).join("")}</ul>
    <p class="ws-note">Say “yes”, show a thumbs up, or press Enter. You can restore it from the Recycle Bin later.</p>
    <div class="ws-confirm-btns">
      <button type="button" class="ws-btn" data-act="no">Keep it</button>
      <button type="button" class="ws-btn danger" data-act="yes">Move to Recycle Bin</button>
    </div>`;
  const answer = (ok) => {
    if (!open.has(id)) return;
    cleanup();
    ctx.send({ t: "holo_confirm", id, ok });
    ctx.wm.close(`confirm:${id}`);
  };
  const onKey = (e) => {
    if (e.key === "Enter") { e.preventDefault(); answer(true); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); answer(false); }
  };
  const cleanup = () => { removeEventListener("keydown", onKey, true); open.delete(id); };
  open.set(id, { cleanup });
  addEventListener("keydown", onKey, true);
  box.querySelector('[data-act="yes"]').onclick = () => answer(true);
  box.querySelector('[data-act="no"]').onclick = () => answer(false);
  const b = { w: innerWidth, h: innerHeight };
  const w = ctx.wm.open({
    id: `confirm:${id}`, kind: "confirm", title: "Confirm", content: box, w: 420, h: 300,
    onClose: () => { if (open.has(id)) { cleanup(); ctx.send({ t: "holo_confirm", id, ok: false }); } },
  });
  // Centre stage: this is the one thing waiting on you.
  w.x = (b.w - w.w) / 2; w.y = Math.max(80, (b.h - w.h) / 2 - 40);
  w.el.style.left = `${w.x}px`; w.el.style.top = `${w.y}px`;
  w.el.classList.add("ws-attention");
}

/** fs_confirm_done: it happened (or didn't). */
export function done(ctx, { id, ok, moved = [], failed = [], cancelled }) {
  const o = open.get(id);
  if (o) { o.cleanup(); ctx.wm.close(`confirm:${id}`); }
  if (cancelled) ctx.toast("Kept it: nothing was deleted.");
  else if (ok) ctx.toast(`Moved ${moved.length} item${moved.length === 1 ? "" : "s"} to the Recycle Bin.`);
  else ctx.toast(`${failed.length} item${failed.length === 1 ? "" : "s"} could not be moved: ${failed[0]?.error || "unknown error"}`, 7000);
  dispatchEvent(new Event("ws-fs-changed"));
  for (const p of moved) ctx.wm.close(`file:${p}`);
}
