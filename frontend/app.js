import { packMic, unpackTts } from "./protocol.js";
import { MicCapture } from "./mic.js";
import { PcmPlayer } from "./player.js";
import { DotGrid } from "./grid.js";
import { Backdrop, Scope } from "./fx.js";

const $ = (id) => document.getElementById(id);
const ui = {
  orb: $("orb"), state: $("state"), stateLabel: $("state-label"), hint: $("hint"),
  conn: $("conn"), connLabel: $("conn-label"),
  wakeVal: $("wake-val"), wakeFill: $("wake-fill"), wakeThresh: $("wake-thresh"),
  wakePeakVal: $("wake-peak-val"), wakeThreshVal: $("wake-thresh-val"),
  vadVal: $("vad-val"), vadFill: $("vad-fill"), vadThresh: $("vad-thresh"),
  vadState: $("vad-state"), vadThreshVal: $("vad-thresh-val"),
  meterFill: $("meter-fill"), meterDb: $("meter-db"),
  statSent: $("stat-sent"), statRecv: $("stat-recv"), statDropped: $("stat-dropped"),
  statNoise: $("stat-noise"), statUnderruns: $("stat-underruns"), statRtt: $("stat-rtt"),
  log: $("log"), sliders: $("sliders"),
  micBtn: $("mic-btn"), micIcon: $("mic-icon"), micLabel: $("mic-label"),
  talkBtn: $("talk-btn"), talkLabel: $("talk-label"), stopBtn: $("stop-btn"),
  convo: $("convo"), phaseLabel: $("phase-label"),
  wakePeak: $("wake-peak"), grid: $("grid"),
  core: $("core"), caption: $("caption"), captionRole: $("caption-role"),
  captionText: $("caption-text"), turns: $("turns"),
  clock: $("clock"), uptime: $("uptime"), themeName: $("theme-name"),
  fsBtn: $("fs-btn"), fsIcon: $("fs-icon"), focusBtn: $("focus-btn"),
};

const THEMES = { arc: "arc hud", matrix: "terminal", paper: "paper", bento: "bento" };
let fx = null, scope = null, turns = 0, captionTimer = 0;

let liveBubble = null;  // the assistant bubble currently being filled
const toolBubbles = new Map();  // tool name -> its bubble, to mark it done

const HINTS = {
  DISCONNECTED: "Connection lost — retrying…",
  CONNECTING: "Connecting…",
  IDLE: 'Say "Hey Jarvis", or tap Talk',
  LISTENING: "Listening…",
  THINKING: "Thinking…",
  SPEAKING: "Speaking — Phase 2 makes this a voice",
  FOLLOW_UP_WINDOW: "Go ahead — no wake word needed",
  OFFLINE: "Start the mic, then say “Hey Jarvis”",
};

// Knobs worth a slider: the ones you retune by ear.
const SLIDERS = [
  ["wake_threshold", 0.02, 0.95, 0.01, "Wake sensitivity (lower = easier)"],
  ["wake_confident", 0.2, 0.95, 0.05, "Trust without checking the words"],
  ["vad_threshold", 0.1, 0.9, 0.05, "Voice detection"],
  ["end_silence_ms", 300, 1500, 50, "Pause before it answers"],
  ["end_silence_short_ms", 200, 1000, 50, "Pause after a short phrase"],
  ["min_speech_ms", 80, 600, 20, "Minimum speech to count"],
  ["no_speech_timeout_ms", 1000, 6000, 250, "Give up after a false wake"],
  ["preroll_ms", 0, 1200, 50, "Audio kept from before the wake"],
  ["followup_ms", 0, 15000, 500, "Follow-up window"],
];

let ws = null, mic = null, player = null, grid = null, micOn = false;
let talkOn = false;  // conversation mode: answer anything said, no wake word
let framesSent = 0, lastPong = 0, wakeThreshold = 0.5, vadThreshold = 0.5;

function log(msg, kind = "") {
  const line = document.createElement("div");
  line.className = `line ${kind}`;
  const time = document.createElement("time");
  time.textContent = new Date().toTimeString().slice(0, 8);
  const text = document.createElement("span");
  text.textContent = msg;
  line.append(time, text);
  ui.log.prepend(line);
  while (ui.log.childElementCount > 300) ui.log.lastElementChild.remove();
}

function bubble(role, text) {
  ui.convo.querySelector(".convo-empty")?.remove();
  const el = document.createElement("div");
  el.className = `bubble ${role}`;
  el.textContent = text;
  ui.convo.append(el);
  ui.convo.scrollTop = ui.convo.scrollHeight;
  return el;
}

function setState(name) {
  ui.stateLabel.textContent = name.replaceAll("_", " ");
  ui.state.dataset.state = name;
  ui.orb.dataset.state = name;
  grid?.setState(name);
  fx?.setState(name);
  document.body.dataset.state = name;
  ui.hint.textContent = talkOn && name === "IDLE"
    ? "Just speak — I'm listening" : (HINTS[name] ?? "");
}

// A short rising blip, generated locally so it fires the instant the wake
// word is detected. Waiting for the server to send audio would add a round
// trip to the one moment that has to feel immediate.
function earcon() {
  if (!player) return;
  const ctx = player.ctx;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  const t = ctx.currentTime;
  osc.frequency.setValueAtTime(660, t);
  osc.frequency.exponentialRampToValueAtTime(1180, t + 0.09);
  gain.gain.setValueAtTime(0.0001, t);
  gain.gain.exponentialRampToValueAtTime(0.22, t + 0.015);
  gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
  osc.connect(gain).connect(ctx.destination);
  osc.start(t);
  osc.stop(t + 0.18);
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  setState("CONNECTING");
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    ui.connLabel.textContent = "connected";
    ui.conn.dataset.ok = "1";
    log("websocket open");
    sendHello();
    if (talkOn) send({ t: "converse", on: true });
  };
  ws.onclose = () => {
    ui.connLabel.textContent = "disconnected · retrying";
    ui.conn.dataset.ok = "0";
    setState("DISCONNECTED");
    setTimeout(connect, 1000);
  };
  ws.onmessage = (ev) => {
    if (typeof ev.data === "string") {
      onControl(JSON.parse(ev.data));
    } else {
      try {
        player.enqueue(unpackTts(ev.data));
      } catch (err) {
        log(`bad audio frame: ${err.message}`, "err");
      }
    }
  };
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function sendHello() {
  send({
    t: "hello", proto: 1,
    sampleRate: mic?.actualSampleRate ?? 16000,
    frameSamples: 1280,
    aec: mic?.aecActive ?? null,
    outputLatencyMs: player ? Math.round(player.outputLatency * 1000) : null,
  });
}

function onControl(msg) {
  switch (msg.t) {
    case "ready":
      log(`ready - phase ${msg.phase}, wake word "${msg.wakeWord}"`, "ok");
      setState(micOn ? "IDLE" : "OFFLINE");
      applySettings(msg.settings);
      buildSliders(msg.settings);
      break;
    case "state":
      setState(msg.to);
      log(`${msg.from} -> ${msg.to}  (${msg.reason})`);
      break;
    case "wake":
      // No chirp for a provisional wake. If it turns out to be nothing, the
      // whole thing should leave no trace -- a beep every time the TV says
      // something jarvis-shaped is worse than missing the odd wake.
      if (!msg.provisional) earcon();
      log(
        `WAKE  score ${msg.score}${msg.provisional ? "  (provisional)" : ""}`,
        msg.provisional ? "warn" : "ok",
      );
      liveBubble = null;
      break;

    case "wake_rejected":
      log(`not addressed to Jarvis, ignoring: "${msg.text}"`, "warn");
      break;

    case "brain_ready":
      if (msg.brain) {
        ui.phaseLabel.textContent = `phase 2 · ${msg.brain}`;
        log(`brain: ${msg.brain}`, "ok");
        log(`tools: ${msg.tools?.length ? msg.tools.join(", ") : "none"}`);
      } else {
        ui.phaseLabel.textContent = "phase 2 · no brain";
        log(`no brain available: ${msg.error ?? "unknown"}`, "err");
      }
      break;

    case "transcript":
      if (msg.text?.trim()) {
        bubble("user", msg.text.trim());
        ui.turns.textContent = String(++turns);
        showCaption("user", "You", msg.text.trim());
      }
      break;

    case "tool_call": {
      const args = typeof msg.args === "string" ? msg.args : JSON.stringify(msg.args ?? {});
      toolBubbles.set(msg.name, bubble("tool", `${msg.name} ${args === "{}" ? "" : args}`));
      // Whatever the model says after the tool is a new thought; keep it
      // below the call instead of appending to the "let me check" bubble.
      liveBubble = null;
      showCaption("tool", "Running tool", `${msg.name}…`);
      log(`tool ${msg.name} ${args}`);
      break;
    }
    case "tool_result": {
      const el = toolBubbles.get(msg.name);
      if (el) {
        el.dataset.ok = msg.ok ? "1" : "0";
        el.textContent += `  ·  ${msg.ok ? "done" : "failed"} in ${msg.ms} ms`;
      }
      log(`tool ${msg.name} ${msg.ok ? "ok" : "FAILED"} (${msg.ms} ms): ${msg.preview}`,
        msg.ok ? "ok" : "err");
      break;
    }
    case "event":
      bubble("event", msg.kind === "motion" ? "Motion detected" :
        msg.kind === "timer" ? `Timer done${msg.data?.label ? ": " + msg.data.label : ""}` :
        msg.kind);
      log(`event ${msg.kind} ${JSON.stringify(msg.data)}`, "warn");
      liveBubble = null;
      break;

    case "assistant_delta":
      if (!liveBubble) liveBubble = bubble("assistant", "");
      liveBubble.textContent += msg.delta;
      showCaption("assistant", "Jarvis", liveBubble.textContent);
      ui.convo.scrollTop = ui.convo.scrollHeight;
      break;
    case "speech_start":
      log("speech detected");
      break;
    case "utterance":
      log(
        `utterance #${msg.index}: ${msg.seconds}s, speech ${msg.speech_ms}ms ` +
          `(${msg.reason})${msg.wav ? " -> " + msg.wav : ""}`,
        "ok",
      );
      break;
    case "follow_up":
      log(`follow-up window open for ${msg.ms}ms`);
      liveBubble = null;
      break;
    case "level":
      renderLevel(msg);
      break;
    case "tts_begin":
      player.beginTurn(msg.turn_id);
      player.startReporting();
      break;
    case "tts_end": {
      // The server stays in SPEAKING until we confirm the queue drained, so
      // keep reporting until it really has. tts_end only means the server
      // has finished *sending* -- there can be ten seconds still queued.
      const poll = setInterval(() => {
        if (!player.playing) {
          clearInterval(poll);
          player.stopReporting();
          player.report();
        }
      }, 200);
      break;
    }
    case "cancel":
      log(`cancel (${msg.reason}) after ${player.flush(msg.turn_id)} samples`, "warn");
      player.stopReporting();
      break;
    case "config_ack":
      if (msg.changed.length) {
        applySettings(msg.settings);
        log(`tuned: ${msg.changed.join(", ")}`);
      }
      break;
    case "pong":
      lastPong = performance.now() - msg.ts;
      break;
    case "error":
      log(`${msg.code}: ${msg.message}`, "err");
      break;
  }
}

function applySettings(s) {
  if (!s) return;
  wakeThreshold = s.wake_threshold;
  vadThreshold = s.vad_threshold;
  ui.wakeThresh.style.left = `${wakeThreshold * 100}%`;
  ui.vadThresh.style.left = `${vadThreshold * 100}%`;
  ui.wakeThreshVal.textContent = wakeThreshold.toFixed(2);
  ui.vadThreshVal.textContent = vadThreshold.toFixed(2);
  if (scope) { scope.threshold = wakeThreshold; scope.draw(); }
}

function setStat(el, text, bad = false) {
  el.textContent = text;
  el.dataset.bad = bad ? "1" : "0";
}

function renderLevel(msg) {
  // Show the live score and the 4 s peak. A single frame flashes past too
  // fast to read; the peak is what tells you whether "Jarvis" nearly fired
  // or was nowhere close.
  const peak = msg.wake_peak ?? msg.wake;
  ui.wakeVal.textContent = msg.wake.toFixed(2);
  ui.wakePeakVal.textContent = peak.toFixed(2);
  ui.wakeVal.dataset.hot = msg.wake >= wakeThreshold ? "1" : "0";
  ui.wakeVal.dataset.near = peak >= wakeThreshold * 0.6 ? "1" : "0";
  ui.wakeFill.style.width = `${msg.wake * 100}%`;
  ui.wakeFill.dataset.hot = msg.wake >= wakeThreshold ? "1" : "0";
  ui.wakePeak.style.left = `${Math.min(100, peak * 100)}%`;

  ui.vadVal.textContent = msg.vad.toFixed(2);
  ui.vadFill.style.width = `${msg.vad * 100}%`;
  ui.vadFill.dataset.hot = msg.voiced ? "1" : "0";
  ui.vadVal.dataset.hot = msg.voiced ? "1" : "0";
  ui.vadState.textContent = msg.voiced ? "speech" : "silent";

  const db = msg.dbfs;
  ui.meterFill.style.width =
    `${db === null ? 0 : Math.max(0, Math.min(100, ((db + 60) / 60) * 100))}%`;
  ui.meterFill.dataset.hot = db !== null && db > -6 ? "1" : "0";
  ui.meterDb.textContent = db === null ? "−∞" : db.toFixed(1);
  grid?.setLevel(db);
  const lvl = db === null ? 0 : Math.max(0, Math.min(1, (db + 60) / 60));
  ui.core.style.setProperty("--lvl", lvl.toFixed(3));
  fx?.setLevel(lvl);
  scope?.push(msg.wake, msg.vad, lvl);

  const underruns = player?.stats.underruns ?? 0;
  setStat(ui.statSent, framesSent.toLocaleString());
  setStat(ui.statRecv, msg.frames.toLocaleString());
  setStat(ui.statDropped, String(msg.dropped), msg.dropped > 0);
  setStat(ui.statNoise, `${msg.noise_dbfs} dB`);
  setStat(ui.statUnderruns, String(underruns), underruns > 0);
  setStat(ui.statRtt, `${lastPong.toFixed(0)} ms`, lastPong > 150);
}

function buildSliders(settings) {
  if (!settings || ui.sliders.childElementCount) return;
  for (const [key, min, max, step, label] of SLIDERS) {
    const row = document.createElement("div");
    row.className = "slider";
    row.innerHTML =
      `<label>${label}<b>${settings[key]}</b></label>` +
      `<input type="range" min="${min}" max="${max}" step="${step}" ` +
      `value="${settings[key]}">`;
    const input = row.querySelector("input");
    const out = row.querySelector("b");
    input.oninput = () => {
      out.textContent = input.value;
      send({ t: "config", patch: { [key]: Number(input.value) } });
    };
    ui.sliders.append(row);
  }
}

async function toggleMic() {
  if (micOn) {
    if (talkOn) setTalk(false);
    await mic.stop();
    mic = null;
    micOn = false;
    grid?.setLevel(null);
    fx?.setLevel(0);
    ui.core.style.setProperty("--lvl", "0");
    setState("OFFLINE");
    ui.micLabel.textContent = "Start mic";
    ui.micIcon.setAttribute("href", "#i-mic");
    ui.micBtn.dataset.on = "0";
    log("mic stopped");
    return;
  }

  await player.resume();
  mic = new MicCapture({
    sampleRate: 16000,
    onFrame: (pcm, seq) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(packMic(seq, 0, pcm));
        framesSent++;
      }
    },
  });

  try {
    const info = await mic.start();
    micOn = true;
    setState("IDLE");
    ui.micLabel.textContent = "Stop mic";
    ui.micIcon.setAttribute("href", "#i-mic-off");
    ui.micBtn.dataset.on = "1";
    log(`mic on - ${info.sampleRate} Hz, AEC ${info.aec}`, "ok");
    if (info.sampleRate !== 16000)
      log(`WARNING: browser gave ${info.sampleRate} Hz, not 16000`, "err");
    if (info.aec === false)
      log("WARNING: echo cancellation is OFF - use headphones", "err");
    sendHello();
  } catch (err) {
    log(`mic failed: ${err.message}`, "err");
  }
}

// Talk on: the mic stays open and anything said gets an answer -- the server
// starts a turn on speech itself, so there is no wake word and no button to hold.
function setTalk(on) {
  talkOn = on;
  send({ t: "converse", on });
  ui.talkBtn.dataset.on = on ? "1" : "0";
  ui.talkBtn.classList.toggle("ghost", !on);
  ui.talkLabel.textContent = on ? "Talking — tap to end" : "Talk";
  setState(ui.state.dataset.state);
  log(on ? "talk on - just speak" : "talk off", "ok");
}

// The big caption under the core: whatever is being said right now. Long
// answers keep only their tail, so the latest words are always the visible ones.
function showCaption(role, label, text) {
  const MAX = 240;
  if (text.length > MAX) {
    const cut = text.slice(-MAX);
    text = "…" + cut.slice(cut.indexOf(" ") + 1);
  }
  ui.caption.dataset.role = role;
  ui.captionRole.textContent = label;
  ui.captionText.textContent = text;
  ui.caption.dataset.live = "1";
  clearTimeout(captionTimer);
  captionTimer = setTimeout(() => (ui.caption.dataset.live = "0"), 9000);
}

function setTheme(name) {
  if (!(name in THEMES)) name = "arc";
  document.documentElement.dataset.theme = name;
  try { localStorage.setItem("jarvis-theme", name); } catch {}
  for (const b of document.querySelectorAll("[data-theme-pick]"))
    b.setAttribute("aria-checked", String(b.dataset.themePick === name));
  ui.themeName.textContent = THEMES[name];
  fx?.setTheme(name);
  scope?.draw();
}

function setFocus(on) {
  document.body.dataset.focus = on ? "1" : "0";
  try { localStorage.setItem("jarvis-focus", on ? "1" : "0"); } catch {}
}

async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen({ navigationUI: "hide" });
  } catch (err) {
    log(`full screen unavailable: ${err.message}`, "warn");
  }
}

function initChrome() {
  for (const b of document.querySelectorAll("[data-theme-pick]"))
    b.onclick = () => setTheme(b.dataset.themePick);
  ui.fsBtn.onclick = toggleFullscreen;
  ui.focusBtn.onclick = () => setFocus(document.body.dataset.focus !== "1");
  document.addEventListener("fullscreenchange", () =>
    ui.fsIcon.setAttribute("href", document.fullscreenElement ? "#i-min" : "#i-max"));

  const keys = Object.keys(THEMES);
  addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.target.closest?.("input, textarea")) return;
    const k = e.key.toLowerCase();
    if (k >= "1" && k <= String(keys.length)) setTheme(keys[Number(k) - 1]);
    else if (k === "f") toggleFullscreen();
    else if (k === "z") setFocus(document.body.dataset.focus !== "1");
  });

  const started = Date.now();
  const pad = (n) => String(n).padStart(2, "0");
  const tick = () => {
    ui.clock.textContent = new Date().toTimeString().slice(0, 8);
    const s = Math.floor((Date.now() - started) / 1000);
    const h = Math.floor(s / 3600);
    ui.uptime.textContent = (h ? `${h}:` : "") + `${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
  };
  tick();
  setInterval(tick, 1000);

  let saved = "arc", focus = "0";
  try {
    saved = localStorage.getItem("jarvis-theme") ?? saved;
    focus = localStorage.getItem("jarvis-focus") ?? focus;
  } catch {}
  setTheme(saved);
  setFocus(focus === "1");
}

function initTabs() {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.onclick = () => {
      for (const t of document.querySelectorAll(".tab")) t.classList.remove("active");
      for (const p of document.querySelectorAll(".tabpane")) p.classList.remove("active");
      tab.classList.add("active");
      $(`tab-${tab.dataset.tab}`).classList.add("active");
    };
  }
}

async function main() {
  player = new PcmPlayer({ sampleRate: 24000, prebufferMs: 150, onReport: send });
  grid = new DotGrid(ui.grid, { rowCount: 17, columnCount: 17 });
  fx = new Backdrop($("fx"));
  fx.anchor = ui.core;
  scope = new Scope($("scope"));
  // While Jarvis talks the bars come from what is actually leaving the
  // speakers; otherwise the grid falls back to its level-driven shapes.
  grid.getBands = () => (player?.playing ? player.bands(8) : null);
  initTabs();
  initChrome();

  ui.micBtn.onclick = toggleMic;
  ui.stopBtn.onclick = () => send({ t: "stop_audio" });
  ui.talkBtn.onclick = async () => {
    if (!talkOn && !micOn) await toggleMic();
    if (talkOn || micOn) setTalk(!talkOn);
  };

  setInterval(() => send({ t: "ping", ts: performance.now() }), 5000);
  connect();
  log(`output latency ${Math.round(player.outputLatency * 1000)} ms`);
}

main();
