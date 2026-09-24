import { MOODS, GESTURES, ARM_POSES } from "./robot-3d/robot-react/robotEngine.js";

// Preferences, layout and the settings drawer.
//
// Everything here is per-browser and saved in localStorage. The voice knobs
// are the exception: they live on the server, per session. Whatever you
// change is remembered here and re-sent each time the socket connects, so a
// reload or reconnect keeps your tuning; "Reset" goes back to the .env values.

const KEY = "jarvis-prefs";
const DEFAULTS = {
  view: "split", swap: false, split: 44,
  theme: "auto", accent: "#1e88ff", look: "wander",
  fidgets: true, shadow: true, caption: true, toolDetails: true,
  chirp: true, deviceId: "",
  talkStyle: "lively", armMode: "auto", armPose: "bothUp",
  tunables: {},  // only the ones you moved; the rest follow .env
  // The holographic workshop (holo/)
  holoColor: "#38d6ff", bloom: 0.9, glass: true, curve: true,
  showCam: true, showSkeleton: true, smoothing: 0.5,
};
const LABELS = {
  boot: "Boot up", hi: "Hi!", bothUp: "Both up", pointUp: "Point up", reachOut: "Reach out", tPose: "T-pose",
};
const label = (k) => LABELS[k] ?? k[0].toUpperCase() + k.slice(1);
const VIEWS = ["split", "robot", "chat", "systems"];
const ACCENTS = [
  ["#1e88ff", "Blue"], ["#7c5cff", "Violet"], ["#0ea5a4", "Teal"],
  ["#16a34a", "Green"], ["#f97316", "Orange"], ["#ec4899", "Pink"],
];

// The server knobs worth exposing, in plain words.
const TUNABLES = [
  { key: "wake_threshold", label: "Wake word sensitivity", min: 0.02, max: 0.95, step: 0.01,
    hint: "Lower wakes more easily, but false wakes go up", fmt: (v) => v.toFixed(2) },
  { key: "vad_threshold", label: "Voice detection", min: 0.1, max: 0.9, step: 0.05,
    hint: "Raise it in a noisy room", fmt: (v) => v.toFixed(2) },
  { key: "end_silence_ms", label: "Pause before answering", min: 300, max: 1500, step: 50,
    hint: "How long a pause counts as “done talking”", fmt: (v) => `${v} ms` },
  { key: "followup_ms", label: "Follow-up window", min: 0, max: 15000, step: 500,
    hint: "Time to keep talking after a reply without the wake word", fmt: (v) => `${(v / 1000).toFixed(1)} s` },
  { key: "no_speech_timeout_ms", label: "Give up after a false wake", min: 1000, max: 6000, step: 250,
    hint: "Stop listening if nothing is said", fmt: (v) => `${(v / 1000).toFixed(1)} s` },
  { key: "barge_enabled", label: "Let me interrupt Jarvis", toggle: true,
    hint: "Speaking over a reply stops it" },
];

export function initSettings({ bot, director, send, onDeviceChange, openMicTest, onChange }) {
  const root = document.documentElement;
  const $ = (id) => document.getElementById(id);
  const drawer = $("settings"), scrim = $("scrim");

  let prefs = { ...DEFAULTS };
  try { prefs = { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch {}
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch {} };

  let envSettings = null;   // what the server started with (.env)
  let liveSettings = null;  // what it is running now

  // ---------- apply ----------

  function apply() {
    root.dataset.view = prefs.view;
    root.dataset.swap = prefs.swap ? "1" : "0";
    root.style.setProperty("--split", `${prefs.split}%`);
    if (prefs.theme === "auto") delete root.dataset.theme; else root.dataset.theme = prefs.theme;
    root.style.setProperty("--accent", prefs.accent);
    root.dataset.caption = prefs.caption ? "1" : "0";
    root.dataset.toolDetails = prefs.toolDetails ? "1" : "0";
    bot.setOptions({ look: prefs.look, shadow: prefs.shadow, colors: { glow: prefs.accent } });
    director.setFidgets(prefs.fidgets);
    director.setTalkStyle(prefs.talkStyle);
    const manual = prefs.armMode === "manual";
    if (manual ? director.armLock !== prefs.armPose : director.armLock) director.lockArms(manual ? prefs.armPose : null);
    for (const b of document.querySelectorAll("#arm-mode [data-arm]"))
      b.setAttribute("aria-checked", String(b.dataset.arm === prefs.armMode));
    for (const b of document.querySelectorAll("#pose-grid button"))
      b.setAttribute("aria-pressed", String(manual && b.dataset.pose === prefs.armPose));
    $("arm-hint").textContent = manual
      ? "Manual: the arms hold the chosen pose, even while talking."
      : "Auto: arms follow the mood and talking. Tap a pose to strike it briefly.";

    for (const b of document.querySelectorAll(".seg [data-view]"))
      b.setAttribute("aria-checked", String(b.dataset.view === prefs.view));
    for (const el of document.querySelectorAll("[data-pref]")) {
      const v = prefs[el.dataset.pref];
      if (el.type === "checkbox") el.checked = !!v; else el.value = String(v);
    }
    for (const b of $("swatches").children)
      b.setAttribute("aria-checked", String(b.dataset.color === prefs.accent));
    const out = document.querySelector('[data-out="split"]');
    if (out) out.textContent = `${Math.round(prefs.split)}%`;
  }

  function set(patch) {
    Object.assign(prefs, patch);
    save();
    apply();
    onChange?.(prefs, patch);
  }

  // ---------- layout ----------

  const setView = (view) => set({ view: VIEWS.includes(view) ? view : "split" });
  const cycleView = () => setView(VIEWS[(VIEWS.indexOf(prefs.view) + 1) % VIEWS.length]);

  function initDivider() {
    const div = $("divider"), panes = document.querySelector(".panes");
    const clamp = (v) => Math.max(25, Math.min(70, v));
    const fromX = (x) => {
      const r = panes.getBoundingClientRect();
      const pct = ((x - r.left) / r.width) * 100;
      return clamp(prefs.swap ? 100 - pct : pct);
    };
    div.addEventListener("pointerdown", (e) => {
      div.setPointerCapture(e.pointerId);
      div.classList.add("dragging");
      document.body.classList.add("resizing");
      const move = (ev) => {
        prefs.split = fromX(ev.clientX);
        root.style.setProperty("--split", `${prefs.split}%`);
      };
      const up = () => {
        div.removeEventListener("pointermove", move);
        div.classList.remove("dragging");
        document.body.classList.remove("resizing");
        set({ split: Math.round(prefs.split) });
      };
      div.addEventListener("pointermove", move);
      div.addEventListener("pointerup", up, { once: true });
      div.addEventListener("pointercancel", up, { once: true });
    });
    div.addEventListener("dblclick", () => set({ split: DEFAULTS.split }));
    div.addEventListener("keydown", (e) => {
      const step = e.shiftKey ? 5 : 2;
      const dir = (e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0) * (prefs.swap ? -1 : 1);
      if (!dir) return;
      e.preventDefault();
      set({ split: clamp(prefs.split + dir * step) });
    });
  }

  // ---------- drawer ----------

  const isOpen = () => drawer.classList.contains("open");
  function open() {
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    scrim.hidden = false;
    refreshDevices();
    drawer.querySelector("[data-close]").focus();
  }
  function close() {
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    scrim.hidden = true;
  }
  const toggle = () => (isOpen() ? close() : open());

  function initDrawer() {
    drawer.querySelector("[data-close]").onclick = close;
    scrim.onclick = close;

    // Every [data-pref] control writes straight to prefs.
    for (const el of drawer.querySelectorAll("[data-pref]")) {
      el.addEventListener(el.type === "range" ? "input" : "change", () => {
        const k = el.dataset.pref;
        const v = el.type === "checkbox" ? el.checked : el.type === "range" ? Number(el.value) : el.value;
        set({ [k]: v });
      });
    }

    const sw = $("swatches");
    for (const [color, name] of ACCENTS) {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.dataset.color = color;
      b.title = name;
      b.setAttribute("aria-label", name);
      b.style.background = color;
      b.onclick = () => set({ accent: color });
      sw.append(b);
    }

    initExpressions();

    $("device-select").addEventListener("change", (e) => {
      set({ deviceId: e.target.value });
      onDeviceChange?.(prefs.deviceId);
    });
    $("drawer-mictest").onclick = () => { close(); openMicTest(); };
    $("tunables-reset").onclick = resetTunables;
    $("prefs-reset").onclick = () => {
      const had = prefs.tunables;
      prefs = { ...DEFAULTS, tunables: {} };
      save();
      apply();
      if (Object.keys(had).length) resetTunables();
      onDeviceChange?.("");
    };
    navigator.mediaDevices?.addEventListener?.("devicechange", refreshDevices);
  }

  // Moods, gestures and poses, the same set as the robot playground.
  function initExpressions() {
    const grid = (id, names, onTap, attr) => {
      const box = $(id);
      for (const n of names) {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = label(n);
        b.dataset[attr] = n;
        b.onclick = () => onTap(n);
        box.append(b);
      }
    };
    grid("mood-grid", MOODS, (m) => director.flash(m, 2500), "mood");
    grid("gesture-grid", GESTURES, (g) => director.play(g), "gesture");
    grid("pose-grid", Object.keys(ARM_POSES), (p) => {
      if (prefs.armMode === "manual") set({ armPose: p });
      else director.pose(p, 2500);
    }, "pose");
    for (const b of document.querySelectorAll("#arm-mode [data-arm]"))
      b.onclick = () => set({ armMode: b.dataset.arm });
  }

  // Device names only appear once the page has mic permission; until then
  // the list is just "System default".
  async function refreshDevices() {
    const sel = $("device-select");
    let devices = [];
    try { devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput"); } catch {}
    const named = devices.filter((d) => d.label && d.deviceId !== "default" && d.deviceId !== "communications");
    sel.replaceChildren(new Option("System default", ""));
    for (const d of named) sel.append(new Option(d.label, d.deviceId));
    if (!named.length && devices.length) {
      const o = new Option("Start the mic once to see device names", "");
      o.disabled = true;
      sel.append(o);
    }
    sel.value = named.some((d) => d.deviceId === prefs.deviceId) ? prefs.deviceId : "";
  }

  // ---------- voice knobs (server) ----------

  function renderTunables() {
    const box = $("tunables");
    box.replaceChildren();
    const s = liveSettings;
    $("voice-note").textContent = s ? "applies instantly" : "connect to change";
    for (const t of TUNABLES) {
      const row = document.createElement("label");
      const v = s?.[t.key];
      if (t.toggle) {
        row.className = "row";
        row.innerHTML = `<span>${t.label}<small>${t.hint}</small></span><input type="checkbox" class="switch">`;
        const input = row.querySelector("input");
        input.checked = !!v;
        input.disabled = !s;
        input.onchange = () => patchTunable(t.key, input.checked);
      } else {
        row.className = "row col";
        row.innerHTML = `<span>${t.label} <b></b></span>` +
          `<input type="range" min="${t.min}" max="${t.max}" step="${t.step}"><small>${t.hint}</small>`;
        const input = row.querySelector("input"), out = row.querySelector("b");
        input.value = v ?? t.min;
        input.disabled = !s;
        out.textContent = v === undefined ? "—" : t.fmt(Number(v));
        input.oninput = () => { out.textContent = t.fmt(Number(input.value)); };
        input.onchange = () => patchTunable(t.key, Number(input.value));
      }
      box.append(row);
    }
  }

  function patchTunable(key, value) {
    prefs.tunables = { ...prefs.tunables, [key]: value };
    if (envSettings && envSettings[key] === value) delete prefs.tunables[key];
    save();
    send({ t: "config", patch: { [key]: value } });
  }

  function resetTunables() {
    const patch = {};
    if (envSettings) for (const t of TUNABLES) patch[t.key] = envSettings[t.key];
    prefs.tunables = {};
    save();
    if (envSettings) send({ t: "config", patch });
  }

  // ---------- public ----------

  initDrawer();
  initDivider();
  for (const b of document.querySelectorAll(".seg [data-view]")) b.onclick = () => setView(b.dataset.view);
  $("swap-btn").onclick = () => set({ swap: !prefs.swap });
  apply();
  renderTunables();

  return {
    get prefs() { return prefs; },
    open, close, toggle, isOpen, cycleView, refreshDevices,

    /** The server's "ready": remember .env values, then re-apply your tuning. */
    onReady(settings) {
      envSettings = { ...settings };
      liveSettings = { ...settings };
      if (Object.keys(prefs.tunables).length) send({ t: "config", patch: prefs.tunables });
      renderTunables();
    },
    /** "config_ack": the server says what it is running now. */
    onSettings(settings) {
      if (!settings) return;
      liveSettings = { ...settings };
      renderTunables();
    },
    onDisconnect() {
      liveSettings = null;
      renderTunables();
    },
  };
}
