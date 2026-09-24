import { createCoach } from "./tutorial-steps.js";
import { gestureSvg } from "./poses.js";

// The hands tutorial: a coach card at the bottom of the workshop that walks
// through each gesture with your real camera. It watches what hands.js sees
// and moves on by itself when you've done the step (Skip if one won't take).

const TUTORIAL_KEY = "jarvis-holo-tutorial-done";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

let current = null;  // one tutorial at a time

export function tutorialDone() {
  try { return localStorage.getItem(TUTORIAL_KEY) === "1"; } catch { return false; }
}

export async function startTutorial(ctx) {
  if (current) { current.el.classList.add("nudge"); setTimeout(() => current?.el.classList.remove("nudge"), 500); return; }
  const coach = createCoach();
  const el = document.createElement("section");
  el.className = "ws-coach";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-label", "Hands tutorial");
  ctx.root.append(el);
  let lastLive = "", advancing = false, lastFrame = 0;
  const offs = [];
  current = { el, stop };

  function render() {
    const s = coach.step;
    el.innerHTML = `
      <div class="ws-coach-gfx">${gestureSvg(s.gfx)}</div>
      <div class="ws-coach-body">
        <small>Hands tutorial · step ${coach.index + 1} of ${coach.total}</small>
        <b>${esc(s.title)}</b>
        <p>${esc(s.say)}</p>
        <div class="ws-coach-bar"><u></u></div>
        <p class="ws-coach-live" aria-live="polite"></p>
      </div>
      <div class="ws-coach-btns">
        ${ctx.handsRunning ? "" : `<button type="button" class="ws-btn" data-act="hands">Turn on hands</button>`}
        <button type="button" class="ws-btn" data-act="back"${coach.index ? "" : " disabled"}>Back</button>
        <button type="button" class="ws-btn" data-act="skip">Skip</button>
        <button type="button" class="ws-btn danger" data-act="end">End</button>
      </div>`;
    el.classList.remove("win");
    paint();
  }

  function paint(live) {
    el.querySelector(".ws-coach-bar u").style.width = `${Math.round(coach.progress * 100)}%`;
    if (live !== undefined && live !== lastLive) {
      lastLive = live;
      el.querySelector(".ws-coach-live").textContent = live;
    }
  }

  function complete() {
    if (advancing) return;
    advancing = true;
    el.classList.add("win");
    paint("Got it!");
    setTimeout(() => {
      advancing = false;
      if (coach.next()) { lastLive = ""; render(); } else finish();
    }, 900);
  }

  function finish() {
    try { localStorage.setItem(TUTORIAL_KEY, "1"); } catch {}
    el.innerHTML = `
      <div class="ws-coach-gfx">${gestureSvg("thumb")}</div>
      <div class="ws-coach-body">
        <small>Hands tutorial · done</small>
        <b>You're ready</b>
        <p>Press <kbd>?</kbd> any time for every gesture, voice command and shortcut, or say “Jarvis, show me the guide”.</p>
      </div>
      <div class="ws-coach-btns"><button type="button" class="ws-btn" data-act="guide">Open the guide</button>
        <button type="button" class="ws-btn" data-act="end">Close</button></div>`;
    el.classList.add("win");
  }

  // What the camera sees, in a few words, so you know why a step isn't taking.
  function describe(hands) {
    const v = hands.filter((h) => h.visible);
    if (!ctx.handsRunning) return "Hand control is off: press “Turn on hands” (or G).";
    if (!v.length) return "No hand in view yet. Try more light, and keep your hand inside the camera picture.";
    const h = v[0];
    const what = h.pinch ? "pinching" : h.fist ? "a fist" : h.pointing ? "pointing" : h.palm ? "an open hand" : "a hand";
    return `${v.length === 2 ? "Two hands" : "One hand"} seen: ${what}.`;
  }

  offs.push(ctx.onHandFrame((hands) => {
    if (advancing || coach.finished) return;
    const now = performance.now();
    if (coach.frame(hands, now)) complete();
    if (now - lastFrame > 150) { lastFrame = now; paint(describe(hands)); } else paint();
  }));
  offs.push(ctx.onGestureEvent((name) => { if (!advancing && !coach.finished && coach.gesture(name)) complete(); else paint(); }));
  offs.push(ctx.onSelectEvent((part, info) => { if (!advancing && !coach.finished && coach.select(part, info)) complete(); else paint(); }));

  el.addEventListener("click", async (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "end") stop();
    else if (act === "skip") { if (coach.next()) render(); else finish(); }
    else if (act === "back") { coach.back(); render(); }
    else if (act === "guide") { stop(); ctx.openPanel("guide"); }
    else if (act === "hands") { await ctx.toggleHands(true); render(); }
  });

  function stop() {
    offs.forEach((off) => off());
    el.remove();
    current = null;
  }

  render();
  if (!ctx.handsRunning) {
    await ctx.toggleHands(true);
    if (current) render();
  }
}

export function stopTutorial() { current?.stop(); }
