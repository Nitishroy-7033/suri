// The workshop guide: every hand gesture (with a moving picture of it), the
// things you can say, and the keyboard and mouse shortcuts. Opens with ?, the
// Guide button, or "Jarvis, show me the guide". The Hands tab starts the
// interactive tutorial.

import { gestureSvg } from "../poses.js";
import { MOUSE, PAGE_KEYS, WORKSHOP_KEYS } from "../keys.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const GESTURES = [
  ["point", "Point", "Only your index finger out. The ring on screen is your cursor. Hold still for a second on a part or a file to select it."],
  ["pinch", "Pinch", "Thumb and index together is a click: press buttons, flip switches, pick from lists, grab a window by its title bar."],
  ["drag", "Pinch and drag", "Turn the model. Let go while moving to give it a spin. On a slider or dial, pinch and slide."],
  ["fist", "Fist and move", "Slide the model around the room."],
  ["spread", "Two-hand pinch, apart or together", "Zoom the model. Do it on one window to resize that window."],
  ["twist", "Two-hand pinch, turn", "Roll the model, like turning a steering wheel."],
  ["palm", "Open palm", "Let go of everything and stop the spin."],
  ["swipe", "Swipe left or right", "An open hand, fast: left for the next model, right for the one before. In a folder window, it turns the page."],
  ["thumb", "Thumbs up, held half a second", "Say yes to the question on screen, like moving a file to the Recycle Bin. It never approves anything else."],
];

const TIPS = [
  "Good light on your hands matters most. A window behind you makes them hard to see.",
  "Sit about an arm's length from the camera. The middle of the camera's view covers the whole screen, so small moves are enough.",
  "The camera preview in the corner shows what Jarvis sees. Turn it and the skeleton off in Settings → Workshop.",
  "If the cursor shakes, raise Hand smoothing in Settings → Workshop. If it lags, lower it.",
];

const VOICE = [
  ["The hologram", ["Show the engine", "Explode it", "Put it back together", "Highlight the piston", "What's this?", "Spin it", "Stop spinning", "Zoom in", "Next model", "Reset the view"]],
  ["Windows", ["Open settings", "Open the PC controls", "Show the system gauges", "Open the chat", "Arrange the windows", "Close all windows", "Close the workshop"]],
  ["Files (FS_ENABLED=true)", ["Open my Downloads", "Open the E drive", "Find my resume PDF", "What's in this file?", "Delete this: asks you first"]],
  ["The PC", ["Volume 30", "Louder", "Mute", "Brightness down", "Pause the music", "Next song"]],
  ["New models (with API keys)", ["Find me a drone", "Build me an arc reactor", "Make a 3D model of this", "How's the model coming?"]],
  ["Help", ["Show me the guide", "Start the hand tutorial"]],
];

const keyRow = ({ keys, label }) =>
  `<tr><td>${keys.map((k) => `<kbd>${esc(k)}</kbd>`).join(" ")}</td><td>${esc(label)}</td></tr>`;

export function openGuide(ctx, tab = "hands") {
  if (ctx.wm.has("guide")) { ctx.wm.focus("guide"); show(ctx.wm.get("guide").content, tab); return; }
  const box = document.createElement("div");
  box.className = "ws-guide";
  box.innerHTML = `
    <nav class="ws-tabs" role="tablist">
      <button type="button" role="tab" data-tab="hands">Hands</button>
      <button type="button" role="tab" data-tab="voice">Voice</button>
      <button type="button" role="tab" data-tab="keys">Keys &amp; mouse</button>
    </nav>
    <section data-pane="hands">
      <div class="ws-guide-start">
        <p><b>New to hand control?</b> The tutorial walks you through each gesture with your camera, about two minutes.</p>
        <button type="button" class="ws-btn" data-act="tutorial">Start the hand tutorial</button>
      </div>
      <div class="ws-gcards">${GESTURES.map(([g, name, what]) => `
        <article class="ws-gcard">${gestureSvg(g)}<div><b>${esc(name)}</b><p>${esc(what)}</p></div></article>`).join("")}
      </div>
      <h4>Tips</h4>
      <ul class="ws-tips">${TIPS.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
    </section>
    <section data-pane="voice" hidden>
      <p class="ws-note">Say “Hey Jarvis”, then any of these. Or tap the orb, or turn on Talk mode (T) to skip the wake word.</p>
      ${VOICE.map(([group, lines]) => `<h4>${esc(group)}</h4>
        <div class="ws-says">${lines.map((l) => `<button type="button" class="ws-say" data-say="${esc(l.split(":")[0])}" title="Send this to Jarvis">“${esc(l)}”</button>`).join("")}</div>`).join("")}
      <p class="ws-note">Tap a line to send it as a typed message.</p>
    </section>
    <section data-pane="keys" hidden>
      <h4>In the workshop</h4><table class="ws-keys">${WORKSHOP_KEYS.map(keyRow).join("")}</table>
      <h4>Everywhere</h4><table class="ws-keys">${PAGE_KEYS.map(keyRow).join("")}</table>
      <h4>Mouse</h4><table class="ws-keys">${MOUSE.map(keyRow).join("")}</table>
    </section>`;
  box.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.tab) show(box, b.dataset.tab);
    else if (b.dataset.act === "tutorial") ctx.openPanel("tutorial");
    else if (b.dataset.say) { ctx.ask(b.dataset.say); ctx.toast(`Asked Jarvis: “${b.dataset.say}”`); }
  });
  ctx.wm.open({ id: "guide", kind: "guide", title: "Guide", content: box, w: 600, h: 620 });
  show(box, tab);
}

function show(box, tab) {
  if (!box.querySelector(`[data-pane="${tab}"]`)) tab = "hands";
  for (const b of box.querySelectorAll("[data-tab]")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  for (const p of box.querySelectorAll("[data-pane]")) p.hidden = p.dataset.pane !== tab;
}
