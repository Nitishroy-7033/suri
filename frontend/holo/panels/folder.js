// A folder as a hologram window: drives and the usual places at the top
// level, then folders and files, a page at a time. Tap a folder to go in;
// tap a file to preview it (a 3D file goes straight onto the projector).
// Pinch a file and drag it to the orb (ask Jarvis), the trash (Recycle Bin)
// or empty space (open it there). Swipe to turn pages.

import { MODEL_EXT } from "../loader.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const ic = (id) => `<svg class="ic"><use href="#${id}"/></svg>`;
const IMG = /^image\/(jpeg|png|webp|gif|bmp|svg\+xml)$/;

function size(n) {
  if (n == null) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}
const when = (t) => new Date(t * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

function iconFor(e) {
  if (e.kind === "drive") return "i-drive";
  if (e.kind === "dir" || e.kind === "place") return "i-folder";
  if (MODEL_EXT.includes(e.ext)) return "i-cube";
  if ((e.mime || "").startsWith("image/")) return "i-image";
  if ((e.mime || "").startsWith("video/")) return "t-video";
  return "i-file";
}

let seq = 0;

export async function openFolder(ctx, path = "") {
  const box = document.createElement("div");
  box.className = "ws-folder";
  const id = `folder-${++seq}`;
  const state = { path, offset: 0, total: 0, grid: true, filter: "" };
  const w = ctx.wm.open({
    id, kind: "folder", title: "This PC", content: box, w: 560, h: 480, path: path || null,
    onSwipe: (dir) => page(dir),
  });

  box.innerHTML = `
    <div class="ws-fbar">
      <button type="button" class="ws-ibtn" data-act="up" title="Up a folder">${ic("i-up")}</button>
      <button type="button" class="ws-ibtn" data-act="root" title="All drives">${ic("i-drive")}</button>
      <div class="ws-crumbs"></div>
      <input class="ws-filter" type="search" placeholder="Filter…" aria-label="Filter this folder" />
      <button type="button" class="ws-ibtn" data-act="view" title="Grid or list">${ic("i-grid")}</button>
      <button type="button" class="ws-ibtn" data-act="refresh" title="Refresh">${ic("i-refresh")}</button>
    </div>
    <div class="ws-fbody"></div>
    <div class="ws-fpager" hidden>
      <button type="button" class="ws-btn" data-act="prev">Previous</button><span></span>
      <button type="button" class="ws-btn" data-act="next">Next</button>
    </div>`;
  const body = box.querySelector(".ws-fbody");

  function tile(e) {
    const drag = e.kind === "file" || e.kind === "dir"
      ? ` data-drag='${esc(JSON.stringify({ kind: "file", path: e.path, name: e.name, mime: e.mime, dir: e.kind === "dir" }))}'`
      : "";
    const thumb = state.grid && e.kind === "file" && IMG.test(e.mime || "") && e.size < 15e6
      ? `<img loading="lazy" alt="" src="${esc(ctx.withToken(`/api/fs/file?path=${encodeURIComponent(e.path)}`))}">`
      : ic(iconFor(e));
    const meta = e.kind === "drive" ? `${size(e.free)} free of ${size(e.total)}`
      : e.kind === "file" ? `${size(e.size)} · ${when(e.mtime)}` : e.kind === "dir" ? when(e.mtime) : "";
    return `<button type="button" class="ws-fitem" data-kind="${e.kind}" data-path="${esc(e.path)}"${drag} title="${esc(e.path)}">
      <span class="ws-fthumb">${thumb}</span><b>${esc(e.name)}</b><small>${esc(meta)}</small></button>`;
  }

  function crumbs(p) {
    const el = box.querySelector(".ws-crumbs");
    if (!p) { el.innerHTML = `<span>This PC</span>`; return; }
    const parts = p.split(/[\\/]/).filter(Boolean);
    let acc = "";
    el.innerHTML = parts.map((part, i) => {
      acc += i === 0 ? `${part}\\` : `${part}\\`;
      const target = i === 0 ? acc : acc.replace(/\\$/, "");
      return `<button type="button" data-crumb="${esc(target)}">${esc(part)}</button>`;
    }).join(`<i>›</i>`);
  }

  async function load(p = state.path, offset = 0) {
    body.innerHTML = `<p class="ws-empty">Loading…</p>`;
    try {
      if (!p) {
        const data = await (await ctx.api("/api/fs/drives")).json();
        state.path = ""; state.total = 0;
        body.innerHTML = `<p class="ws-fsec">Drives</p><div class="ws-fgrid">${data.drives.map(tile).join("")}</div>
          <p class="ws-fsec">Places</p><div class="ws-fgrid">${data.places.map(tile).join("")}</div>`;
        crumbs("");
        ctx.wm.setTitle(id, "This PC");
        w.path = null;
      } else {
        const data = await (await ctx.api(`/api/fs/list?path=${encodeURIComponent(p)}&offset=${offset}`)).json();
        Object.assign(state, { path: data.path, offset: data.offset, total: data.total, parent: data.parent });
        const list = state.filter ? data.entries.filter((e) => e.name.toLowerCase().includes(state.filter)) : data.entries;
        body.innerHTML = list.length
          ? `<div class="${state.grid ? "ws-fgrid" : "ws-flist"}">${list.map(tile).join("")}</div>`
          : `<p class="ws-empty">${state.filter ? "Nothing matches" : "This folder is empty (or everything in it is hidden or off limits)"}</p>`;
        crumbs(data.path);
        ctx.wm.setTitle(id, data.name);
        w.path = data.path;
      }
      const pager = box.querySelector(".ws-fpager");
      pager.hidden = state.total <= 200;
      pager.querySelector("span").textContent = `${state.offset + 1}–${Math.min(state.total, state.offset + 200)} of ${state.total}`;
      ctx.report();
    } catch (err) {
      body.innerHTML = `<p class="ws-empty">${esc(err.detail || err.message)}</p>`;
    }
  }

  function page(dir) {
    const next = state.offset + dir * 200;
    if (state.path && next >= 0 && next < state.total) load(state.path, next);
  }

  box.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.crumb) return load(b.dataset.crumb);
    const act = b.dataset.act;
    if (act === "up") return load(state.path ? state.parent || "" : "");
    if (act === "root") return load("");
    if (act === "refresh") return load(state.path, state.offset);
    if (act === "view") { state.grid = !state.grid; return load(state.path, state.offset); }
    if (act === "prev") return page(-1);
    if (act === "next") return page(1);
    if (b.classList.contains("ws-fitem")) {
      const k = b.dataset.kind, p = b.dataset.path;
      if (k === "drive" || k === "dir" || k === "place") return load(p);
      const name = b.querySelector("b").textContent;
      const ext = (name.match(/\.[^.]+$/)?.[0] || "").toLowerCase();
      if (MODEL_EXT.includes(ext)) ctx.dropFile({ kind: "file", path: p, name }, "space");
      else ctx.openPanel("file", { path: p, name });
    }
  });
  box.querySelector(".ws-filter").addEventListener("input", (e) => {
    state.filter = e.target.value.trim().toLowerCase();
    if (state.path) load(state.path, state.offset);
  });
  const onChanged = () => { if (state.path) load(state.path, state.offset); };
  addEventListener("ws-fs-changed", onChanged);
  w.onClose = () => removeEventListener("ws-fs-changed", onChanged);
  // The mouse says what it is over too, for "what's this?".
  box.addEventListener("pointerover", (e) => {
    const item = e.target.closest?.("[data-drag]");
    if (item && e.pointerType === "mouse") ctx.hover(JSON.parse(item.dataset.drag));
  });

  load(path);
}
