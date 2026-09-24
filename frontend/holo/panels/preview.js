// A file, opened as a hologram window: pictures, video, audio, PDFs, text
// and code. Works for a file on your drives (streamed from /api/fs/file) or
// one dropped from Explorer (read locally, never uploaded). The header chip
// can be dragged to the orb or the trash like a file in a folder.

import { MODEL_EXT, extOf } from "../loader.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const TEXT = new Set([".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".css",
  ".xml", ".yaml", ".yml", ".ini", ".toml", ".bat", ".ps1", ".sh", ".c", ".cpp", ".h", ".java", ".cs", ".go", ".rs",
  ".rb", ".php", ".sql", ".srt", ".vtt", ".env.example", ".gitignore"]);
const MAX_TEXT = 200_000;

function kindOf(name, mime = "") {
  const ext = extOf(name) || (name.match(/\.[^.\\/]+$/)?.[0] ?? "").toLowerCase();
  if (MODEL_EXT.includes(ext)) return "model";
  if (mime.startsWith("image/") || /\.(jpe?g|png|gif|webp|bmp|svg|avif)$/i.test(name)) return "image";
  if (mime.startsWith("video/") || /\.(mp4|webm|mov|mkv|m4v)$/i.test(name)) return "video";
  if (mime.startsWith("audio/") || /\.(mp3|wav|ogg|m4a|flac|aac)$/i.test(name)) return "audio";
  if (ext === ".pdf") return "pdf";
  if (TEXT.has(ext) || mime.startsWith("text/")) return "text";
  return "other";
}

let seq = 0;

export async function openPreview(ctx, { path = null, name = null, file = null }) {
  const title = file ? file.name : name || path.split(/[\\/]/).pop();
  const kind = kindOf(title, file?.type);
  const url = file ? URL.createObjectURL(file) : ctx.withToken(`/api/fs/file?path=${encodeURIComponent(path)}`);
  const box = document.createElement("div");
  box.className = "ws-preview";
  box.dataset.kind = kind;
  const payload = path ? { kind: "file", path, name: title } : null;
  box.innerHTML = `
    <div class="ws-pbar">
      <span class="ws-pchip"${payload ? ` data-drag='${esc(JSON.stringify(payload))}' title="Drag to the orb or the Recycle Bin"` : ""}>${esc(title)}</span>
      <span class="grow"></span>
      ${kind === "model" ? `<button type="button" class="ws-btn" data-act="holo">Show as hologram</button>` : ""}
      ${path ? `<button type="button" class="ws-btn" data-act="ask">Ask Jarvis</button>
                <button type="button" class="ws-btn danger" data-act="trash">Recycle Bin</button>` : ""}
    </div>
    <div class="ws-pbody"></div>`;
  const body = box.querySelector(".ws-pbody");
  const w = ctx.wm.open({
    id: path ? `file:${path}` : `local-${++seq}`, kind: "file", title, content: box, path,
    w: kind === "text" ? 560 : kind === "audio" ? 420 : 620, h: kind === "audio" ? 220 : 480,
    onClose: () => { if (file) URL.revokeObjectURL(url); },
  });
  if (w.content !== box) return;  // already open: brought forward

  if (kind === "image") body.innerHTML = `<img alt="${esc(title)}" src="${esc(url)}">`;
  else if (kind === "video") body.innerHTML = `<video controls preload="metadata" src="${esc(url)}"></video>`;
  else if (kind === "audio") body.innerHTML = `<audio controls preload="metadata" src="${esc(url)}"></audio>`;
  else if (kind === "pdf") body.innerHTML = `<iframe title="${esc(title)}" src="${esc(url)}"></iframe>`;
  else if (kind === "model") {
    body.innerHTML = `<p class="ws-empty">A 3D model. Loading it onto the projector…</p>`;
    if (file) ctx.showFile(file);
    else ctx.dropFile(payload, "space");
  } else if (kind === "text") {
    body.innerHTML = `<p class="ws-empty">Loading…</p>`;
    try {
      const res = file ? null : await fetch(url, { headers: { Range: `bytes=0-${MAX_TEXT - 1}` } });
      if (res && !res.ok) {
        const body = await res.text();
        let why = body;
        try { why = JSON.parse(body).detail || body; } catch {}
        throw new Error(why);
      }
      const text = file ? await file.slice(0, MAX_TEXT).text() : await res.text();
      const pre = document.createElement("pre");
      pre.textContent = text + ((file ? file.size : Number(res.headers.get("content-range")?.split("/")[1] || 0)) > MAX_TEXT ? "\n…" : "");
      body.replaceChildren(pre);
    } catch (err) {
      body.innerHTML = `<p class="ws-empty">${esc(err.message)}</p>`;
    }
  } else {
    body.innerHTML = `<p class="ws-empty">No preview for this kind of file.${path ? " Jarvis may still be able to tell you about it." : ""}</p>`;
  }

  box.querySelector(".ws-pbar").addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "ask") { ctx.ask(`Tell me what's in this file: ${path}`); ctx.toast(`Asking Jarvis about ${title}…`); }
    else if (act === "trash") ctx.confirmDelete([path]);
    else if (act === "holo") { if (file) ctx.showFile(file); else ctx.dropFile(payload, "space"); }
  });
}
