// Jarvis, as the workshop shows him: a glowing orb that breathes when idle,
// swirls while thinking and pulses with the voice. Tap it for the mic; drop
// a file on it to ask about the file.

export function createOrb(parent, { bands, onTap }) {
  const el = document.createElement("button");
  el.type = "button";
  el.className = "ws-orb";
  el.title = "Jarvis: tap for the mic · drop a file here to ask about it";
  el.setAttribute("aria-label", "Jarvis microphone");
  el.innerHTML = `<i class="core"></i><i class="ring r1"></i><i class="ring r2"></i><i class="ring r3"></i><b class="ws-orb-label"></b>`;
  parent.append(el);
  el.onclick = () => onTap?.();

  let raf = 0, level = 0, running = true;
  function tick() {
    if (!running) return;
    const b = bands?.(6);
    const target = b ? b.reduce((a, v) => a + v, 0) / b.length : 0;
    level += (target - level) * 0.25;
    el.style.setProperty("--lvl", level.toFixed(3));
    raf = requestAnimationFrame(tick);
  }
  raf = requestAnimationFrame(tick);

  return {
    el,
    setState(state, word) {
      el.dataset.state = state;
      el.querySelector(".ws-orb-label").textContent = word || "";
    },
    setPaused(on) {
      running = !on;
      cancelAnimationFrame(raf);
      if (running) raf = requestAnimationFrame(tick);
    },
  };
}
