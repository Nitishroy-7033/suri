// The PC as a hologram panel: dials for volume and brightness, media keys,
// Wi-Fi, and live gauges for CPU, GPU, memory, battery and temperature.
//
// The dials act at once (POST /api/pc, no model in the loop). Each is a real
// range input under the ring, so a mouse drags it, the wheel nudges it, and
// a pinching hand slides it through pointer.js's slider adapter. The gauges
// are the diagnostics agent's snapshot -- the same numbers as the Systems view.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const ic = (id) => `<svg class="ic"><use href="#${id}"/></svg>`;
const ARC = 2 * Math.PI * 50 * 0.75;  // a 270° ring of radius 50

function dial(control, label, icon) {
  return `
    <div class="ws-dial" data-control="${control}">
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <circle class="track" cx="60" cy="60" r="50" stroke-dasharray="${ARC} 999"/>
        <circle class="val" cx="60" cy="60" r="50" stroke-dasharray="0 999"/>
      </svg>
      <div class="ws-dial-read">${ic(icon)}<b>–</b><span>${label}</span></div>
      <input type="range" min="0" max="100" step="1" aria-label="${label}" />
      <div class="ws-dial-btns">
        <button type="button" class="ws-ibtn" data-step="-10" title="Down">−</button>
        ${control === "volume" ? `<button type="button" class="ws-ibtn" data-act="mute" title="Mute">${ic("i-mute")}</button>` : ""}
        <button type="button" class="ws-ibtn" data-step="10" title="Up">+</button>
      </div>
    </div>`;
}

const gauge = (key, label) => `<div class="ws-gauge" data-g="${key}"><span>${label}</span><b>–</b><i><u></u></i></div>`;

export async function openPc(ctx) {
  const box = document.createElement("div");
  box.className = "ws-pc";
  box.innerHTML = `
    <div class="ws-dials">${dial("volume", "Volume", "i-volume")}${dial("brightness", "Brightness", "i-sun")}</div>
    <div class="ws-media">
      <button type="button" class="ws-ibtn big" data-media="previous" title="Previous">${ic("i-prev")}</button>
      <button type="button" class="ws-ibtn big" data-media="play_pause" title="Play / pause">${ic("i-play")}</button>
      <button type="button" class="ws-ibtn big" data-media="next" title="Next">${ic("i-next")}</button>
      <span class="grow"></span>
      <span class="ws-wifi">${ic("i-wifi")}<em>…</em></span>
    </div>
    <div class="ws-gauges">
      ${gauge("cpu", "CPU")}${gauge("gpu", "GPU")}${gauge("ram", "Memory")}${gauge("battery", "Battery")}${gauge("temp", "CPU temp")}${gauge("disk", "Disk")}
    </div>
    <p class="ws-note ws-pc-err" hidden></p>`;
  const w = ctx.wm.open({ id: "pc", kind: "pc", title: "PC controls", content: box, w: 440, h: 470 });
  if (w.content !== box) return;
  const err = box.querySelector(".ws-pc-err");

  function show(state) {
    if (!state) return;
    for (const d of box.querySelectorAll(".ws-dial")) {
      const c = d.dataset.control;
      const v = state[c];
      if (v === undefined) continue;
      const range = d.querySelector("input");
      d.classList.toggle("off", v === null);
      if (v !== null && document.activeElement !== range && !d.classList.contains("busy")) range.value = v;
      paint(d, v, c === "volume" && state.muted);
    }
    const wifi = state.wifi;
    box.querySelector(".ws-wifi em").textContent =
      wifi === undefined ? "…" : !wifi ? "no Wi-Fi" : wifi.connected ? `${wifi.ssid ?? "connected"}${wifi.signal ? ` · ${wifi.signal}%` : ""}` : "disconnected";
    const problems = [state.volume_error, state.brightness_error].filter(Boolean);
    err.hidden = !problems.length;
    err.textContent = problems.join(" · ");
  }

  function paint(d, v, muted = false) {
    d.querySelector(".val").setAttribute("stroke-dasharray", `${v == null ? 0 : (ARC * v) / 100} 999`);
    d.querySelector("b").textContent = v == null ? "n/a" : muted ? "muted" : `${Math.round(v)}`;
    d.classList.toggle("muted", !!muted);
  }

  async function set(body) {
    try {
      const res = await ctx.api("/api/pc", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      show(await res.json());
    } catch (e) {
      err.hidden = false;
      err.textContent = e.detail || e.message;
    }
  }

  // Dragging a dial sends at most ~8 changes a second, and always the last.
  const timers = {};
  for (const d of box.querySelectorAll(".ws-dial")) {
    const c = d.dataset.control, range = d.querySelector("input");
    range.addEventListener("input", () => {
      paint(d, Number(range.value));
      d.classList.add("busy");
      clearTimeout(timers[c]);
      timers[c] = setTimeout(() => { d.classList.remove("busy"); set({ control: c, value: Number(range.value) }); }, 120);
    });
    d.addEventListener("wheel", (e) => {
      e.preventDefault();
      range.value = String(Math.max(0, Math.min(100, Number(range.value) + (e.deltaY < 0 ? 2 : -2))));
      range.dispatchEvent(new Event("input"));
    }, { passive: false });
  }
  box.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    const d = b.closest(".ws-dial");
    if (b.dataset.step) set({ control: d.dataset.control, change: Number(b.dataset.step) });
    else if (b.dataset.act === "mute") set({ control: "mute", action: "toggle" });
    else if (b.dataset.media) set({ control: "media", action: b.dataset.media });
  });

  // Gauges from the diagnostics feed, while this window is open.
  const setGauge = (key, pct, text) => {
    const g = box.querySelector(`[data-g="${key}"]`);
    g.hidden = pct == null && text == null;
    g.querySelector("b").textContent = text ?? (pct == null ? "–" : `${Math.round(pct)}%`);
    g.querySelector("u").style.width = `${Math.max(0, Math.min(100, pct ?? 0))}%`;
    // A full battery is good news; a full CPU is not.
    g.dataset.level = key === "battery"
      ? (pct <= 10 ? "bad" : pct <= 20 ? "warn" : "ok")
      : pct >= 90 ? "bad" : pct >= 75 ? "warn" : "ok";
  };
  const offDiag = ctx.onDiag((s) => {
    if (!s) return;
    setGauge("cpu", s.cpu?.pct);
    setGauge("gpu", s.gpu?.pct ?? null);
    setGauge("ram", s.ram?.pct);
    setGauge("battery", s.battery?.pct ?? null, s.battery ? `${s.battery.pct}%${s.battery.plugged ? " ⚡" : ""}` : null);
    const t = s.cpu?.temp_c ?? s.gpu?.temp_c;
    setGauge("temp", t == null ? null : Math.min(100, t), t == null ? null : `${Math.round(t)}°C`);
    setGauge("disk", s.disk?.pct, s.disk ? `${s.disk.free_gb} GB free` : null);
  });

  // Volume keys on the keyboard change things too: re-read now and then.
  const poll = setInterval(async () => {
    if (!ctx.wm.has("pc")) return;
    try { show(await (await ctx.api("/api/pc")).json()); } catch {}
  }, 5000);
  w.onClose = () => { clearInterval(poll); offDiag(); };
  try { show(await (await ctx.api("/api/pc")).json()); } catch (e) { err.hidden = false; err.textContent = e.detail || e.message; }
}
