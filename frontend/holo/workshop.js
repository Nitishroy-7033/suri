import { createStage } from "./stage.js";
import { loadModel, MODEL_EXT, extOf } from "./loader.js";
import { createWM } from "./wm.js";
import { createDnd } from "./dnd.js";
import { createPointers } from "./pointer.js";
import { createCallouts } from "./callouts.js";
import { createOrb } from "./orb.js";
import { actFor } from "./keys.js";
import { api, withToken } from "../api.js";

// The holographic workshop: a full-screen hologram lab over the normal page.
//
//   stage.js     the 3D hologram (model, projector, bloom)
//   wm.js        floating windows; chat, settings and Systems are *adopted*
//                from the page, not copied
//   pointer.js   mouse and hands as one kind of pointer
//   dnd.js       drag and drop between windows, the orb and the trash
//   hands.js     camera + MediaPipe -> virtual hand pointers (loaded on demand)
//   panels/*     library, files, previews, PC controls, the guide (on demand)
//   tutorial.js  the hands tutorial's coach card (on demand)
//
// Voice arrives as holo_* events from the backend's tools; what is on screen
// goes back as {"t":"holo_state"} so Jarvis can answer "what's this?".

const ic = (id) => `<svg class="ic"><use href="#${id}"/></svg>`;
const GUIDED_KEY = "jarvis-holo-guided";  // the guide opens by itself once

/** The workshop's stylesheet and fonts, fetched the first time it opens. */
export function ensureStyles() {
  if (document.getElementById("holo-css")) return Promise.resolve();
  const fonts = document.createElement("link");
  fonts.rel = "stylesheet";
  fonts.href = "https://fonts.googleapis.com/css2?family=Orbitron:wght@500;600;700&family=Rajdhani:wght@500;600;700&display=swap";
  document.head.append(fonts);
  const css = document.createElement("link");
  css.id = "holo-css";
  css.rel = "stylesheet";
  css.href = new URL("./holo.css", import.meta.url).href;
  const loaded = new Promise((resolve) => { css.onload = css.onerror = resolve; });
  document.head.append(css);
  return loaded;
}

export function createWorkshop(deps) {
  const { root, send, bot, prefs, closeSettings, diag, ask, toggleMic, bands } = deps;
  const html = document.documentElement;

  root.innerHTML = `
    <div class="ws-stage"></div>
    <div class="ws-panels"></div>
    <header class="ws-hud">
      <div class="ws-brand">
        <b>J.A.R.V.I.S</b><span>workshop</span>
        <span class="ws-state"><i></i><em>Ready</em></span>
        <span class="ws-hands-chip" hidden>${ic("i-hand")}<em></em></span>
      </div>
      <nav class="ws-tools" aria-label="Workshop">
        <button type="button" data-open="guide" title="Guide: gestures, voice, shortcuts (?)">${ic("i-help")}<span>Guide</span></button>
        <button type="button" data-open="hands" title="Hand control: uses the camera (G)">${ic("i-hand")}<span>Hands</span></button>
        <button type="button" data-open="library" title="Model library (L)">${ic("i-cube")}<span>Models</span></button>
        <button type="button" data-open="files" title="Files (F)">${ic("i-folder")}<span>Files</span></button>
        <button type="button" data-open="chat" title="Conversation (C)">${ic("i-chat")}<span>Chat</span></button>
        <button type="button" data-open="systems" title="System gauges (S)">${ic("i-gauge")}<span>Systems</span></button>
        <button type="button" data-open="pc" title="Volume, brightness, media (P)">${ic("i-volume")}<span>PC</span></button>
        <button type="button" data-open="settings" title="Jarvis settings (,)">${ic("i-gear")}<span>Settings</span></button>
        <button type="button" data-act="arrange" title="Tidy the windows (A)">${ic("i-grid")}</button>
        <button type="button" data-act="exit" class="exit" title="Leave the workshop (H)">${ic("i-x")}</button>
      </nav>
    </header>
    <div class="ws-model-info" hidden><b></b><span></span><small></small></div>
    <p class="ws-hint"><kbd>?</kbd> guide · <kbd>G</kbd> hand control · drag to turn · wheel to zoom · double-click to reset · drop a 3D file anywhere</p>
    <div class="ws-dock" aria-label="Minimised windows"></div>
    <div class="ws-trash" aria-label="Drop here to move to the Recycle Bin">${ic("t-trash")}<span>Recycle Bin</span></div>
    <video class="ws-cam" playsinline muted hidden></video>
    <canvas class="ws-overlay"></canvas>
    <div class="ws-loading" hidden><i></i><span></span></div>
    <div class="ws-toasts" aria-live="polite"></div>`;
  const q = (s) => root.querySelector(s);
  const panelsEl = q(".ws-panels");

  // ---------- parts ----------

  const stage = createStage(q(".ws-stage"), { color: prefs().holoColor, bloom: prefs().bloom });
  const canvas = stage.renderer.domElement;
  const wm = createWM({ layer: panelsEl, dock: q(".ws-dock"), onChange: () => { syncTools(); report(); } });
  const callouts = createCallouts(panelsEl, stage);
  const orb = createOrb(root, { bands, onTap: toggleMic });
  const dnd = createDnd({
    root,
    targets: () => [
      { name: "orb", el: orb.el, pad: 30 },
      { name: "trash", el: q(".ws-trash"), pad: 10, accepts: (p) => p.kind === "file" },
      ...wm.list().filter((w) => !w.min).map((w) => ({ name: `win:${w.id}`, el: w.el, win: w })),
    ],
    onDrop,
  });
  const pointers = createPointers({
    root, wm, stage, dnd,
    isStage: (el) => el === canvas,
    onSelect: (part, info) => { select(part); for (const fn of selectFns) fn(part, info); },
    // What is under a hand or the mouse: a part of the model, or a file in a
    // folder window -- "what's this?" is answered from it.
    onHover: (part, item) => {
      view.hovered = item?.path ?? part;
      canvas.style.cursor = part ? "pointer" : "";
      report();
    },
  });

  // ---------- what is on show ----------

  const view = { entry: null, file: null, selected: null, hovered: null, hands: 0 };
  // Who else wants to know what the hands are doing (the tutorial).
  const frameFns = new Set(), gestureFns = new Set(), selectFns = new Set();
  const listen = (set) => (fn) => { set.add(fn); return () => set.delete(fn); };
  let library = [];
  let loadSeq = 0;
  let loading = false;
  // "Show the gearbox, then explode it" arrives as two events; the second
  // must wait for the model, or loading it would undo the explode.
  const afterLoad = [];
  let isOpen = false;

  // Yours and the samples, merged by the server (domain/holo/catalog.py).
  async function loadLibrary() {
    try {
      library = (await (await api("/api/library")).json()).models || [];
    } catch (err) {
      console.warn("model library unavailable", err);
    }
    return library;
  }

  function spinner(on, text = "") {
    const el = q(".ws-loading");
    el.hidden = !on;
    el.querySelector("span").textContent = text;
  }

  async function showEntry(entry) {
    return show({ url: entry.src }, entry);
  }

  async function showFile(file) {
    return show({ file }, { id: `local:${file.name}`, name: file.name.replace(/\.[^.]+$/, ""), local: true, credit: "from your computer" }, file);
  }

  async function show(source, entry, file = null) {
    const seq = ++loadSeq;
    loading = true;
    spinner(true, `Loading ${entry.name || entry.id}…`);
    try {
      const obj = await loadModel({
        ...source,
        onProgress: (f) => { if (seq === loadSeq) spinner(true, `Loading ${entry.name || entry.id}… ${Math.round(f * 100)}%`); },
      });
      if (seq !== loadSeq) return;  // a newer request won
      const holo = stage.show(obj);
      Object.assign(view, { entry, file, selected: null, hovered: null });
      callouts.clear();
      const info = q(".ws-model-info");
      info.hidden = false;
      info.querySelector("b").textContent = entry.name || entry.id;
      info.querySelector("span").textContent = `${holo.count} part${holo.count === 1 ? "" : "s"}` + (file ? " · not saved" : "");
      info.querySelector("small").textContent = [entry.credit, entry.license].filter(Boolean).join(" · ");
      loading = false;
      for (const fn of afterLoad.splice(0)) fn();
      report();
    } catch (err) {
      if (seq === loadSeq) { toast(`Couldn't load ${entry.name || "that model"}: ${err.message}`, 6000); afterLoad.length = 0; }
    } finally {
      if (seq === loadSeq) { loading = false; spinner(false); }
    }
  }

  /** Run now, or once the model being loaded is on the projector. */
  const whenLoaded = (fn) => (loading ? afterLoad.push(fn) : fn());

  async function step(dir) {
    if (!library.length) await loadLibrary();
    if (!library.length) return;
    const i = library.findIndex((m) => m.id === view.entry?.id);
    showEntry(library[(i + dir + library.length) % library.length]);
  }

  function select(part) {
    const holo = stage.holo;
    if (!holo) return;
    const name = holo.highlight(part);
    view.selected = name;
    if (name) {
      const note = view.entry?.parts?.[name];
      callouts.upsert("part", {
        title: name,
        lines: [note || "Say “what's this?” to ask Jarvis"],
        anchor: (v) => stage.holo?.centreOf(name, v),
      });
    } else {
      callouts.remove("part");
    }
    report();
    return name;
  }

  const VIEW = {
    rotate_left: () => stage.fling(-4, 0), rotate_right: () => stage.fling(4, 0),
    tilt_up: () => stage.fling(0, -2.6), tilt_down: () => stage.fling(0, 2.6),
    spin_on: () => stage.setSpin(true), spin_off: () => stage.setSpin(false),
    zoom_in: () => stage.zoom(1.35), zoom_out: () => stage.zoom(1 / 1.35),
    reset: () => { stage.reset(); select(null); },
    explode: () => stage.explode(true), collapse: () => stage.explode(false),
    next: () => step(1), previous: () => step(-1),
  };

  // ---------- windows ----------

  // The diagnostics feed runs while anything shows it: the page's Systems
  // view, the Systems window here, or the PC panel's gauges.
  const diagUsers = new Set();
  const syncDiag = () => diag.setVisible(html.dataset.view === "systems" || wm.has("systems") || diagUsers.size > 0);

  const ctx = {
    wm, dnd, stage, api, withToken, send, ask, toast, report, showEntry, showFile,
    onDiag: (fn) => { diagUsers.add(fn); syncDiag(); return () => { diagUsers.delete(fn); syncDiag(); }; },
    loadLibrary, get library() { return library; }, get view() { return view; }, openPanel,
    dropFile: (payload, where) => onDrop(payload, where, {}),
    hover: (item) => { if (view.hovered !== item.path) { view.hovered = item.path; report(); } },
    confirmDelete: (paths) => import("./panels/confirm.js").then((m) => m.requestDelete(ctx, paths)),
    root, toggleHands: (on) => toggleHands(on),
    get handsRunning() { return !!hands?.running; },
    onHandFrame: listen(frameFns), onGestureEvent: listen(gestureFns), onSelectEvent: listen(selectFns),
  };

  function adopt(id, title, el, size, hooks = {}) {
    if (!el) return;
    wm.open({ id, kind: id, title, content: el, adopt: true, ...size, onOpen: hooks.onOpen, onClose: hooks.onClose });
  }

  function openPanel(target, opts = {}) {
    switch (target) {
      case "chat":
        adopt("chat", "Conversation", document.querySelector(".convo-panel"), { w: 400, h: 560 });
        break;
      case "settings":
        closeSettings();
        adopt("settings", "Jarvis settings", document.getElementById("settings"), { w: 420, h: 620 });
        break;
      case "systems":
        adopt("systems", "Systems", document.getElementById("diag"), { w: 540, h: 560 }, {
          onOpen: syncDiag, onClose: syncDiag,
        });
        break;
      case "library":
        import("./panels/library.js").then((m) => m.openLibrary(ctx));
        break;
      case "files": case "folder":
        import("./panels/folder.js").then((m) => m.openFolder(ctx, opts.path || ""));
        break;
      case "file":
        import("./panels/preview.js").then((m) => m.openPreview(ctx, { path: opts.path, name: opts.name }));
        break;
      case "pc":
        import("./panels/pc.js").then((m) => m.openPc(ctx));
        break;
      case "hands":
        toggleHands();
        break;
      case "guide": case "help":
        import("./panels/guide.js").then((m) => m.openGuide(ctx, opts.tab));
        break;
      case "tutorial":
        // Out of the way: you practise on the hologram behind it.
        if (wm.has("guide")) wm.minimise("guide");
        import("./tutorial.js").then((m) => { tutorialMod = m; m.startTutorial(ctx); });
        break;
      default:
        return false;
    }
    return true;
  }

  function syncTools() {
    for (const b of root.querySelectorAll(".ws-tools [data-open]")) {
      const id = b.dataset.open;
      const on = id === "hands" ? !!hands?.running : id === "files" ? wm.list().some((w) => w.kind === "folder") : wm.has(id);
      b.classList.toggle("on", on);
    }
  }

  root.querySelector(".ws-tools").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.act === "exit") close();
    else if (b.dataset.act === "arrange") wm.arrange();
    else if (b.dataset.open) {
      const id = b.dataset.open;
      const w = id === "files" ? null : wm.get(id);
      if (w && !w.min) wm.close(id); else openPanel(id);
    }
  });

  // ---------- drag and drop ----------

  function onDrop(p, where, at) {
    if (p.kind === "model") {
      const entry = library.find((m) => m.id === p.id);
      if (entry) showEntry(entry);
      return;
    }
    if (p.kind !== "file") return;
    if (where === "orb") {
      ask(`Tell me what's in this file: ${p.path}`);
      toast(`Asking Jarvis about ${p.name}…`);
    } else if (where === "trash") {
      ctx.confirmDelete([p.path]);
    } else if (where.startsWith("win:")) {
      toast("Moving files isn't switched on: drop it on empty space to open it.");
    } else if (MODEL_EXT.includes(extOf(p.name))) {
      show({ url: withToken(`/api/fs/file?path=${encodeURIComponent(p.path)}`), ext: extOf(p.name) },
        { id: `file:${p.path}`, name: p.name.replace(/\.[^.]+$/, ""), credit: p.path });
    } else {
      openPanel("file", { path: p.path, name: p.name });
    }
  }

  // Files dropped from Windows Explorer onto the workshop.
  root.addEventListener("dragover", (e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); root.classList.add("os-drop"); } });
  root.addEventListener("dragleave", (e) => { if (e.target === root || !root.contains(e.relatedTarget)) root.classList.remove("os-drop"); });
  root.addEventListener("drop", (e) => {
    const files = [...(e.dataTransfer?.files || [])];
    if (!files.length) return;
    e.preventDefault();
    root.classList.remove("os-drop");
    const model = files.find((f) => MODEL_EXT.includes(extOf(f.name)));
    if (model) { showFile(model); return; }
    import("./panels/preview.js").then((m) => files.slice(0, 4).forEach((f) => m.openPreview(ctx, { file: f })));
  });

  // ---------- hands ----------

  let hands = null;
  async function toggleHands(on = !hands?.running) {
    if (!on) { hands?.stop(); syncTools(); return; }
    try {
      if (!hands) {
        toast("Starting hand tracking…");
        const { createHands } = await import("./hands.js");
        hands = createHands({
          video: q(".ws-cam"), overlay: q(".ws-overlay"), pointers, prefs,
          onHands: (n) => {
            if (n === view.hands) return;
            view.hands = n;
            const chip = q(".ws-hands-chip");
            chip.hidden = !hands?.running;
            chip.querySelector("em").textContent = n ? `${n} hand${n > 1 ? "s" : ""}` : "show a hand";
            report();
          },
          onGesture,
          onTrack: (hs) => { for (const fn of frameFns) fn(hs); },
        });
      }
      await hands.start();
      q(".ws-hands-chip").hidden = false;
      q(".ws-hands-chip em").textContent = "show a hand";
    } catch (err) {
      toast(`Hand tracking failed: ${err.message}`, 8000);
    }
    syncTools();
  }

  // Swipe like a touch carousel: flick left for the next model (or page of
  // the focused window), right for the previous. Thumbs up presses the
  // confirm button of a dialog that is showing -- never anything else.
  function onGesture(g) {
    for (const fn of gestureFns) fn(g);
    if (g === "palm") stage.fling(0, 0);
    else if (g === "swipe_left" || g === "swipe_right") {
      const dir = g === "swipe_left" ? 1 : -1;
      const w = wm.focused();
      if (w?.onSwipe) w.onSwipe(dir); else step(dir);
    } else if (g === "confirm") {
      root.querySelector('.ws-confirm [data-act="yes"]')?.click();
    }
  }

  // ---------- camera snapshot ("make a 3D model of this") ----------

  // The server asked for a picture: take it from the hand-tracking camera if
  // it is on, else open the camera just long enough for one frame.
  async function snapshot(id) {
    toast("Scanning what the camera sees…");
    let stream = null, video = q(".ws-cam");
    try {
      if (!hands?.running) {
        stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
        video = document.createElement("video");
        Object.assign(video, { muted: true, playsInline: true, srcObject: stream });
        await video.play();
        await new Promise((r) => setTimeout(r, 500));  // let the exposure settle
      }
      const c = document.createElement("canvas");
      c.width = video.videoWidth; c.height = video.videoHeight;
      c.getContext("2d").drawImage(video, 0, 0);
      const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.9));
      await api(`/api/snapshot?id=${encodeURIComponent(id)}`, { method: "POST", body: blob, headers: { "Content-Type": "image/jpeg" } });
      root.classList.add("flash");
      setTimeout(() => root.classList.remove("flash"), 400);
    } catch (err) {
      toast(`Couldn't take the picture: ${err.detail || err.message}`, 6000);
    } finally {
      stream?.getTracks().forEach((t) => t.stop());
    }
  }

  // ---------- keyboard ----------

  // The workshop's own keys (keys.js). app.js still handles H, M, T, Esc,
  // / and , -- these are the rest, only while the workshop is open.
  let tutorialMod = null;
  addEventListener("keydown", (e) => {
    if (!isOpen || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target.closest?.("input, textarea, select, [contenteditable], dialog")) return;
    const hit = actFor(e);
    if (!hit) return;
    // Space and arrows on a focused button belong to the button.
    if ((e.key === " " || e.key.startsWith("Arrow")) && e.target.closest?.("button")) return;
    if (e.repeat && !["zoom", "step"].includes(hit.act)) return;
    e.preventDefault();
    const toggle = (id) => { const w = wm.get(id); if (w && !w.min) wm.close(id); else openPanel(id); };
    switch (hit.act) {
      case "guide": toggle("guide"); break;
      case "hands": toggleHands(); break;
      case "files": openPanel("files"); break;
      case "library": case "chat": case "systems": case "pc": toggle(hit.act); break;
      case "explode": whenLoaded(() => { stage.explode(!stage.exploded); report(); }); break;
      case "spin": stage.setSpin(!stage.spinning); break;
      case "reset": VIEW.reset(); break;
      case "step": step(hit.dir); break;
      case "zoom": stage.zoom(hit.dir > 0 ? 1.15 : 1 / 1.15); break;
      case "arrange": wm.arrange(); break;
      case "closeWin": { const w = wm.focused(); if (w) wm.close(w.id); break; }
    }
  });

  // ---------- messages ----------

  function toast(text, ms = 3500) {
    const el = document.createElement("div");
    el.className = "ws-toast";
    el.textContent = text;
    q(".ws-toasts").append(el);
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 300); }, ms);
  }

  // What the page shows, for the brain. Only sent when something changed.
  let reportTimer = 0, lastReport = "";
  function report(force = false) {
    clearTimeout(reportTimer);
    reportTimer = setTimeout(() => {
      const holo = stage.holo;
      const focused = wm.focused();
      const state = {
        open: isOpen,
        model: view.entry ? { id: view.entry.id, name: view.entry.name || view.entry.id } : null,
        parts: holo ? holo.names().slice(0, 40) : [],
        part_count: holo?.count ?? 0,
        selected: view.selected, hovered: view.hovered,
        exploded: stage.exploded,
        panels: wm.list().map((w) => ({ id: w.id, kind: w.kind, title: w.title, path: w.path })),
        focused: focused ? (focused.path || focused.id) : null,
        hands: view.hands,
      };
      const json = JSON.stringify(state);
      if (force || json !== lastReport) { lastReport = json; send({ t: "holo_state", state }); }
    }, force ? 0 : 250);
  }

  // ---------- open / close ----------

  function open() {
    if (isOpen) return;
    isOpen = true;
    root.hidden = false;
    html.dataset.workshop = "1";
    bot.setPaused?.(true);
    stage.setPaused(false);
    orb.setPaused(false);
    applyPrefs();
    if (!wm.list().length) openPanel("chat");
    // Fetch the panels now, so the first "open my downloads" is instant.
    (window.requestIdleCallback || setTimeout)(() => {
      for (const m of ["library", "folder", "preview", "confirm", "pc", "events"]) import(`./panels/${m}.js`).catch(() => {});
    });
    // Something to look at on first open -- unless a model was already asked for.
    if (loadSeq === 0) loadLibrary().then((lib) => { if (loadSeq === 0 && lib[0]) showEntry(lib[0]); });
    // The very first time, show how it all works.
    let guided = true;
    try { guided = localStorage.getItem(GUIDED_KEY) === "1"; localStorage.setItem(GUIDED_KEY, "1"); } catch {}
    if (!guided) setTimeout(() => { if (isOpen) openPanel("guide"); }, 900);
    report(true);
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    tutorialMod?.stopTutorial();
    hands?.stop();
    pointers.closePicker();
    dnd.cancel();
    wm.closeAll();  // hands the chat, settings and Systems back to the page
    stage.setPaused(true);
    orb.setPaused(true);
    root.hidden = true;
    delete html.dataset.workshop;
    bot.setPaused?.(false);
    syncTools();
    report(true);
  }

  function applyPrefs() {
    const p = prefs();
    root.style.setProperty("--holo", p.holoColor);
    stage.setColor(p.holoColor);
    stage.setBloom(p.bloom);
    wm.setCurve(p.curve);
    root.classList.toggle("no-glass", !p.glass);
    hands?.applyPrefs?.();
  }

  return {
    get isOpen() { return isOpen; },
    open, close,
    toggle() { isOpen ? close() : open(); },
    openPanel,
    /** The pointer layer and the 3D stage, for poking at from the console. */
    pointers, stage,
    /** State from app.js: the voice state and its label. */
    setState(state, word) {
      orb.setState(state, word);
      root.querySelector(".ws-state").dataset.state = state;
      root.querySelector(".ws-state em").textContent = word;
    },
    applyPrefs,
    resync: () => report(true),
    /** A diagnostics snapshot ({"t":"diag"}), for the PC panel's gauges. */
    diagSnap(snap) { for (const fn of diagUsers) fn(snap); },
    /** A holo_* / fs_* / forge_* event from the server. */
    event(msg) {
      const d = msg.data || {};
      const closing = msg.kind === "holo_panel" && d.action === "close" && d.target === "workshop";
      // A delete waiting for your yes, or a build starting, must be seen.
      if ((!closing && msg.kind.startsWith("holo_")) || msg.kind === "fs_confirm" || msg.kind === "forge_start") open();
      switch (msg.kind) {
        case "holo_show": if (d.entry) showEntry(d.entry); break;
        case "holo_snapshot_request": snapshot(d.id); break;
        case "holo_view": whenLoaded(() => { VIEW[d.action]?.(); report(); }); break;
        case "holo_highlight": whenLoaded(() => {
          const name = select(d.part || null);
          if (d.part && !name) toast(`No part called “${d.part}”`);
        }); break;
        case "holo_panel": {
          const target = String(d.target || "");
          if (d.action === "open") {
            if (target === "workshop") break;
            if (target === "folder" || target === "file") openPanel(target, { path: d.path });
            else openPanel(target);
          } else if (d.action === "close") {
            if (target === "workshop") { close(); break; }
            const w = wm.find(target);
            if (w) wm.close(w.id);
          } else if (d.action === "focus") {
            const w = wm.find(target);
            if (w) wm.focus(w.id);
          } else if (d.action === "close_all") wm.closeAll();
          else if (d.action === "arrange") wm.arrange();
          break;
        }
        default:
          if (msg.kind.startsWith("fs_") || msg.kind.startsWith("forge_")) {
            import("./panels/events.js").then((m) => m.handle(ctx, msg));
          }
      }
    },
  };
}
