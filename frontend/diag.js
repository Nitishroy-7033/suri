// The Systems view: the laptop's vital signs and Jarvis's own, drawn from the
// server's {"t":"diag"} snapshots (every ~2 s, only while this view is open).
//
// Layout, top to bottom: one summary line (is anything wrong?), a row of stat
// tiles (value, meter, 2-minute trend), activity (cores, network, processes),
// Jarvis's agents, and the log.
//
// Colour rules: load is drawn in one fixed data hue, never the user's accent
// (an orange accent would read as a warning). Amber and red appear only when a
// threshold is crossed, and always with a word ("High", "Low"), so meaning is
// never colour alone. The skeleton is built once; each snapshot only updates
// text, widths and small lists.

const HISTORY = 60;  // points per trend (~2 minutes at 2 s)

const AGENT_LABELS = {
  voice_llm: "Voice", web_agent: "Web agent", vision_agent: "Vision", diagnostics_agent: "Diagnostics",
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const known = (v) => v !== null && v !== undefined && !Number.isNaN(v);
const num = (v, d = 0) => (known(v) ? Number(v).toFixed(d) : "—");
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

function duration(s) {
  if (!known(s)) return "";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

function rate(kb) {
  if (!known(kb)) return "—";
  return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB/s` : `${Math.round(kb)} KB/s`;
}

const gb = (mb) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`);

/** "ok" | "warn" | "crit" for a 0-100 load. */
const loadLevel = (pct) => (!known(pct) ? "ok" : pct >= 90 ? "crit" : pct >= 75 ? "warn" : "ok");
const LEVEL_WORD = { warn: "High", crit: "Very high" };

const ICON = {
  ok: `<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7"/><path d="m5 8.2 2 2 4-4.4"/></svg>`,
  warn: `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.8 15 14H1z"/><path d="M8 6v3.6M8 11.6v.1"/></svg>`,
  crit: `<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7"/><path d="M8 4.4v4.4M8 11.2v.1"/></svg>`,
  idle: `<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7"/><path d="M5 8h6"/></svg>`,
};

function badge(level, word) {
  return `<span class="badge" data-level="${level}">${ICON[level] || ""}${esc(word)}</span>`;
}

// ---------- trend line with hover ----------

function sparkSvg(values, max, key) {
  const base = `<line class="spark-base" x1="0" x2="100" y1="23" y2="23"/>`;
  if (values.length < 2) return `<svg class="spark" data-spark="${key}" viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true">${base}</svg>`;
  const top = Math.max(max ?? 0, ...values, 1e-9);
  const x = (i) => ((HISTORY - values.length + i) / (HISTORY - 1)) * 100;
  const y = (v) => 22 - (v / top) * 20;
  const pts = values.map((v, i) => `${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(" ");
  const last = values.length - 1;
  return `<svg class="spark" data-spark="${key}" viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true">
    ${base}<polyline points="${pts}"/>
    <line class="spark-now" x1="${x(last)}" x2="${x(last)}" y1="${y(values[last])}" y2="${y(values[last])}"/>
  </svg>`;
}

export function createDiag(el, { send, stamp }) {
  const tile = (key, label) => `
    <article class="tile" data-tile="${key}" data-level="ok">
      <header><span class="tile-label">${label}</span><span class="tile-badge"></span></header>
      <div class="tile-value"><b data-v>—</b><small data-u></small></div>
      <div class="meter" data-m><i></i></div>
      <p class="tile-sub" data-s>&nbsp;</p>
      <div class="tile-trend" data-t></div>
    </article>`;

  el.innerHTML = `
    <div class="dsum" data-k="summary" data-level="idle">
      <span class="dsum-icon" data-k="sum-icon">${ICON.idle}</span>
      <div><b data-k="sum-title">Waiting for readings…</b><span data-k="sum-detail"></span></div>
    </div>

    <h2 class="dsec">Laptop</h2>
    <div class="tiles">
      ${tile("cpu", "CPU")}${tile("ram", "Memory")}${tile("gpu", "GPU")}
      ${tile("vram", "GPU memory")}${tile("battery", "Battery")}${tile("disk", "Disk")}
    </div>

    <h2 class="dsec">Activity</h2>
    <section class="dbox dbox-cores" aria-label="CPU cores">
      <h3>Cores <span data-k="cpu-meta"></span>
        <span class="cores-scale"><span>idle</span><i></i><span>busy</span></span></h3>
      <div class="cores" data-k="cores"></div>
    </section>
    <div class="dgrid">
      <section class="dbox" aria-label="Network">
        <h3>Network <span data-k="net-badge"></span></h3>
        <dl class="facts">
          <div><dt>Ping</dt><dd data-k="ping">—</dd></div>
          <div><dt>Download</dt><dd data-k="down">—</dd></div>
          <div><dt>Upload</dt><dd data-k="up">—</dd></div>
          <div><dt>Disk read / write</dt><dd data-k="disk-rate">—</dd></div>
        </dl>
        <div class="trend-label">Download, last 2 min</div>
        <div data-k="net-spark"></div>
      </section>
      <section class="dbox" aria-label="Top processes">
        <h3>Top processes
          <span class="dtabs" role="tablist">
            <button type="button" role="tab" data-proc="cpu" aria-selected="true">CPU</button>
            <button type="button" role="tab" data-proc="ram" aria-selected="false">Memory</button>
          </span>
        </h3>
        <ol class="procs" data-k="procs"></ol>
      </section>
    </div>

    <h2 class="dsec">Jarvis <span class="dsec-meta" data-k="uptime"></span></h2>
    <div class="vchips" data-k="voice"></div>
    <div class="agents" data-k="agents"></div>
    <dl class="facts facts-grid">
      <div><dt>Local models (Ollama)</dt><dd data-k="ollama">—</dd></div>
      <div><dt>Web agent</dt><dd data-k="web">—</dd></div>
      <div><dt>Reply latency</dt><dd data-k="latency">—</dd></div>
      <div><dt>Memory store</dt><dd data-k="storage">—</dd></div>
      <div><dt>Errors / warnings</dt><dd data-k="errs">—</dd></div>
      <div><dt>Jarvis data on disk</dt><dd data-k="disk-jarvis">—</dd></div>
    </dl>

    <h2 class="dsec">Log</h2>
    <ol class="dlog" data-k="log"><li class="dlog-empty">Nothing to report.</li></ol>

    <div class="dtip" data-k="tip" hidden></div>`;

  const k = (name) => el.querySelector(`[data-k="${name}"]`);
  const tiles = Object.fromEntries([...el.querySelectorAll("[data-tile]")].map((t) => [t.dataset.tile, t]));
  const hist = { cpu: [], ram: [], gpu: [], vram: [], battery: [], disk: [], down: [] };
  const histMax = { cpu: 100, ram: 100, gpu: 100, battery: 100 };
  const histUnit = { cpu: "%", ram: "%", gpu: "%", vram: " GB", battery: "%", disk: " GB free", down: " KB/s" };
  const localAlerts = [];
  let procTab = "cpu";
  let lastSnap = null;
  let visible = false;

  const push = (key, v) => {
    if (!known(v)) return;
    const h = hist[key];
    h.push(v);
    if (h.length > HISTORY) h.shift();
  };

  // ---------- tiles ----------

  /** level: "ok" | "warn" | "crit"; word shown beside a non-ok level. */
  function setTile(key, { value, unit = "", sub = "", pct = null, level = "ok", word = "" }) {
    const t = tiles[key];
    t.dataset.level = level;
    t.querySelector("[data-v]").textContent = value;
    t.querySelector("[data-u]").textContent = unit;
    t.querySelector("[data-s]").textContent = sub || " ";
    t.querySelector(".tile-badge").innerHTML = level !== "ok" && word ? badge(level, word) : "";
    const m = t.querySelector("[data-m]");
    m.hidden = !known(pct);
    if (known(pct)) m.firstElementChild.style.width = `${Math.max(0, Math.min(100, pct))}%`;
    t.querySelector("[data-t]").innerHTML = sparkSvg(hist[key], histMax[key], key);
  }

  function renderSystem(s) {
    const cpu = s.cpu || {}, ram = s.ram || {}, gpu = s.gpu, bat = s.battery, disk = s.disk || {}, net = s.net || {};
    const issues = [];
    const note = (level, text) => { if (level !== "ok") issues.push({ level, text }); };

    push("cpu", cpu.pct);
    const cl = loadLevel(cpu.pct);
    setTile("cpu", { value: num(cpu.pct), unit: "%", pct: cpu.pct, level: cl, word: LEVEL_WORD[cl],
      sub: [cpu.freq_mhz && `${(cpu.freq_mhz / 1000).toFixed(1)} GHz`, known(cpu.temp_c) ? `${num(cpu.temp_c)} °C` : "temperature n/a"].filter(Boolean).join(" · ") });
    note(cl, `CPU ${num(cpu.pct)}%`);

    push("ram", ram.pct);
    const rl = loadLevel(ram.pct);
    setTile("ram", { value: num(ram.pct), unit: "%", pct: ram.pct, level: rl, word: LEVEL_WORD[rl],
      sub: ram.total_gb ? `${num(ram.used_gb, 1)} of ${num(ram.total_gb, 0)} GB` : "" });
    note(rl, `memory ${num(ram.pct)}%`);

    if (gpu) {
      push("gpu", gpu.pct);
      const hot = known(gpu.temp_c) && gpu.temp_c >= 85;
      const gl = hot ? "crit" : loadLevel(gpu.pct);
      const name = (gpu.name || "").replace(/\((TM|R)\)/g, "").trim();
      setTile("gpu", { value: num(gpu.pct), unit: "%", pct: gpu.pct, level: gl, word: hot ? "Hot" : LEVEL_WORD[gl],
        sub: [name, known(gpu.temp_c) && `${num(gpu.temp_c)} °C`].filter(Boolean).join(" · ") });
      note(gl, hot ? `GPU ${num(gpu.temp_c)} °C` : `GPU ${num(gpu.pct)}%`);
      const vUsed = known(gpu.vram_used_mb) ? gpu.vram_used_mb / 1024 : null;
      push("vram", vUsed);
      const vpct = gpu.vram_total_mb ? (gpu.vram_used_mb / gpu.vram_total_mb) * 100 : null;
      const vl = known(vpct) ? loadLevel(vpct) : "ok";
      setTile("vram", { value: num(vUsed, 1), unit: "GB", pct: vpct, level: vl, word: LEVEL_WORD[vl],
        sub: gpu.vram_total_mb ? `of ${num(gpu.vram_total_mb / 1024, 1)} GB` : "in use (total not reported)" });
    } else {
      setTile("gpu", { value: "—", sub: "no GPU readings" });
      setTile("vram", { value: "—", sub: "no GPU readings" });
    }

    if (bat) {
      push("battery", bat.pct);
      const bl = bat.plugged ? "ok" : bat.pct <= 10 ? "crit" : bat.pct <= 20 ? "warn" : "ok";
      const left = !bat.plugged && bat.secs_left ? ` · ${duration(bat.secs_left)} left` : "";
      setTile("battery", { value: num(bat.pct), unit: "%", pct: bat.pct, level: bl, word: "Low",
        sub: (bat.plugged ? (bat.pct >= 99 ? "Plugged in, full" : "Charging") : "On battery") + left });
      note(bl, `battery ${num(bat.pct)}%`);
    } else {
      setTile("battery", { value: "—", sub: "no battery" });
    }

    push("disk", disk.free_gb);
    const dl = known(disk.free_gb) && disk.free_gb < 5 ? "warn" : "ok";
    setTile("disk", { value: num(disk.free_gb, 0), unit: "GB free", pct: disk.pct, level: dl, word: "Low",
      sub: disk.total_gb ? `${num(disk.pct, 0)}% of ${num(disk.total_gb, 0)} GB used` : "" });
    note(dl, `disk ${num(disk.free_gb, 0)} GB free`);

    // Cores: one column each, filled to its load; green when idle, amber,
    // then red when busy. The % under each keeps it readable without colour.
    k("cpu-meta").textContent = cpu.count ? `${cpu.count} threads` : "";
    const cores = cpu.cores || [];
    k("cores").style.setProperty("--n", Math.min(cores.length || 1, 16));
    k("cores").innerHTML = cores.map((c, i) =>
      `<div class="core" data-tip="Core ${i + 1}: ${c}%" aria-label="Core ${i + 1}: ${c}%">
        <i><b style="height:${Math.max(4, c)}%;--h:${Math.round(145 - Math.min(100, c) * 1.2)};--v:${Math.min(100, c) / 100}"></b></i>
        <span>${c}%</span></div>`).join("");

    // Network
    const online = net.online;
    k("net-badge").innerHTML = !known(online) ? badge("idle", "Checking") : online ? badge("ok", "Online") : badge("crit", "Offline");
    if (online === false) issues.push({ level: "crit", text: "internet offline" });
    k("ping").textContent = known(net.ping_ms) ? `${num(net.ping_ms)} ms` : "—";
    k("down").textContent = rate(net.down_kb_s);
    k("up").textContent = rate(net.up_kb_s);
    k("disk-rate").textContent = known(disk.read_mb_s) ? `${num(disk.read_mb_s, 1)} / ${num(disk.write_mb_s, 1)} MB/s` : "—";
    push("down", net.down_kb_s);
    k("net-spark").innerHTML = sparkSvg(hist.down, null, "down");

    renderProcs(s.procs || {});
    return issues;
  }

  function renderProcs(procs) {
    const rows = procTab === "cpu" ? procs.by_cpu || [] : procs.by_ram || [];
    const max = Math.max(1e-9, ...rows.map((p) => (procTab === "cpu" ? p.cpu : p.ram_mb)));
    k("procs").innerHTML = rows.map((p) => {
      const v = procTab === "cpu" ? p.cpu : p.ram_mb;
      const label = procTab === "cpu" ? `${num(p.cpu, 1)}%` : gb(p.ram_mb);
      return `<li title="${esc(p.name)} (pid ${p.pid})"><span class="pname">${esc(p.name.replace(/\.exe$/i, ""))}</span>
        <b>${label}</b><i style="width:${((v / max) * 100).toFixed(1)}%"></i></li>`;
    }).join("") || `<li class="dlog-empty">—</li>`;
  }

  // ---------- Jarvis ----------

  function agentState(m) {
    if (m.unavailable) return ["crit", "Unavailable", m.unavailable];
    if (m.last_error_ts && m.last_error_ts > (m.last_ok_ts || 0)) return ["warn", "Failing", m.last_error];
    if (m.calls) return ["ok", "Working", `last answer ${ago(m.last_ok_ts)}`];
    return ["idle", "Standby", "not used yet this session"];
  }

  function renderJarvis(j, voice) {
    const issues = [];
    if (voice) {
      const chip = (level, label, value) =>
        `<span class="chip-s" data-level="${level}">${ICON[level]}<span>${label}</span><b>${esc(value ?? "—")}</b></span>`;
      k("voice").innerHTML =
        chip(voice.brain ? "ok" : "crit", "Brain", voice.brain || "none") +
        chip(voice.mic === "live" ? "ok" : "idle", "Mic", voice.mic === "live" ? "Listening" : "Off") +
        chip(voice.stt ? "ok" : "idle", "Speech-to-text", voice.stt) +
        chip(voice.tts ? "ok" : "idle", "Text-to-speech", voice.tts) +
        chip("idle", "State", voice.state) +
        (voice.dropped_frames ? chip("warn", "Dropped audio", voice.dropped_frames) : "");
      if (!voice.brain) issues.push({ level: "crit", text: "no voice brain" });
    }
    if (!j) return issues;
    k("uptime").textContent = `running ${duration(j.uptime_s) || "0 min"}`;
    k("agents").innerHTML = (j.models || []).map((m) => {
      const [level, word, why] = agentState(m);
      if (level === "warn" || level === "crit") issues.push({ level: "warn", text: `${AGENT_LABELS[m.agent] || m.agent} model ${word.toLowerCase()}` });
      const fallback = m.chain.length > 1 ? `<span class="agent-chain">then ${esc(m.chain.slice(1).join(", "))}</span>` : "";
      return `<article class="agent" data-level="${level}">
        <div class="agent-head"><b>${esc(AGENT_LABELS[m.agent] || m.agent)}</b>${badge(level, word)}</div>
        <div class="agent-model" title="${esc(m.chain.join(" → "))}">${esc(m.active)}${fallback}</div>
        <dl>
          <div><dt>Speed</dt><dd>${m.avg_tok_per_s != null ? `${m.avg_tok_per_s} tok/s` : "—"}</dd></div>
          <div><dt>First token</dt><dd>${m.avg_first_token_ms != null ? `${(m.avg_first_token_ms / 1000).toFixed(1)} s` : "—"}</dd></div>
          <div><dt>Calls</dt><dd>${m.calls}${m.errors ? ` · ${m.errors} failed` : ""}</dd></div>
        </dl>
        <p class="agent-why" title="${esc(why || "")}">${esc(why || "")}</p>
      </article>`;
    }).join("");

    const ol = j.ollama || {};
    k("ollama").textContent = !ol.reachable ? "Not running"
      : ol.models?.length ? ol.models.map((m) => `${m.name} (${gb(m.size_mb)})`).join(", ") : "Running, none loaded";
    const web = j.agents?.web ?? "—";
    k("web").textContent = web.charAt(0).toUpperCase() + web.slice(1);
    const v = j.voice || {};
    k("latency").textContent = v.avg_first_audio_ms != null ? `${(v.avg_first_audio_ms / 1000).toFixed(1)} s to first word` : "No spoken replies yet";
    const st = j.storage || {};
    k("storage").textContent = `${st.memory_facts ?? 0} facts${st.memory_mtime ? ` · saved ${ago(st.memory_mtime)}` : ""}`;
    const logs = j.logs || {};
    k("errs").textContent = `${logs.errors ?? 0} / ${logs.warnings ?? 0}`;
    k("disk-jarvis").textContent = known(st.debug_mb) ? gb(((st.debug_mb || 0) + (st.browser_profile_mb || 0)) * 1) : "—";
    if (logs.errors) issues.push({ level: "warn", text: `${logs.errors} error${logs.errors > 1 ? "s" : ""} logged` });
    return issues;
  }

  function renderLog(s) {
    const alerts = [...(s.alerts || [])];
    for (const a of localAlerts) if (!alerts.some((x) => x.ts === a.ts)) alerts.push(a);
    const rows = [
      ...alerts.map((a) => ({ ts: a.ts, level: a.level === "critical" ? "crit" : a.level === "info" ? "ok" : "warn", who: "Alert", msg: a.text || a.facts })),
      ...(s.jarvis?.logs?.recent || []).map((r) => ({
        ts: r.ts, level: r.level === "error" || r.level === "critical" ? "crit" : "warn",
        who: r.logger.replace(/^jarvis\./, ""), msg: r.msg })),
    ].sort((a, b) => b.ts - a.ts).slice(0, 40);
    k("log").innerHTML = rows.length
      ? rows.map((r) => `<li data-level="${r.level}">${ICON[r.level]}<time>${hhmm(r.ts)}</time><span class="who">${esc(r.who)}</span><span class="msg">${esc(r.msg)}</span></li>`).join("")
      : `<li class="dlog-empty">Nothing to report.</li>`;
  }

  function renderSummary(issues) {
    const worst = issues.some((i) => i.level === "crit") ? "crit" : issues.length ? "warn" : "ok";
    k("summary").dataset.level = worst;
    k("sum-icon").innerHTML = ICON[worst];
    k("sum-title").textContent = worst === "ok" ? "All systems normal"
      : `${issues.length} thing${issues.length > 1 ? "s" : ""} need${issues.length > 1 ? "" : "s"} attention`;
    k("sum-detail").textContent = worst === "ok" ? "" : issues.map((i) => i.text).join(" · ");
  }

  // ---------- interaction: tabs and hover tooltips ----------

  for (const b of el.querySelectorAll("[data-proc]")) {
    b.onclick = () => {
      procTab = b.dataset.proc;
      for (const x of el.querySelectorAll("[data-proc]")) x.setAttribute("aria-selected", String(x === b));
      if (lastSnap) renderProcs(lastSnap.procs || {});
    };
  }

  const tip = k("tip");
  const showTip = (text, x, y) => {
    tip.textContent = text;
    tip.hidden = false;
    const box = el.getBoundingClientRect();
    tip.style.left = `${Math.min(x - box.left + el.scrollLeft + 12, el.scrollWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${y - box.top + el.scrollTop - tip.offsetHeight - 10}px`;
  };
  el.addEventListener("pointermove", (e) => {
    const cell = e.target.closest("[data-tip]");
    if (cell) return showTip(cell.dataset.tip, e.clientX, e.clientY);
    const wrap = e.target.closest(".tile-trend, [data-k='net-spark']");
    const svg = wrap?.querySelector("[data-spark]");
    const series = svg && hist[svg.dataset.spark];
    if (series?.length > 1) {
      const r = svg.getBoundingClientRect();
      const slot = Math.round(((e.clientX - r.left) / r.width) * (HISTORY - 1));
      const i = slot - (HISTORY - series.length);
      if (i >= 0 && i < series.length) {
        const secs = (series.length - 1 - i) * 2;
        const v = series[i];
        const key = svg.dataset.spark;
        const shown = key === "down" ? rate(v) : `${num(v, key === "vram" ? 1 : 0)}${histUnit[key]}`;
        return showTip(`${shown} · ${secs ? `${secs} s ago` : "now"}`, e.clientX, e.clientY);
      }
    }
    tip.hidden = true;
  });
  el.addEventListener("pointerleave", () => { tip.hidden = true; });

  return {
    update(snap, error) {
      if (!snap) {
        stamp.textContent = error || "no data";
        return;
      }
      lastSnap = snap;
      stamp.textContent = `updated ${new Date((snap.ts || Date.now() / 1000) * 1000).toLocaleTimeString([], { hour12: false })}`;
      const issues = snap.cpu ? renderSystem(snap) : [];
      if (!snap.cpu) stamp.textContent += " · laptop sensors unavailable (pip install psutil)";
      issues.push(...renderJarvis(snap.jarvis, snap.voice));
      renderLog(snap);
      renderSummary(issues);
    },
    alert(msg) {
      localAlerts.push({ ...(msg.data || {}), text: msg.text, ts: msg.data?.ts || msg.ts });
      if (localAlerts.length > 20) localAlerts.shift();
    },
    setVisible(on) {
      if (on === visible) return;
      visible = on;
      send({ t: "diag_sub", on });
    },
    /** After a reconnect the new session has no subscription yet. */
    resubscribe() { if (visible) send({ t: "diag_sub", on: true }); },
  };
}
