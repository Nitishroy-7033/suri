import { packMic, unpackTts } from "./protocol.js";
import { MicCapture } from "./mic.js";
import { PcmPlayer } from "./player.js";
import { createRobot } from "./robot-3d/robot-react/robotEngine.js";
import { createDirector } from "./director.js";
import { initSettings } from "./settings.js";
import { MicTest } from "./mictest.js";
import { createChat } from "./chat.js";
import { createDiag } from "./diag.js";

// Companion layout: the robot and its state in one pane, the conversation in
// the other. Voice states become moods, server events become gestures and
// cards, and the mouth follows the audio actually being played. Layout,
// preferences and the settings drawer live in settings.js.
const $ = (id) => document.getElementById(id);
const ui = {
  robot: $("robot"), status: $("status"), hint: $("hint"), caption: $("caption"), level: $("level"),
  conn: $("conn"), connLabel: $("conn-label"), brain: $("brain"),
  miniStatus: $("mini-status").querySelector("span"),
  micBtn: $("mic-btn"), micLabel: $("mic-label"), composerMic: $("composer-mic"),
  talkBtn: $("talk-btn"), stopBtn: $("stop-btn"),
  convo: $("convo"), empty: $("empty"), clearBtn: $("clear-btn"),
  composer: $("composer"), input: $("input"), sendBtn: $("send-btn"),
};
const bars = [...ui.level.children];

const bot = createRobot(ui.robot, { mood: "sleepy", voice: false, look: "wander" });
// Moods, gestures and arm poses cued by what is said, plus idle fidgets.
const director = createDirector(bot);
window.bot = bot;  // handy for poking at it from the console

const MOOD_FOR_STATE = {
  OFFLINE: "sleepy",
  CONNECTING: "thinking",
  DISCONNECTED: "angry",
  IDLE: "idle",
  LISTENING: "listening",
  FOLLOW_UP_WINDOW: "listening",
  THINKING: "thinking",
  SPEAKING: "talking",
};

const LABELS = {
  OFFLINE: ["Sleeping", "Tap the robot or press the mic to start"],
  CONNECTING: ["Connecting…", ""],
  DISCONNECTED: ["Connection lost", "Retrying…"],
  IDLE: ["Ready", 'Say "Hey Jarvis", or type a message'],
  LISTENING: ["Listening…", ""],
  FOLLOW_UP_WINDOW: ["Listening…", "Go ahead — no wake word needed"],
  THINKING: ["Thinking…", ""],
  SPEAKING: ["Speaking", "Tap the robot or press Esc to interrupt"],
};

let ws = null, mic = null, player = null, micOn = false;
let micMuted = false;  // true while the mic test has the microphone
let talkOn = false;  // conversation mode: answer anything said, no wake word
let state = "OFFLINE";
let lastTyped = "";  // for "Retry" when a typed message fails
let captionTimer = 0;

const settings = initSettings({
  bot, director, send,
  onDeviceChange: restartMic,
  openMicTest: () => micTest.open(),
});
const micTest = new MicTest($("mictest"), {
  deviceId: () => settings.prefs.deviceId || null,
  playTone: () => chime(),
  onBusy: (busy) => { micMuted = busy; },
});

// ---------- state ----------

function setState(name) {
  state = name;
  // With the mic off nothing can be heard, so a follow-up window after a
  // typed reply is just "ready", not "listening".
  const shown = !micOn && (name === "FOLLOW_UP_WINDOW" || name === "LISTENING") ? "IDLE" : name;
  document.body.dataset.state = shown;
  const idleTalk = talkOn && shown === "IDLE";
  bot.setMood(idleTalk ? "listening" : MOOD_FOR_STATE[shown] ?? "idle");
  bot.setSpeaking(shown === "SPEAKING", () => player?.bands(8));
  director.state(shown);
  chat.thinking(shown === "THINKING");

  const [word, hint] = LABELS[shown] ?? [shown, ""];
  ui.status.textContent = idleTalk ? "Listening…" : word;
  ui.miniStatus.textContent = ui.status.textContent;
  ui.hint.textContent = idleTalk ? "Just speak — no wake word needed"
    : shown === "IDLE" && !micOn ? "Mic is off — type a message, or press the mic"
    : hint;
}

function setConn(ok, label) {
  ui.conn.dataset.ok = ok ? "1" : "0";
  ui.connLabel.textContent = label;
}

// The line under the robot: whatever is being said right now. Long replies
// keep only their tail, so the newest words are the visible ones.
function caption(text, holdMs = 9000) {
  const MAX = 220;
  if (text.length > MAX) {
    const cut = text.slice(-MAX);
    text = "…" + cut.slice(cut.indexOf(" ") + 1);
  }
  ui.caption.textContent = text;
  clearTimeout(captionTimer);
  captionTimer = setTimeout(() => (ui.caption.textContent = ""), holdMs);
}

// ---------- conversation ----------

// Messages, tool cards and timers live in chat.js.
const chat = createChat({
  convo: ui.convo, empty: ui.empty, onAsk: (t) => ask(t),
  // The web task card's Yes / No / Stop buttons go straight to the server.
  onWeb: (m) => { if (!send(m)) errorCard("Not connected to the server."); },
});
const errorCard = (text, retry = null) => chat.error(text, retry);

// The Systems view (diag.js). Stats only flow while it is on screen.
const diag = createDiag($("diag-body"), {
  send, stamp: $("diag-stamp"),
});
diag.setVisible(document.documentElement.dataset.view === "systems");
new MutationObserver(() => diag.setVisible(document.documentElement.dataset.view === "systems"))
  .observe(document.documentElement, { attributes: true, attributeFilter: ["data-view"] });
$("diag-report").onclick = () => ask("Status report");

// ---------- sounds ----------

function tone(freqs, { dur = 0.16, gap = 0, vol = 0.22 } = {}) {
  if (!player) return;
  const ctx = player.ctx;
  let t = ctx.currentTime;
  for (const [f0, f1] of freqs) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.setValueAtTime(f0, t);
    osc.frequency.exponentialRampToValueAtTime(f1, t + dur * 0.55);
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(vol, t + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + dur + 0.02);
    t += dur + gap;
  }
}

// A short rising blip, generated locally so it fires the instant the wake
// word is detected. Waiting for the server to send audio would add a round
// trip to the one moment that has to feel immediate.
function earcon() {
  if (settings.prefs.chirp) tone([[660, 1180]]);
}

// Speaker test: three notes, loud enough to hear across a room.
async function chime() {
  await player.resume();
  tone([[523, 523], [659, 659], [784, 784]], { dur: 0.22, gap: 0.04, vol: 0.3 });
}

// ---------- socket ----------

// Connected: the robot glows green for a moment, then goes back to your
// accent colour. After a drop it reboots (the boot-up gesture); on the first
// connect it just nods. (Disconnected is the angry mood, which glows red.)
const CONNECTED_GREEN = "#22c55e";
let greenTimer = 0;
function connectedFlash(reconnected) {
  clearTimeout(greenTimer);
  bot.setOptions({ colors: { glow: CONNECTED_GREEN } });
  bot.flash("happy", reconnected ? 3200 : 2200);
  director.play(reconnected ? "boot" : "nod");
  greenTimer = setTimeout(() => bot.setOptions({ colors: { glow: settings.prefs.accent } }), reconnected ? 3400 : 2400);
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  // Retries after a drop stay "disconnected" (angry) instead of flickering
  // to "connecting" once a second while the server is down.
  if (state !== "DISCONNECTED") {
    setState("CONNECTING");
    setConn(false, "connecting");
  }
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    setConn(true, "connected");
    // Out of "disconnected" now, not when "ready" arrives a moment later --
    // otherwise the robot turns angry again right after the green flash.
    const reconnected = state === "DISCONNECTED";
    if (reconnected) setState("CONNECTING");
    connectedFlash(reconnected);
    sendHello();
    if (talkOn) send({ t: "converse", on: true });
    diag.resubscribe();
  };
  ws.onclose = () => {
    setConn(false, "offline · retrying");
    settings.onDisconnect();
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
        console.warn(`bad audio frame: ${err.message}`);
      }
    }
  };
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(msg)); return true; }
  return false;
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
      settings.onReady(msg.settings);
      setState(micOn ? "IDLE" : "OFFLINE");
      break;
    case "config_ack":
      settings.onSettings(msg.settings);
      break;
    case "state":
      setState(msg.to);
      break;
    case "brain_ready":
      ui.brain.textContent = msg.brain ? msg.brain : "no brain";
      ui.brain.title = msg.tools?.length ? `tools: ${msg.tools.join(", ")}` : (msg.error ?? "");
      if (!msg.brain) errorCard(`No AI brain available${msg.error ? ` — ${msg.error}` : ""}. Check the API keys in .env.`);
      break;
    case "wake":
      // No chirp for a provisional wake. If it turns out to be nothing, the
      // whole thing should leave no trace.
      if (!msg.provisional) { earcon(); director.play("surprise"); }
      chat.newTurn();
      director.newTurn();
      break;
    case "wake_rejected":
      bot.flash("confused", 1400);
      break;
    case "transcript": {
      if (!msg.text?.trim()) break;
      // One bubble per turn, however many pieces the words arrive in.
      const said = chat.heard(msg.text, msg.turn_id);
      if (said.trim() === msg.text.trim()) director.newTurn();
      caption(`You: ${said}`);
      director.user(msg.text);
      break;
    }
    case "assistant_delta":
      caption(chat.reply(msg.delta));
      director.reply(msg.delta);
      break;
    case "tool_call":
      // The card also starts a new thought: text after it gets its own message.
      chat.toolCall(msg);
      director.poke();
      director.play("scan");
      break;
    case "tool_result":
      chat.toolResult(msg);
      director.poke();
      if (msg.ok) director.play("nod");
      else { director.play("shake"); bot.flash("confused", 1600); }
      break;
    case "event":
      director.poke();
      if (msg.kind?.startsWith("web_")) { webEvent(msg); break; }
      chat.event(msg);
      if (msg.kind === "diag_alert") { diag.alert(msg); bot.flash(msg.data?.level === "info" ? "happy" : "alert", 2400); }
      if (msg.kind === "timer") director.play("wave");
      else if (msg.kind === "motion") bot.flash("alert", 2000);
      break;
    case "diag":
      diag.update(msg.snap, msg.error);
      break;
    case "follow_up":
      director.endReply();
      chat.newTurn();
      break;
    case "converse":
      setTalkUi(!!msg.on);
      break;
    case "level": {
      const db = msg.dbfs;
      bot.setLevel(db === null ? 0 : (db + 60) / 60);
      break;
    }
    case "tts_begin":
      player.beginTurn(msg.turn_id);
      player.startReporting();
      break;
    case "tts_end": {
      director.endReply();
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
      if (msg.reason !== "superseded") chat.interrupted();
      player.flush(msg.turn_id);
      player.stopReporting();
      break;
    case "error":
      console.warn(`${msg.code}: ${msg.message}`);
      bot.flash("confused", 1600);
      if (msg.code === "sample_rate") errorCard("Your browser gave the wrong mic sample rate. Try Chrome or Edge.");
      else if (msg.code === "busy") errorCard("Still finishing the last reply — try again in a moment.", lastTyped || null);
      else if (/quota|exhausted|429/i.test(msg.message ?? "")) errorCard("The AI's usage quota is used up — check the plan and billing for your API key, or switch the brain in .env.", lastTyped || null);
      else errorCard(msg.message || "Something went wrong.");
      break;
  }
}

// ---------- web agent ----------

// Progress from the separate web agent: the chat card shows it, and the
// robot reacts to the moments that matter.
const webChip = $("web-chip");
webChip.onclick = () => send({ t: "web_stop" });

function webEvent(msg) {
  chat.web(msg);
  const d = msg.data || {};
  // Top-bar chip: which site, while a task runs (visible in every view).
  // Only lifecycle events change it: a late screenshot must not bring it back.
  if (msg.kind !== "web_frame") {
    webChip.hidden = msg.kind === "web_done";
    webChip.dataset.state = msg.kind === "web_question" ? "waiting" : "working";
  }
  if (d.url) {
    try { webChip.querySelector("span").textContent = new URL(d.url).hostname.replace(/^www\./, ""); } catch {}
  } else if (msg.kind === "web_start") {
    webChip.querySelector("span").textContent = "web agent";
  }
  if (msg.kind === "web_start") director.play("scan");
  else if (msg.kind === "web_question") { director.play("surprise"); bot.flash("listening", 2500); }
  else if (msg.kind === "web_done") {
    if (d.state === "done") director.play("nod");
    else if (d.state === "failed") { director.play("shake"); bot.flash("confused", 1800); }
  }
}

// ---------- mic and talk mode ----------

async function toggleMic() {
  if (micOn) {
    if (talkOn) setTalk(false);
    await mic.stop();
    mic = null;
    micOn = false;
    bot.setLevel(0);
    setMicUi();
    setState("OFFLINE");
    return;
  }

  await player.resume();
  mic = new MicCapture({
    sampleRate: 16000,
    deviceId: settings.prefs.deviceId || null,
    onFrame: (pcm, seq) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      // During the mic test the stream keeps flowing, but as silence, so
      // "testing, testing" never wakes Jarvis.
      ws.send(packMic(seq, 0, micMuted ? new Int16Array(pcm.length) : pcm));
    },
  });

  try {
    const info = await mic.start();
    micOn = true;
    setMicUi();
    setState("IDLE");
    settings.refreshDevices();
    if (info.sampleRate !== 16000)
      console.warn(`browser gave ${info.sampleRate} Hz, not 16000`);
    if (info.aec === false)
      errorCard("Echo cancellation is off — use headphones, or Jarvis may hear itself.");
    sendHello();
  } catch (err) {
    mic = null;
    bot.flash("sad", 2000);
    errorCard(err.name === "NotAllowedError"
      ? "Microphone blocked — allow it from the icon in the address bar, then try again."
      : err.name === "NotFoundError" || err.name === "OverconstrainedError"
        ? "That microphone isn't available — plug it in, or pick another in Settings."
      : `Couldn't start the microphone: ${err.message}`);
  }
}

// A different device was picked: if the mic is running, move it over.
async function restartMic() {
  if (!micOn) return;
  const talk = talkOn;
  await toggleMic();
  await toggleMic();
  if (talk && micOn) setTalk(true);
}

function setMicUi() {
  for (const b of [ui.micBtn, ui.composerMic]) b.dataset.on = micOn ? "1" : "0";
  ui.micLabel.textContent = micOn ? "Stop mic" : "Start mic";
}

// Talk on: the mic stays open and anything said gets an answer -- the server
// starts a turn on speech itself, so there is no wake word.
function setTalk(on) {
  send({ t: "converse", on });
  setTalkUi(on);
  if (on) director.play("nod");
}

function setTalkUi(on) {
  talkOn = on;
  ui.talkBtn.dataset.on = on ? "1" : "0";
  setState(state);
}

async function toggleTalk() {
  if (!micOn) { await toggleMic(); if (micOn) setTalk(true); return; }
  setTalk(!talkOn);
}

// ---------- typing ----------

function ask(text) {
  text = text.trim();
  if (!text) return;
  // A click or Enter counts as the gesture audio needs. Not awaited: the
  // message should go out even if the browser holds the audio back.
  player.resume().catch(() => {});
  if (!send({ t: "text", text })) {
    errorCard("Not connected to the server — your message wasn't sent.", text);
    return;
  }
  // No local flush: the server cancels any reply in flight and its "cancel"
  // message flushes the player, so the two can never disagree.
  lastTyped = text;
  director.newTurn();
  chat.typed(text);
  director.user(text);
}

function initComposer() {
  const grow = () => {
    ui.input.style.height = "auto";
    ui.input.style.height = `${ui.input.scrollHeight}px`;
    ui.sendBtn.disabled = !ui.input.value.trim();
  };
  ui.input.addEventListener("input", grow);
  ui.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      ui.composer.requestSubmit();
    }
  });
  ui.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    ask(ui.input.value);
    ui.input.value = "";
    grow();
  });
  ui.convo.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-ask]");
    if (chip) ask(chip.dataset.ask);
  });
  ui.clearBtn.onclick = () => chat.clear();
}

// ---------- robot and keyboard ----------

// Tap: start the mic; while speaking, interrupt; otherwise toggle talk mode.
// Double-tap: stop the mic. Drags rotate the view and are not taps.
async function onTap() {
  if (!micOn) { await toggleMic(); return; }
  if (state === "SPEAKING") send({ t: "stop_audio" });
  else setTalk(!talkOn);
}

function initInput() {
  let down = null, tapTimer = 0;
  ui.robot.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY }; });
  ui.robot.addEventListener("pointerup", (e) => {
    if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6) return;
    down = null;
    if (tapTimer) {
      clearTimeout(tapTimer);
      tapTimer = 0;
      if (micOn) toggleMic();
      return;
    }
    tapTimer = setTimeout(() => { tapTimer = 0; onTap(); }, 260);
  });

  ui.micBtn.onclick = toggleMic;
  ui.composerMic.onclick = toggleMic;
  ui.talkBtn.onclick = toggleTalk;
  ui.stopBtn.onclick = () => send({ t: "stop_audio" });
  $("settings-btn").onclick = settings.toggle;
  $("mictest-btn").onclick = () => micTest.open();

  addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      // Close whatever is open first; only then does Esc mean "stop talking".
      if (settings.isOpen()) { settings.close(); return; }
      if ($("mictest").open) return;  // the dialog closes itself
      send({ t: "stop_audio" });
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
    if (e.target.closest?.("input, textarea, select, dialog")) return;
    const k = e.key.toLowerCase();
    if (k === "m") toggleMic();
    else if (k === "t") toggleTalk();
    else if (k === "v") settings.cycleView();
    else if (k === ",") settings.toggle();
    else if (k === "/") { e.preventDefault(); ui.input.focus(); }
  });
}

// The bars under the status: Jarvis's voice while speaking, your mic
// otherwise. Flat when neither is making sound.
function animateLevel() {
  const src = player?.playing ? player : micOn && !micMuted ? mic : null;
  const bands = src?.bands(bars.length);
  bars.forEach((b, i) => b.style.setProperty("--h", bands ? bands[i].toFixed(3) : "0"));
  requestAnimationFrame(animateLevel);
}

function main() {
  player = new PcmPlayer({ sampleRate: 24000, prebufferMs: 150, onReport: send });
  initInput();
  initComposer();
  animateLevel();
  setInterval(() => send({ t: "ping", ts: performance.now() }), 5000);
  connect();
}

main();
