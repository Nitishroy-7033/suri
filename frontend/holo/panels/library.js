// The model library as a hologram window: your models first, then the
// samples. Tap one to put it on the projector, or drag it onto the stage.
// A model you dropped from your computer can be saved here, so "show my
// drone" works next time.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export async function openLibrary(ctx) {
  const box = document.createElement("div");
  box.className = "ws-library";
  const w = ctx.wm.open({ id: "library", kind: "library", title: "Model library", content: box, w: 340, h: 480 });
  if (w.content !== box) return;  // already open: it was just brought forward

  async function render() {
    const lib = await ctx.loadLibrary();
    const cur = ctx.view.entry;
    const unsaved = ctx.view.file;
    box.innerHTML = `
      ${unsaved ? `<div class="ws-lib-save"><span>“${esc(cur?.name)}” isn't in the library yet</span>
        <button type="button" class="ws-btn" data-act="save">Save it</button></div>` : ""}
      <ul class="ws-lib-list">${lib.map((m) => `
        <li><button type="button" class="ws-lib-item${cur?.id === m.id ? " on" : ""}" data-model="${esc(m.id)}"
             data-drag='${esc(JSON.stringify({ kind: "model", id: m.id, name: m.name || m.id }))}'>
          <b>${esc(m.name || m.id)}</b>
          <small>${esc(m.credit || "")}${m.license ? ` · ${esc(m.license)}` : ""}</small>
        </button></li>`).join("")}
      </ul>
      <p class="ws-note">Say “Jarvis, show the ${esc(lib[0]?.aliases?.[0] || "engine")}”, or drop a .glb, .stl or .obj file anywhere.</p>`;
  }

  box.addEventListener("click", async (e) => {
    const item = e.target.closest("[data-model]");
    if (item) {
      const entry = ctx.library.find((m) => m.id === item.dataset.model);
      if (entry) { await ctx.showEntry(entry); render(); }
      return;
    }
    if (e.target.closest('[data-act="save"]')) {
      const file = ctx.view.file;
      if (!file) return;
      try {
        const res = await ctx.api(`/api/models?name=${encodeURIComponent(file.name)}`, {
          method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" },
        });
        const { entry } = await res.json();
        ctx.view.entry = entry;
        ctx.view.file = null;
        ctx.toast(`Saved “${entry.name}” to your library`);
        ctx.report();
        render();
      } catch (err) {
        ctx.toast(`Couldn't save it: ${err.detail || err.message}`, 6000);
      }
    }
  });
  render();
}
