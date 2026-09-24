// The forge's progress, as a small hologram window: what is being built,
// by which service, how far along. It fills in from forge_start /
// forge_progress / forge_done events; when the model is ready it goes on the
// projector by itself (the server sends holo_show) and this says so.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function update(ctx, kind, d) {
  let w = ctx.wm.get("forge");
  if (!w) {
    const box = document.createElement("div");
    box.className = "ws-forge";
    box.innerHTML = `<p class="ws-forge-what"></p><div class="ws-forge-bar"><u></u></div><p class="ws-forge-note"></p>
      <div class="ws-forge-btns"><button type="button" class="ws-btn" data-act="show" hidden>Show it</button>
      <button type="button" class="ws-btn danger" data-act="cancel">Cancel</button></div>`;
    w = ctx.wm.open({ id: "forge", kind: "forge", title: "Forge", content: box, w: 340, h: 200 });
    box.addEventListener("click", (e) => {
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act === "cancel") ctx.ask("Cancel building the model.");
      else if (act === "show" && box.entry) ctx.showEntry(box.entry);
    });
  }
  const box = w.content;
  box.dataset.state = d.state;
  box.querySelector(".ws-forge-what").innerHTML = `<b>${esc(d.prompt)}</b><small>${esc(d.provider || "")}</small>`;
  box.querySelector(".ws-forge-bar u").style.width = `${Math.round((d.state === "done" ? 1 : d.progress || 0.03) * 100)}%`;
  const note = d.state === "done" ? "Ready: it's on the projector"
    : d.state === "failed" ? `Failed: ${d.error || "no generator worked"}`
    : d.state === "cancelled" ? "Cancelled"
    : `${d.note || "working"} · ${d.seconds ?? 0}s`;
  box.querySelector(".ws-forge-note").textContent = note;
  box.querySelector('[data-act="cancel"]').hidden = d.state !== "working";
  const show = box.querySelector('[data-act="show"]');
  show.hidden = !(d.state === "done" && d.entry);
  box.entry = d.entry || null;
  if (kind === "forge_done" && d.state === "done") ctx.toast(`Built “${d.entry?.name || d.prompt}”`);
}
