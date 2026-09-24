import { api, getToken } from "./api.js";

// The Setup page: which model every agent uses, the API keys, Ollama's model
// library and everything else in .env, on one full-screen page. The server
// side is backend/setup_api.py. Everything saved here lands in .env; models
// and keys apply at once, and each setting says when it takes effect.
//
// Edits are drafts until Save, and survive closing the page. While the page
// is open it keeps its keystrokes to itself, so the app's one-letter
// shortcuts (M, T, V...) don't fire behind it; Esc closes it.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const icon = (id, cls = "") => `<svg class="ic ${cls}" aria-hidden="true"><use href="#${id}"/></svg>`;

const TABS = [
  { id: "agents", label: "Agents & models", icon: "t-bot" },
  { id: "ollama", label: "Ollama", icon: "i-box" },
  { id: "keys", label: "API keys", icon: "i-key" },
  { id: "voice", label: "Voice", icon: "i-mic" },
  { id: "tools", label: "Tools", icon: "t-tool" },
  { id: "web", label: "Web agent", icon: "t-globe" },
  { id: "diagnostics", label: "Diagnostics", icon: "i-gauge" },
  { id: "camera", label: "Camera", icon: "t-camera" },
  { id: "advanced", label: "Advanced", icon: "i-sliders" },
];
// Tabs holding a single section take their intro from its note (server side).
const INTRO = {
  advanced: "Every other setting, with the notes from config.py. Most are read once, when Jarvis starts.",
};
const NEEDS = { tools: "Calls tools", vision: "Sees pictures" };
const APPLY = { reconnect: "Next connection", restart: "Needs restart" };
const WHO = { gemini_live: "Gemini Live", speech: "Speech" };
const PLACEHOLDER = {
  groq: "e.g. qwen/qwen3.8-27b", gemini: "e.g. gemini-flash-latest", openai: "e.g. gpt-5-mini",
  anthropic: "e.g. claude-haiku-4-5", openrouter: "e.g. google/gemini-2.5-flash",
  openai_compat: "the server's model name",
};

const size = (mb) => (!mb ? "" : mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`);
const bytes = (n) => (n >= 2 ** 30 ? `${(n / 2 ** 30).toFixed(1)} GB` : n >= 2 ** 20 ? `${Math.round(n / 2 ** 20)} MB` : `${Math.round(n / 1024)} KB`);
const secs = (s) => (s < 60 ? `${Math.max(1, Math.round(s))} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
const same = (a, b) => String(a ?? "") === String(b ?? "");
// "qwen2.5" and "qwen2.5:latest" are the same model to Ollama.
const norm = (n) => { n = n.trim(); return n.split("/").pop().includes(":") ? n : `${n}:latest`; };

async function call(path, method = "GET", body) {
  const opts = { method };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  return (await api(path, opts)).json();
}

/** The server's reason, from api()'s "<status> <body>" error. */
function errText(e) {
  const m = /^(\d{3}) ([\s\S]*)$/.exec(e?.message || "");
  if (!m) return e?.message || String(e);
  try {
    const d = JSON.parse(m[2]).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((x) => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("; ");
  } catch {}
  if (m[1] === "401") return "Jarvis isn't connected yet. Try again in a moment.";
  return m[2] || `HTTP ${m[1]}`;
}

export function initSetup({ beforeOpen } = {}) {
  const page = $("setup");
  if (!page) return null;
  let st = null;        // /api/setup/state
  let ol = null;        // /api/setup/ollama
  let tab = "agents";
  let loading = null;
  let olTimer = 0, toastTimer = 0;
  let filter = "";      // the Advanced tab's search
  let confirmDel = "";  // Ollama model waiting for "really delete?"
  const drafts = {};    // agent -> unsaved profile
  const fdrafts = {};   // section -> {setting: unsaved value}
  const tests = {};     // "agent:index" -> result html
  const lists = {};     // provider (or "openai_compat|url") -> {state, models, error}
  const openMore = {};  // agent -> "More options" left open
  const busy = new Set();

  page.tabIndex = -1;
  page.innerHTML = `
    <header class="su-bar">
      <button class="tool" type="button" data-close title="Back to Jarvis (Esc)" aria-label="Back to Jarvis">${icon("i-back")}</button>
      <div class="su-title"><h1>Setup</h1><p>Models, API keys, Ollama and everything else in <code>.env</code></p></div>
      <span class="grow"></span>
      <span class="su-where" id="su-where"></span>
    </header>
    <div class="su-wrap">
      <nav class="su-nav" role="tablist" aria-label="Setup sections" aria-orientation="vertical">
        ${TABS.map((t) => `<button type="button" role="tab" data-tab="${t.id}" aria-selected="false">${icon(t.icon)}<span>${esc(t.label)}</span><i class="su-badge" hidden></i></button>`).join("")}
      </nav>
      <div class="su-main" id="su-main"></div>
    </div>
    <div id="su-lists"></div>
    <div class="su-toast" id="su-toast" role="status" aria-live="polite" hidden></div>`;
  const main = $("su-main");

  // ---------- open / close ----------

  function open(which) {
    if (TABS.some((t) => t.id === which)) tab = which;
    beforeOpen?.();
    page.hidden = false;
    document.querySelector(".app")?.setAttribute("inert", "");
    history.replaceState(null, "", `#setup/${tab}`);
    render();
    page.focus({ preventScroll: true });  // the page itself: no focus ring on open
    load();
  }

  function close() {
    page.hidden = true;
    document.querySelector(".app")?.removeAttribute("inert");
    clearTimeout(olTimer);
    if (location.hash.startsWith("#setup")) history.replaceState(null, "", location.pathname + location.search);
    $("setup-btn")?.focus();
  }

  const isOpen = () => !page.hidden;

  function setTab(id) {
    tab = id;
    history.replaceState(null, "", `#setup/${tab}`);
    render();
    main.scrollTop = 0;
    if (tab === "ollama") refreshOllama();
    else schedule();
  }

  async function waitForToken() {
    for (let i = 0; i < 60 && !getToken(); i++) await new Promise((r) => setTimeout(r, 250));
  }

  function load() {
    if (loading) return loading;
    loading = (async () => {
      if (!getToken()) {
        main.innerHTML = `<div class="su-empty"><span class="spin"></span>Waiting for Jarvis to connect…</div>`;
        await waitForToken();
      }
      try {
        const [s, o] = await Promise.all([call("/api/setup/state"), call("/api/setup/ollama").catch(() => null)]);
        st = s;
        ol = o;
        for (const a of st.agents) if (drafts[a.id] && !dirty(a)) delete drafts[a.id];
        $("su-where").textContent = `Saved to ${st.env_path}`;
        render();
        schedule();
      } catch (e) {
        main.innerHTML = `<div class="su-empty">${icon("t-alert")}<p>Couldn't load the settings: ${esc(errText(e))}</p>
          <button type="button" class="btn" data-act="reload">Try again</button></div>`;
      } finally {
        loading = null;
      }
    })();
    return loading;
  }

  // ---------- render ----------

  function render() {
    for (const b of page.querySelectorAll("[data-tab]")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
    paintBadges();
    if (!st) {
      main.innerHTML = `<div class="su-empty"><span class="spin"></span>Loading…</div>`;
      return;
    }
    const view = { agents: agentsView, ollama: ollamaView, keys: keysView }[tab] ?? (() => sectionsView(tab));
    main.innerHTML = `<div class="su-page">${restartBanner()}${view()}</div>`;
    if (tab === "ollama") paintOllama();
    if (tab === "agents") for (const a of st.agents) primeLists(a);
    if (tab === "advanced" && filter) applyFilter();
  }

  function restartBanner() {
    const p = st.restart_pending || [];
    if (!p.length) return "";
    return `<div class="su-banner">${icon("t-alert")}<div><b>Restart Jarvis to finish applying ${p.length === 1 ? "a change" : `${p.length} changes`}</b>
      <span>${p.map((n) => `<code>${esc(n.toUpperCase())}</code>`).join(" ")}: close the run.bat window and start it again.</span></div></div>`;
  }

  function paintBadges() {
    const badge = page.querySelector('[data-tab="ollama"] .su-badge');
    const running = ol?.pulls?.filter((p) => p.state === "running") ?? [];
    if (running.length) {
      const total = running.reduce((n, p) => n + p.total, 0), done = running.reduce((n, p) => n + p.completed, 0);
      badge.textContent = total ? `${Math.floor((done / total) * 100)}%` : "…";
      badge.dataset.level = "info";
      badge.title = "Downloading";
      badge.hidden = false;
    } else if (ol?.missing?.length) {
      badge.textContent = String(ol.missing.length);
      badge.dataset.level = "warn";
      badge.title = "Models an agent uses that aren't downloaded";
      badge.hidden = false;
    } else badge.hidden = true;
    for (const t of TABS) page.querySelector(`[data-tab="${t.id}"]`).classList.toggle("su-unsaved", hasDrafts(t.id));
  }

  function hasDrafts(tabId) {
    if (!st) return false;
    const secs = st.sections.filter((s) => s.tab === tabId && fdrafts[s.id] && Object.keys(fdrafts[s.id]).length);
    return secs.length > 0 || (tabId === "agents" && st.agents.some(dirty));
  }

  const intro = (title, text) => `<div class="su-intro"><h2>${esc(title)}</h2>${text ? `<p>${text}</p>` : ""}</div>`;

  // ---------- agents ----------

  const agentById = (id) => st.agents.find((a) => a.id === id);
  const providerById = (id) => st.providers.find((p) => p.id === id);
  const labelOf = (who) => {
    const [id, role] = who.split(":");
    const base = WHO[id] ?? agentById(id)?.label ?? id;
    return role ? `${base} (fallback)` : base;
  };

  const fromProfile = (p) => ({
    provider: p.provider, model: p.model, base_url: p.base_url || "", model_key: null,
    temperature: p.temperature == null ? "" : String(p.temperature),
    max_tokens: p.max_tokens == null ? "" : String(p.max_tokens),
    timeout_s: p.timeout_s == null ? "" : String(p.timeout_s),
    extra: Object.keys(p.extra || {}).length ? JSON.stringify(p.extra) : "",
    fallback: p.fallback.map((f) => ({ provider: f.provider, model: f.model, base_url: f.base_url || "" })),
  });
  const draftOf = (id) => (drafts[id] ??= fromProfile(agentById(id).profile));
  const dirty = (a) => !!drafts[a.id] && JSON.stringify(drafts[a.id]) !== JSON.stringify(fromProfile(a.profile));

  function toBody(d) {
    const num = (v) => (v === "" || v == null ? null : Number(v));
    let extra = {};
    if (d.extra.trim()) {
      try { extra = JSON.parse(d.extra); } catch { throw new Error("Extra request fields must be JSON, like {\"think\": true}"); }
      if (!extra || typeof extra !== "object" || Array.isArray(extra)) throw new Error("Extra request fields must be a JSON object");
    }
    return {
      provider: d.provider, model: d.model.trim(), base_url: d.base_url.trim(), model_key: d.model_key,
      temperature: num(d.temperature), max_tokens: num(d.max_tokens), timeout_s: num(d.timeout_s), extra,
      fallback: d.fallback.filter((f) => f.model.trim())
        .map((f) => ({ provider: f.provider, model: f.model.trim(), base_url: f.base_url.trim() })),
    };
  }

  function agentsView() {
    return intro("Agents & models", "Each agent has its own model, plus fallbacks tried in order when it is down or out of quota. " +
      "Saving applies at once, with no restart. <b>Test</b> sends one short request shaped like the agent's real work.") +
      st.agents.map((a) => `<article class="su-card su-agent" data-agent="${a.id}">${agentInner(a)}</article>`).join("") +
      st.sections.filter((s) => s.tab === "agents").map((s) => sectionHTML(s)).join("");
  }

  function agentInner(a) {
    const d = draftOf(a.id);
    const pill = agentPill(a, d);
    const isDirty = dirty(a);
    return `
      <header class="su-card-head">
        <div class="su-head-text">
          <h3>${esc(a.label)} <code class="su-env">${esc(a.env)}__*</code></h3>
          <p>${esc(a.purpose)}</p>
          ${a.needs.length ? `<div class="su-chips">${a.needs.map((n) => `<span class="su-chip">${NEEDS[n] ?? n}</span>`).join("")}</div>` : ""}
        </div>
        <span class="su-pill" data-level="${pill.level}" title="${esc(pill.title)}">${esc(pill.text)}</span>
      </header>
      <p class="su-label">Model</p>
      ${entryHTML(a, d, 0)}
      ${moreHTML(a, d)}
      <p class="su-label">Fallbacks <small>tried in order when the one above fails</small></p>
      ${d.fallback.length ? `<ol class="su-fb">${d.fallback.map((f, i) => `<li>${entryHTML(a, f, i + 1)}</li>`).join("")}</ol>`
        : `<p class="su-muted su-none">None. If this model is down, the agent fails.</p>`}
      <button type="button" class="link su-add" data-act="add-fb">+ Add a fallback</button>
      <footer class="su-card-foot">
        <span class="su-stats" title="${esc(a.status?.last_error || "")}">${esc(statsText(a.status))}</span>
        <span class="grow"></span>
        <button type="button" class="btn" data-act="discard" ${isDirty ? "" : "hidden"}>Discard</button>
        <button type="button" class="btn primary" data-act="save" ${isDirty ? "" : "disabled"}>Save</button>
      </footer>`;
  }

  function agentPill(a, d) {
    const s = a.status || {};
    if (dirty(a)) return { level: "info", text: "Unsaved", title: "Press Save to use these changes" };
    if (s.unavailable) return { level: "bad", text: "Can't run", title: s.unavailable };
    if (d.provider === "ollama" && ol && !ol.reachable) return { level: "bad", text: "Ollama is off", title: `Nothing answers at ${ol.host}` };
    if (d.provider === "ollama" && ol?.reachable && d.model && !installed(d.model)) return { level: "warn", text: "Not downloaded", title: "" };
    if (s.errors && s.last_error_ts > (s.last_ok_ts || 0)) return { level: "warn", text: "Last call failed", title: s.last_error };
    return { level: "ok", text: "Ready", title: s.active ? `Active: ${s.active}` : "" };
  }

  function statsText(s) {
    if (!s?.calls) return "Not used since Jarvis started";
    const bits = [`${s.calls} call${s.calls === 1 ? "" : "s"}`];
    if (s.errors) bits.push(`${s.errors} failed`);
    if (s.avg_tok_per_s) bits.push(`${s.avg_tok_per_s} tok/s`);
    if (s.avg_first_token_ms != null) bits.push(`first token ${s.avg_first_token_ms} ms`);
    if (s.model) bits.push(`last: ${s.model}`);
    return bits.join(" · ");
  }

  function providerOptions(sel) {
    return st.providers.map((p) => `<option value="${p.id}" ${p.id === sel ? "selected" : ""}>${esc(p.label)}${p.key && !p.key_set ? " (no key)" : ""}</option>`).join("");
  }

  function entryHTML(a, e, i) {
    const n = a.id;
    const wantsUrl = e.provider === "openai_compat";
    const ph = e.provider === "ollama"
      ? (ol?.models?.length ? "pick an installed model" : "download one on the Ollama tab")
      : PLACEHOLDER[e.provider] ?? "model name";
    return `<div class="su-entry" data-i="${i}">
        ${i ? `<span class="su-num" aria-hidden="true">${i}</span>` : ""}
        <select data-f="provider" aria-label="Provider">${providerOptions(e.provider)}</select>
        <input data-f="model" value="${esc(e.model)}" list="${listId(e.provider, e.base_url)}" placeholder="${esc(ph)}"
          aria-label="Model" autocomplete="off" spellcheck="false">
        ${wantsUrl ? `<input data-f="base_url" class="su-url" value="${esc(e.base_url)}" placeholder="http://127.0.0.1:1234/v1" aria-label="Base URL" spellcheck="false">` : ""}
        <button type="button" class="btn sm" data-act="test" ${busy.has(`test:${n}:${i}`) ? "disabled" : ""}>${icon("i-bolt")}Test</button>
        ${i ? `<span class="su-move">
          <button type="button" class="mini-i" data-act="up" title="Try it earlier" aria-label="Move up" ${i === 1 ? "disabled" : ""}>${icon("t-chev", "up")}</button>
          <button type="button" class="mini-i" data-act="down" title="Try it later" aria-label="Move down" ${i === draftOf(n).fallback.length ? "disabled" : ""}>${icon("t-chev", "down")}</button>
          <button type="button" class="mini-i" data-act="remove" title="Remove" aria-label="Remove fallback">${icon("t-x")}</button>
        </span>` : ""}
      </div>
      <div class="su-under" data-under="${i}">${underHTML(a, e, i)}</div>`;
  }

  function underHTML(a, e, i) {
    return (i === 0 ? listNote(e) : "") + warnings(a, e, i) + (tests[`${a.id}:${i}`] || "");
  }

  function moreHTML(a, d) {
    const inh = (k) => {
      const b = a.builtin;
      return d.provider === b.provider && b[k] != null ? b[k] : st.globals[k];
    };
    const prov = providerById(d.provider);
    const defUrl = d.provider === "ollama" ? (ol?.host || "the OLLAMA_HOST setting") : prov?.default_base_url || "the provider's usual endpoint";
    return `<details class="su-more" ${openMore[a.id] ? "open" : ""}><summary>More options</summary>
      <div class="su-grid">
        ${d.provider !== "openai_compat" ? `<label class="su-f"><span>Base URL <small>Only to use a different server</small></span>
          <input data-f="base_url" value="${esc(d.base_url)}" placeholder="${esc(defUrl)}" spellcheck="false"></label>` : ""}
        ${prov?.key ? keyField(a, d, prov) : ""}
        <label class="su-f"><span>Temperature <small>0 to 2</small></span>
          <input data-f="temperature" type="number" min="0" max="2" step="0.05" value="${esc(d.temperature)}" placeholder="default ${inh("temperature")}"></label>
        <label class="su-f"><span>Max reply tokens</span>
          <input data-f="max_tokens" type="number" min="1" step="1" value="${esc(d.max_tokens)}" placeholder="default ${inh("max_tokens")}"></label>
        <label class="su-f"><span>Timeout (seconds)</span>
          <input data-f="timeout_s" type="number" min="1" step="1" value="${esc(d.timeout_s)}" placeholder="default ${inh("timeout_s")}"></label>
        <label class="su-f su-wide"><span>Extra request fields <small>JSON sent with every request, e.g. {"think": true} for an Ollama thinking model</small></span>
          <textarea data-f="extra" rows="2" class="mono" spellcheck="false" placeholder="{}">${esc(d.extra)}</textarea></label>
      </div>
    </details>`;
  }

  function keyField(a, d, prov) {
    const env = prov.key.toUpperCase();
    const saved = a.profile.model_key;
    let line;
    if (d.model_key === "") line = `Will use ${env} after you save`;
    else if (d.model_key) line = "A new key, stored when you press Save";
    else if (saved.set && d.provider === a.profile.provider)
      line = `Own key <b>${esc(saved.hint)}</b> <button type="button" class="link" data-act="clear-key">Use ${env} instead</button>`;
    else line = `Uses ${env}`;
    return `<div class="su-f su-wide"><span>API key for this agent only <small>${line}</small></span>
      <input data-f="model_key" type="password" autocomplete="off" spellcheck="false" placeholder="Paste a key to use instead of ${env}"></div>`;
  }

  // Datalists of model names, one per provider, filled from the provider itself.
  const listKey = (p, base) => (p === "openai_compat" ? `${p}|${(base || "").trim()}` : p);
  const listId = (p, base) => `su-dl-${listKey(p, base).replace(/[^a-z0-9]/gi, "_")}`;

  function primeLists(a) {
    const d = draftOf(a.id);
    for (const e of [d, ...d.fallback]) ensureList(e.provider, e.base_url, a.id);
  }

  async function ensureList(provider, base = "", agent = "", force = false) {
    if (provider === "openai_compat" && !base.trim()) return;
    const k = listKey(provider, base);
    if (lists[k] && !force && lists[k].state !== "error") return;
    if (lists[k]?.state === "loading") return;
    lists[k] = { state: "loading", models: [] };
    const q = new URLSearchParams({ provider, agent });
    if (provider === "openai_compat") q.set("base_url", base.trim());
    if (force) q.set("refresh", "1");
    try {
      const r = await call(`/api/setup/models?${q}`);
      lists[k] = r.error ? { state: "error", models: [], error: r.error } : { state: "ok", models: r.models };
    } catch (e) {
      lists[k] = { state: "error", models: [], error: errText(e) };
    }
    const id = listId(provider, base);
    let dl = document.getElementById(id);
    if (!dl) {
      dl = document.createElement("datalist");
      dl.id = id;
      $("su-lists").append(dl);
    }
    dl.replaceChildren(...lists[k].models.map((m) => {
      const caps = [m.tools && "tools", m.vision && "vision"].filter(Boolean).join(", ");
      return new Option([m.note, caps].filter(Boolean).join(" · "), m.id);
    }));
    if (tab === "agents") for (const a of st.agents) paintMeta(a.id);
  }

  function listNote(e) {
    const l = lists[listKey(e.provider, e.base_url)];
    const name = providerById(e.provider)?.label ?? e.provider;
    if (!l) return "";
    if (l.state === "loading") return `<p class="su-hint"><span class="spin"></span>Asking ${esc(name)} which models it has…</p>`;
    if (l.state === "error") return `<p class="su-hint">Couldn't list ${esc(name)}'s models: ${esc(l.error)} <button type="button" class="link" data-act="relist">Try again</button></p>`;
    if (!l.models.length) return "";
    return `<p class="su-hint">${l.models.length} model${l.models.length === 1 ? "" : "s"} available. Click the box or type to search. <button type="button" class="link" data-act="relist">Refresh</button></p>`;
  }

  function capsOf(e) {
    const m = lists[listKey(e.provider, e.base_url)]?.models?.find((x) => x.id === e.model.trim());
    if (m) return m;
    if (e.provider === "ollama") {
      const om = ol?.models?.find((x) => norm(x.name) === norm(e.model));
      if (om?.capabilities) return { tools: om.capabilities.includes("tools"), vision: om.capabilities.includes("vision") };
    }
    return null;
  }

  const installed = (name) => !!ol?.models?.some((m) => norm(m.name) === norm(name));

  function ownKey(a, d) {
    if (d.model_key === "") return false;
    return !!d.model_key || (a.profile.model_key.set && d.provider === a.profile.provider);
  }

  function warnings(a, e, i) {
    const w = [];
    const prov = providerById(e.provider);
    const model = e.model.trim();
    if (!model) w.push(["warn", i ? "Pick a model, or remove this row (empty rows are dropped when you save)." : "Pick a model."]);
    if (prov?.key && !prov.key_set && !(i === 0 && ownKey(a, draftOf(a.id))))
      w.push(["bad", `No ${esc(prov.key.toUpperCase())} yet. <button type="button" class="link" data-goto="keys">Add it under API keys</button>`]);
    if (e.provider === "openai_compat" && !e.base_url.trim()) w.push(["bad", "Needs the server's base URL, e.g. http://127.0.0.1:1234/v1 for LM Studio."]);
    if (e.provider === "ollama" && ol) {
      if (!ol.reachable) w.push(["bad", `Ollama isn't running at ${esc(ol.host)}. Start the Ollama app.`]);
      else if (model && !installed(model)) {
        const pulling = ol.pulls?.find((p) => norm(p.name) === norm(model) && p.state === "running");
        w.push(["warn", pulling ? `Downloading… ${pulling.total ? Math.floor((pulling.completed / pulling.total) * 100) : 0}%`
          : `Not downloaded yet. <button type="button" class="link" data-act="pull" data-name="${esc(model)}">Download ${esc(model)}</button>`]);
      }
    }
    const caps = model ? capsOf(e) : null;
    if (a.needs.includes("tools") && caps?.tools === false) w.push(["warn", `${esc(model)} can't call tools, and this agent needs them.`]);
    if (a.needs.includes("vision") && caps?.vision === false) w.push(["warn", `${esc(model)} can't see pictures, and this agent needs to.`]);
    return w.map(([lvl, html]) => `<p class="su-note" data-level="${lvl}">${icon(lvl === "bad" ? "t-x" : "t-alert", "xs")}<span>${html}</span></p>`).join("");
  }

  // Light updates: pill, buttons and the notes under each row -- never the
  // inputs, so typing is never interrupted.
  function paintMeta(id) {
    const card = main.querySelector(`[data-agent="${id}"]`);
    if (!card) return;
    const a = agentById(id), d = draftOf(id);
    const pill = agentPill(a, d), el = card.querySelector(".su-pill");
    el.dataset.level = pill.level;
    el.textContent = pill.text;
    el.title = pill.title;
    const isDirty = dirty(a);
    card.querySelector('[data-act="save"]').disabled = !isDirty;
    card.querySelector('[data-act="discard"]').hidden = !isDirty;
    for (const u of card.querySelectorAll("[data-under]")) {
      const i = Number(u.dataset.under);
      const e = i ? d.fallback[i - 1] : d;
      if (e) u.innerHTML = underHTML(a, e, i);
    }
    paintBadges();
  }

  function paintAgent(id, focusSel) {
    const card = main.querySelector(`[data-agent="${id}"]`);
    if (!card) return;
    card.innerHTML = agentInner(agentById(id));
    if (focusSel) card.querySelector(focusSel)?.focus();
    paintBadges();
  }

  async function runTest(a, i) {
    const d = draftOf(a.id);
    const e = i ? d.fallback[i - 1] : d;
    if (!e?.model.trim()) return;
    let extra = {};
    if (!i) { try { extra = toBody(d).extra; } catch {} }
    const key = `${a.id}:${i}`;
    busy.add(`test:${key}`);
    tests[key] = `<p class="su-note" data-level="info"><span class="spin"></span><span>Testing ${esc(e.provider)}:${esc(e.model.trim())}…${
      e.provider === "ollama" ? " Loading it into memory first can take a while on this PC." : ""}</span></p>`;
    paintAgent(a.id);
    let r;
    try {
      r = await call("/api/setup/test", "POST", {
        agent: a.id, index: i, provider: e.provider, model: e.model.trim(), base_url: e.base_url.trim(),
        model_key: i ? null : d.model_key, extra,
      });
    } catch (err) {
      r = { ok: false, error: errText(err) };
    }
    busy.delete(`test:${key}`);
    tests[key] = testHTML(r);
    paintAgent(a.id);
  }

  function testHTML(r) {
    if (!r.ok) return `<p class="su-note" data-level="bad">${icon("t-x", "xs")}<span>${esc(r.error)}</span></p>`;
    const bits = [`${(r.ms / 1000).toFixed(1)} s`];
    if (r.first_token_ms != null && r.kind === "text") bits.push(`first token ${r.first_token_ms} ms`);
    if (r.tok_per_s) bits.push(`${r.tok_per_s} tok/s`);
    if (r.load_ms > 1500) bits.push(`after ${secs(r.load_ms / 1000)} loading`);
    const what = r.kind === "tools" ? (r.tool_called ? "Called the tool" : "Replied")
      : r.kind === "vision" ? "Looked at a red square" : "Replied";
    const said = r.text ? `: “${esc(r.text.slice(0, 140))}”` : "";
    return `<p class="su-note" data-level="${r.warning ? "warn" : "ok"}">${icon(r.warning ? "t-alert" : "t-check", "xs")}<span>${what} in ${bits.join(" · ")}${said}${
      r.warning ? `<br>${esc(r.warning)}` : ""}</span></p>`;
  }

  async function saveAgent(a, body = null, { quiet = false } = {}) {
    try {
      body ??= toBody(draftOf(a.id));
    } catch (e) {
      toast(e.message, "bad");
      return false;
    }
    if (!body.model) { toast("Pick a model first.", "bad"); return false; }
    const btn = main.querySelector(`[data-agent="${a.id}"] [data-act="save"]`);
    if (btn) btn.disabled = true;
    try {
      const r = await call(`/api/setup/agents/${a.id}`, "PUT", body);
      st.agents[st.agents.findIndex((x) => x.id === a.id)] = r.agent;
      st.keys = r.keys;
      delete drafts[a.id];
      for (const k of Object.keys(tests)) if (k.startsWith(`${a.id}:`)) delete tests[k];
      if (tab === "agents") paintAgent(a.id);
      if (tab === "ollama") paintOllama();
      if (!quiet) {
        const fb = body.fallback.length ? `, with ${body.fallback.length} fallback${body.fallback.length === 1 ? "" : "s"}` : "";
        toast(`${a.label} now uses ${body.provider}:${body.model}${fb}.${shadowNote(r.result)}`, r.result.shadowed.length ? "warn" : "ok");
      }
      return r;
    } catch (e) {
      toast(`Not saved: ${errText(e)}`, "bad");
      if (btn) btn.disabled = false;
      return false;
    }
  }

  const shadowNote = (res) => (res?.shadowed?.length
    ? ` But a Windows environment variable (${res.shadowed.join(", ")}) overrides .env, so the old value still wins.` : "");

  // One click on the Ollama tab: this model becomes the agent's main model.
  // Fallbacks stay as they are.
  async function assign(agentId, model) {
    const a = agentById(agentId);
    const before = toBody(fromProfile(a.profile));
    const body = { ...before, provider: "ollama", model, base_url: a.profile.provider === "ollama" ? before.base_url : "", model_key: null };
    const r = await saveAgent(a, body, { quiet: true });
    if (!r) return;
    const caps = ol?.models?.find((m) => m.name === model)?.capabilities;
    const lacks = caps && a.needs.find((n) => !caps.includes(n));
    toast(`${a.label} now uses ollama:${model}.${lacks ? ` Note: it can't ${lacks === "tools" ? "call tools" : "see pictures"}, which ${a.label} needs.` : ""}`,
      lacks ? "warn" : "ok", { label: "Undo", run: () => saveAgent(agentById(agentId), before) });
  }

  // ---------- Ollama ----------

  function ollamaView() {
    return intro("Ollama", "Local models that run on this PC, with no key and no quota. Download them here, then pick one for any agent.") +
      `<div class="su-ol-status"></div><div class="su-ol-missing"></div>
      <section class="su-card"><header class="su-card-head"><div class="su-head-text"><h3>Installed</h3></div>
        <button type="button" class="btn sm" data-act="ol-refresh">${icon("i-refresh")}Refresh</button></header>
        <div class="su-ol-models"></div></section>
      <section class="su-card"><header class="su-card-head"><div class="su-head-text"><h3>Download a model</h3>
        <p>Names come from <a href="https://ollama.com/library" target="_blank" rel="noopener">ollama.com/library</a>, e.g. <code>qwen2.5:1.5b</code>.
        Ollama runs on the CPU here (about 3.5 tokens/s for a 5B model), so smaller models answer faster.</p></div></header>
        <form class="su-pull" id="su-pull" autocomplete="off">
          <input name="name" list="su-dl-suggest" placeholder="model name, e.g. qwen2.5:1.5b" aria-label="Model to download" spellcheck="false" required>
          <button type="submit" class="btn primary">${icon("t-down")}Download</button>
        </form>
        <datalist id="su-dl-suggest">${(ol?.suggested ?? []).map((s) => `<option value="${esc(s.name)}">${esc(s.size)} · ${esc(s.why)}</option>`).join("")}</datalist>
        <div class="su-ol-pulls"></div>
        <p class="su-label">Good fits for this PC</p>
        <div class="su-ol-suggest"></div>
      </section>` +
      st.sections.filter((s) => s.tab === "ollama").map((s) => sectionHTML(s)).join("");
  }

  function paintOllama() {
    const q = (sel) => main.querySelector(sel);
    if (!q(".su-ol-status")) return;
    if (!ol) {
      q(".su-ol-status").innerHTML = `<p class="su-note" data-level="info"><span class="spin"></span><span>Checking Ollama…</span></p>`;
      return;
    }
    const total = ol.models.reduce((n, m) => n + m.size_mb, 0);
    q(".su-ol-status").innerHTML = ol.reachable
      ? `<div class="su-status" data-level="ok"><i></i><b>Running</b><span>Ollama ${esc(ol.version)} at <code>${esc(ol.host)}</code> · ${ol.models.length} model${ol.models.length === 1 ? "" : "s"}, ${size(total) || "0 MB"}${
        ol.ram_mb ? ` · this PC has ${size(ol.ram_mb)} of RAM` : ""}</span></div>`
      : `<div class="su-status" data-level="bad"><i></i><b>Not running</b><span>Nothing answers at <code>${esc(ol.host)}</code>. Start the Ollama app (ollama.com/download), or fix the address under “Ollama server” below.</span></div>`;

    q(".su-ol-missing").innerHTML = (ol.missing || []).map((m) => {
      const pulling = ol.pulls.some((p) => p.name === m.name && p.state === "running");
      return `<div class="su-banner warn">${icon("t-alert")}<div><b>${esc(m.used_by.map(labelOf).join(", "))} ${m.used_by.length === 1 ? "uses" : "use"} <code>${esc(m.name)}</code>, which isn't downloaded</b>
        <span>Until it is, ${m.used_by.length === 1 ? "that agent skips" : "they skip"} it.</span></div>
        ${pulling ? "" : `<button type="button" class="btn sm primary" data-act="pull" data-name="${esc(m.name)}">${icon("t-down")}Download</button>`}</div>`;
    }).join("");

    q(".su-ol-models").innerHTML = !ol.reachable ? `<p class="su-muted">Start Ollama to see its models.</p>`
      : !ol.models.length ? `<p class="su-muted">No models yet. Download one below.</p>`
      : ol.models.map(modelRow).join("");

    q(".su-ol-pulls").innerHTML = (ol.pulls || []).slice().reverse().map(pullRow).join("");

    q(".su-ol-suggest").innerHTML = (ol.suggested || []).map((s) => {
      const pulling = ol.pulls.some((p) => p.name === s.name && p.state === "running");
      return `<div class="su-sug"><div><b class="mono">${esc(s.name)}</b><span>${esc(s.size)}${s.caps.length ? ` · ${s.caps.join(", ")}` : ""}</span><small>${esc(s.why)}</small></div>
        ${s.installed ? `<span class="su-pill" data-level="ok">Installed</span>`
          : pulling ? `<span class="su-pill" data-level="info">Downloading</span>`
          : `<button type="button" class="btn sm" data-act="pull" data-name="${esc(s.name)}" ${ol.reachable ? "" : "disabled"}>${icon("t-down")}Download</button>`}</div>`;
    }).join("");
  }

  function modelRow(m) {
    const caps = m.capabilities || [];
    const chips = [
      ...["tools", "vision", "thinking"].filter((c) => caps.includes(c)).map((c) => `<span class="su-chip">${{ tools: "Tools", vision: "Vision", thinking: "Thinking" }[c]}</span>`),
      m.loaded ? `<span class="su-chip" data-level="info">In memory</span>` : "",
      ...m.used_by.map((u) => `<span class="su-chip" data-level="ok">${esc(labelOf(u))}</span>`),
    ].join("");
    const name = esc(m.name);
    const opts = st.agents.map((a) => {
      const lacks = m.capabilities && a.needs.find((n) => !caps.includes(n));
      const current = a.profile.provider === "ollama" && norm(a.profile.model) === norm(m.name);
      return `<option value="${a.id}" ${current ? "disabled" : ""}>${esc(a.label)}${current ? " (already)" : lacks ? ` (can't ${lacks === "tools" ? "call tools" : "see"})` : ""}</option>`;
    }).join("");
    const b = (act) => busy.has(`${act}:${m.name}`);
    const del = confirmDel === m.name
      ? `<div class="su-confirm"><span>Delete ${name} and free ${size(m.size_mb)}?${m.used_by.length ? ` ${esc(m.used_by.map(labelOf).join(", "))} will stop working until you pick another model.` : ""}</span>
          <button type="button" class="btn sm danger" data-act="ol-delete-yes" data-name="${name}">Delete</button>
          <button type="button" class="btn sm" data-act="ol-delete-no">Keep</button></div>` : "";
    return `<div class="su-om" data-name="${name}">
      <div class="su-om-main"><b class="mono">${name}</b>
        <span class="su-om-meta">${esc([m.params, m.quant, size(m.size_mb), m.family].filter(Boolean).join(" · "))}</span>
        ${chips ? `<div class="su-chips">${chips}</div>` : ""}</div>
      <div class="su-om-actions">
        <select data-act="assign" data-name="${name}" aria-label="Use ${name} for an agent"><option value="">Use for…</option>${opts}</select>
        ${m.loaded
          ? `<button type="button" class="btn sm" data-act="ol-unload" data-name="${name}" ${b("unload") ? "disabled" : ""} title="Free the memory it holds">${b("unload") ? `<span class="spin"></span>` : ""}Unload</button>`
          : `<button type="button" class="btn sm" data-act="ol-load" data-name="${name}" ${b("load") ? "disabled" : ""} title="Load it now, so the next answer skips the cold start">${b("load") ? `<span class="spin"></span>Loading` : "Load"}</button>`}
        <button type="button" class="btn sm ghost danger" data-act="ol-delete" data-name="${name}" aria-label="Delete ${name}">${icon("t-trash")}</button>
      </div>${del}</div>`;
  }

  function pullRow(p) {
    const pct = p.total ? Math.min(100, (p.completed / p.total) * 100) : 0;
    const left = p.rate > 0 && p.total ? (p.total - p.completed) / p.rate : null;
    let line;
    if (p.state === "running") {
      line = p.total ? `${bytes(p.completed)} of ${bytes(p.total)}${p.rate ? ` · ${bytes(p.rate)}/s` : ""}${left != null && left > 1 ? ` · about ${secs(left)} left` : ""}` : esc(p.status);
      if (p.total && p.completed >= p.total) line = esc(p.status);  // verifying, writing manifest
    } else if (p.state === "done") line = "Downloaded. It's in the list above.";
    else if (p.state === "cancelled") line = "Cancelled.";
    else line = `Failed: ${esc(p.error)}`;
    const lvl = { running: "info", done: "ok", cancelled: "idle", failed: "bad" }[p.state];
    return `<div class="su-prog" data-state="${p.state}">
      <div class="su-prog-head"><b class="mono">${esc(p.name)}</b><span class="su-pill" data-level="${lvl}">${{ running: p.total ? `${Math.floor(pct)}%` : "Starting", done: "Done", cancelled: "Cancelled", failed: "Failed" }[p.state]}</span>
        <span class="grow"></span>
        ${p.state === "running" ? `<button type="button" class="btn sm" data-act="cancel" data-name="${esc(p.name)}">Cancel</button>`
          : p.state !== "done" ? `<button type="button" class="btn sm" data-act="pull" data-name="${esc(p.name)}">Try again</button>` : ""}</div>
      ${p.state === "running" ? `<div class="su-bar-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.floor(pct)}" aria-label="Downloading ${esc(p.name)}"><i style="width:${pct.toFixed(1)}%"></i></div>` : ""}
      <small>${line}</small></div>`;
  }

  async function refreshOllama() {
    const wasRunning = new Set((ol?.pulls ?? []).filter((p) => p.state === "running").map((p) => p.name));
    try {
      ol = await call("/api/setup/ollama");
    } catch {
      schedule();
      return;
    }
    for (const p of ol.pulls) {
      if (!wasRunning.has(p.name) || p.state === "running") continue;
      if (p.state === "done") toast(`Downloaded ${p.name}. Pick it for an agent from the Installed list.`, "ok");
      if (p.state === "failed") toast(`Couldn't download ${p.name}: ${p.error}`, "bad");
      delete lists.ollama;  // the model lists now include it
    }
    if (tab === "ollama") paintOllama();
    if (tab === "agents") for (const a of st.agents) { primeLists(a); paintMeta(a.id); }
    paintBadges();
    schedule();
  }

  // Fast while something downloads, slow while the Ollama tab is just open.
  function schedule() {
    clearTimeout(olTimer);
    if (page.hidden) return;
    if (ol?.pulls?.some((p) => p.state === "running")) olTimer = setTimeout(refreshOllama, 1200);
    else if (tab === "ollama") olTimer = setTimeout(refreshOllama, 8000);
  }

  async function ollamaAction(act, name) {
    const key = `${act}:${name}`;
    busy.add(key);
    paintOllama();
    try {
      ol = await call(`/api/setup/ollama/${act}`, "POST", { name });
      if (act === "delete") { delete lists.ollama; toast(`Deleted ${name}.`, "ok"); }
      if (act === "pull") toast(`Downloading ${name}. You can keep using Jarvis meanwhile.`, "ok");
    } catch (e) {
      toast(`${{ pull: "Couldn't start the download", load: "Couldn't load it", unload: "Couldn't unload it", delete: "Couldn't delete it", cancel: "Couldn't cancel" }[act]}: ${errText(e)}`, "bad");
    }
    busy.delete(key);
    if (tab === "ollama") paintOllama();
    if (tab === "agents") for (const a of st.agents) paintMeta(a.id);
    paintBadges();
    schedule();
  }

  // ---------- API keys ----------

  function keysView() {
    return intro("API keys", "Stored in <code>.env</code> on this PC and never shown in full. They apply at once. " +
      "An agent can also have its own key, under its More options.") +
      `<section class="su-card su-keys">${st.keys.map(keyRow).join("")}</section>`;
  }

  function keyRow(k) {
    const used = k.used_by.length ? `Used by ${k.used_by.map(labelOf).join(", ")}` : k.note || "Not used by any agent right now";
    return `<div class="su-key" data-field="${k.field}">
      <div class="su-key-head"><b>${esc(k.label)}</b><code class="su-env">${esc(k.env)}</code>
        <span class="su-pill" data-level="${k.set ? "ok" : "idle"}">${k.set ? `Set ${esc(k.hint)}` : "Not set"}</span>
        <span class="grow"></span>
        ${k.keys_url ? `<a class="su-ext" href="${esc(k.keys_url)}" target="_blank" rel="noopener">Get a key ↗</a>` : ""}</div>
      <p class="su-muted">${esc(used)}</p>
      ${k.shadowed ? `<p class="su-note" data-level="warn">${icon("t-alert", "xs")}<span>A Windows environment variable ${esc(k.env)} is set. It wins over .env, so a change here only shows up after you remove it.</span></p>` : ""}
      <div class="su-key-row">
        <input type="password" data-key-input autocomplete="off" spellcheck="false" aria-label="${esc(k.label)} key"
          placeholder="${k.set ? "Paste a new key to replace it" : "Paste the key"}">
        <button type="button" class="btn sm primary" data-act="key-save">Save</button>
        ${k.checkable && k.set ? `<button type="button" class="btn sm" data-act="key-check">Check</button>` : ""}
        ${k.set ? `<button type="button" class="btn sm ghost danger" data-act="key-clear">Remove</button>` : ""}
      </div>
      <div data-key-result></div></div>`;
  }

  async function saveKey(field, value, row) {
    try {
      const r = await call("/api/setup/keys", "PUT", { values: { [field]: value } });
      st.keys = r.keys;
      for (const p of st.providers) p.key_set = !!r.providers_key_set[p.id];
      render();
      const k = st.keys.find((x) => x.field === field);
      toast(`${value ? "Saved" : "Removed"} ${k.env}.${shadowNote(r.result)}`, r.result.shadowed.length ? "warn" : "ok");
      return true;
    } catch (e) {
      row.querySelector("[data-key-result]").innerHTML = `<p class="su-note" data-level="bad">${icon("t-x", "xs")}<span>${esc(errText(e))}</span></p>`;
      return false;
    }
  }

  // ---------- settings sections ----------

  function sectionsView(tabId) {
    const t = TABS.find((x) => x.id === tabId);
    const secs = st.sections.filter((s) => s.tab === tabId);
    const n = secs.reduce((c, s) => c + s.fields.length, 0);
    const search = tabId === "advanced"
      ? `<input type="search" id="su-search" class="su-search" placeholder="Search ${n} settings, e.g. wake, barge, tts" value="${esc(filter)}" aria-label="Search settings">` : "";
    // One section: the page's title and intro are its title and note.
    const single = secs.length === 1 && tabId !== "advanced";
    return intro(t.label, esc(single ? secs[0].note : INTRO[tabId] || "")) + search +
      secs.map((s) => sectionHTML(s, single)).join("") +
      (tabId === "advanced" ? `<p class="su-muted su-none" id="su-nomatch" hidden>No setting matches.</p>` : "");
  }

  const secById = (id) => st.sections.find((s) => s.id === id);
  const valueOf = (secId, f) => (fdrafts[secId] && f.key in fdrafts[secId] ? fdrafts[secId][f.key] : f.value);

  function sectionHTML(sec, bare = false) {
    const dirtySec = fdrafts[sec.id] && Object.keys(fdrafts[sec.id]).length;
    return `<section class="su-card su-sec" data-sec="${sec.id}">
      ${bare ? "" : `<header class="su-card-head"><div class="su-head-text"><h3>${esc(sec.title)}</h3>${sec.note ? `<p>${esc(sec.note)}</p>` : ""}</div></header>`}
      <div class="su-fields">${sec.fields.map((f) => fieldHTML(sec, f)).join("")}</div>
      <footer class="su-card-foot" ${dirtySec ? "" : "hidden"}><span class="su-stats">Unsaved changes</span><span class="grow"></span>
        <button type="button" class="btn" data-act="sec-discard">Discard</button>
        <button type="button" class="btn primary" data-act="sec-save">Save</button></footer>
    </section>`;
  }

  function fieldHTML(sec, f) {
    const v = valueOf(sec.id, f);
    const id = `su-f-${f.key}`;
    const adv = sec.tab === "advanced";
    const wide = ["text", "json", "tools"].includes(f.type);
    const tag = f.apply !== "now" ? `<span class="su-apply" data-apply="${f.apply}">${APPLY[f.apply]}</span>` : "";
    const reset = f.type !== "tools" && !same(v, f.default)
      ? `<button type="button" class="link su-reset" data-reset="${f.key}" title="Default: ${esc(f.default === "" ? "(empty)" : f.default)}">Reset</button>` : "";
    const text = `${f.label} ${f.env} ${f.hint}`.toLowerCase();
    return `<div class="su-field ${wide ? "wide" : ""}" data-key="${f.key}" data-type="${f.type}" data-text="${esc(text)}">
      <div class="su-field-text">
        <label for="${id}">${adv ? `<code>${esc(f.env)}</code>` : esc(f.label)}</label>${tag}
        ${f.hint ? `<small class="${adv ? "su-clamp" : ""}">${esc(f.hint)}</small>` : ""}
        ${adv ? "" : `<code class="su-env">${esc(f.env)}</code>`}
      </div>
      <div class="su-field-input">${inputHTML(f, v, id)}${reset}</div>
    </div>`;
  }

  function inputHTML(f, v, id) {
    switch (f.type) {
      case "bool":
        return `<input type="checkbox" class="switch" id="${id}" data-v ${v ? "checked" : ""}>`;
      case "int":
      case "float":
        return `<input type="number" id="${id}" data-v value="${esc(v ?? "")}" step="${f.type === "int" ? 1 : "any"}"
          ${f.min != null ? `min="${f.min}"` : ""} ${f.max != null ? `max="${f.max}"` : ""} ${f.nullable ? `placeholder="not set"` : ""}>`;
      case "choice": {
        const opts = f.options.some(([val]) => same(val, v)) ? f.options : [[v, v], ...f.options];
        return `<select id="${id}" data-v>${opts.map(([val, lab]) => `<option value="${esc(val)}" ${same(val, v) ? "selected" : ""}>${esc(lab)}</option>`).join("")}</select>`;
      }
      case "text":
        return `<textarea id="${id}" data-v rows="5">${esc(v)}</textarea>`;
      case "json":
        return `<textarea id="${id}" data-v rows="3" class="mono" spellcheck="false">${esc(v)}</textarea>`;
      case "tools":
        return toolsPicker(f, v, id);
      default:
        return `<input type="text" id="${id}" data-v value="${esc(v)}" spellcheck="false" ${f.suggest ? `list="${id}-dl"` : ""}>` +
          (f.suggest ? `<datalist id="${id}-dl">${f.suggest.map((s) => `<option value="${esc(s)}">`).join("")}</datalist>` : "");
    }
  }

  function toolsPicker(f, v, id) {
    const spec = String(v ?? "").trim();
    const mode = spec === "all" ? "all" : spec ? "some" : "none";
    const chosen = new Set(mode === "some" ? spec.split(",").map((s) => s.trim()) : []);
    return `<div class="su-tools" id="${id}" data-v data-mode="${mode}">
      <div class="seg" role="radiogroup" aria-label="Which tools">${[["all", "All tools"], ["some", "Only these"], ["none", "None"]]
        .map(([m, l]) => `<button type="button" role="radio" data-tools-mode="${m}" aria-checked="${m === mode}">${l}</button>`).join("")}</div>
      <div class="su-tool-grid" ${mode === "some" ? "" : "hidden"}>${st.tools.map((t) => `
        <label class="su-tool"><input type="checkbox" value="${esc(t.name)}" ${chosen.has(t.name) ? "checked" : ""}>
          <span><b class="mono">${esc(t.name)}</b>${t.action ? `<i>action</i>` : ""}<small title="${esc(t.description)}">${esc(t.unavailable ? `Unavailable now: ${t.unavailable}` : t.description)}</small></span></label>`).join("")}
      </div></div>`;
  }

  function readValue(fieldEl) {
    const type = fieldEl.dataset.type;
    const input = fieldEl.querySelector("[data-v]");
    if (type === "bool") return input.checked;
    if (type === "int" || type === "float") return input.value === "" ? null : Number(input.value);
    if (type === "tools") {
      const mode = input.dataset.mode;
      if (mode !== "some") return mode === "all" ? "all" : "";
      return [...input.querySelectorAll(".su-tool-grid input:checked")].map((c) => c.value).join(",");
    }
    return input.value;
  }

  function noteField(fieldEl) {
    const secEl = fieldEl.closest("[data-sec]");
    const sec = secById(secEl.dataset.sec);
    const f = sec.fields.find((x) => x.key === fieldEl.dataset.key);
    const v = readValue(fieldEl);
    const d = (fdrafts[sec.id] ??= {});
    if (same(v, f.value)) delete d[f.key]; else d[f.key] = v;
    secEl.querySelector(".su-card-foot").hidden = !Object.keys(d).length;
    paintBadges();
  }

  async function saveSection(secId) {
    const values = fdrafts[secId];
    if (!values || !Object.keys(values).length) return;
    const btn = main.querySelector(`[data-sec="${secId}"] [data-act="sec-save"]`);
    if (btn) btn.disabled = true;
    try {
      const r = await call("/api/setup/fields", "PUT", { values });
      st.sections = r.sections;
      st.restart_pending = r.restart_pending;
      delete fdrafts[secId];
      render();
      applyToast(r.result);
    } catch (e) {
      toast(`Not saved: ${errText(e)}`, "bad");
      if (btn) btn.disabled = false;
    }
  }

  function applyToast(res) {
    if (!res.changed.length) { toast("Saved. Nothing was different.", "ok"); return; }
    const parts = [];
    if (res.now.length) parts.push(res.reconnect.length || res.restart.length ? "some changes apply now" : "it applies now");
    if (res.reconnect.length) parts.push(`${res.reconnect.length === res.changed.length ? "it applies" : "some apply"} from the next connection`);
    if (res.restart.length) parts.push(`${res.restart.length === 1 ? "one change needs" : `${res.restart.length} changes need`} a restart of Jarvis`);
    const text = `Saved to .env: ${parts.join(", ")}.${shadowNote(res)}`;
    const level = res.restart.length || res.shadowed.length ? "warn" : "ok";
    toast(text, level, res.reconnect.length ? { label: "Reconnect now", run: () => location.reload() } : null);
  }

  function applyFilter() {
    const q = filter.trim().toLowerCase();
    let any = false;
    for (const sec of main.querySelectorAll("[data-sec]")) {
      let shown = 0;
      for (const f of sec.querySelectorAll(".su-field")) {
        const hit = !q || f.dataset.text.includes(q);
        f.hidden = !hit;
        shown += hit;
      }
      sec.hidden = !shown;
      any ||= shown > 0;
    }
    const none = $("su-nomatch");
    if (none) none.hidden = any;
  }

  // ---------- toast ----------

  function toast(text, level = "ok", action = null) {
    const t = $("su-toast");
    t.dataset.level = level;
    t.innerHTML = `${icon(level === "bad" ? "t-x" : level === "warn" ? "t-alert" : "t-check", "xs")}<span>${esc(text)}</span>
      ${action ? `<button type="button" class="mini-btn" data-toast-act>${esc(action.label)}</button>` : ""}
      <button type="button" class="su-toast-x" aria-label="Dismiss">${icon("i-x", "xs")}</button>`;
    t.hidden = false;
    if (action) t.querySelector("[data-toast-act]").onclick = () => { t.hidden = true; action.run(); };
    t.querySelector(".su-toast-x").onclick = () => { t.hidden = true; };
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, action ? 12000 : level === "bad" ? 10000 : 6000);
  }

  // ---------- events ----------

  page.querySelector("[data-close]").onclick = close;

  const nav = page.querySelector(".su-nav");
  nav.addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b) setTab(b.dataset.tab);
  });
  nav.addEventListener("keydown", (e) => {
    const dir = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
    if (!dir) return;
    e.preventDefault();
    const i = (TABS.findIndex((t) => t.id === tab) + dir + TABS.length) % TABS.length;
    setTab(TABS[i].id);
    nav.querySelector(`[data-tab="${TABS[i].id}"]`).focus();
  });

  // The app's one-letter shortcuts must not fire behind the page.
  page.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (confirmDel) { confirmDel = ""; paintOllama(); return; }
      close();
      return;
    }
    if (!e.ctrlKey && !e.metaKey && !e.altKey) e.stopPropagation();
  });

  main.addEventListener("toggle", (e) => {
    const card = e.target.closest?.("[data-agent]");
    if (card && e.target.matches("details.su-more")) openMore[card.dataset.agent] = e.target.open;
  }, true);

  main.addEventListener("input", (e) => {
    const t = e.target;
    const card = t.closest("[data-agent]");
    if (card && t.dataset.f && t.dataset.f !== "provider") {
      const id = card.dataset.agent, d = draftOf(id);
      const row = t.closest(".su-entry");
      const i = row ? Number(row.dataset.i) : 0;
      const entry = i ? d.fallback[i - 1] : d;
      if (t.dataset.f === "model_key") d.model_key = t.value.trim() || null;
      else entry[t.dataset.f] = t.value;
      if (t.dataset.f === "base_url" && entry.provider === "openai_compat") {
        const input = row?.querySelector('[data-f="model"]');
        if (input) input.setAttribute("list", listId(entry.provider, entry.base_url));
      }
      delete tests[`${id}:${i}`];
      paintMeta(id);
      return;
    }
    const field = t.closest(".su-field");
    if (field && field.dataset.type !== "bool" && field.dataset.type !== "choice") noteField(field);
    if (t.id === "su-search") { filter = t.value; applyFilter(); }
  });

  main.addEventListener("change", (e) => {
    const t = e.target;
    const card = t.closest("[data-agent]");
    if (card && t.dataset.f === "provider") {
      const id = card.dataset.agent, d = draftOf(id);
      const i = Number(t.closest(".su-entry").dataset.i);
      const entry = i ? d.fallback[i - 1] : d;
      entry.provider = t.value;
      entry.model = "";
      entry.base_url = "";
      if (!i) d.model_key = null;
      delete tests[`${id}:${i}`];
      paintAgent(id, `.su-entry[data-i="${i}"] [data-f="${t.value === "openai_compat" ? "base_url" : "model"}"]`);
      ensureList(entry.provider, entry.base_url, id);
      return;
    }
    if (card && t.dataset.f === "base_url") {
      const d = draftOf(card.dataset.agent);
      const i = Number(t.closest(".su-entry")?.dataset.i ?? 0);
      const entry = i ? d.fallback[i - 1] : d;
      ensureList(entry.provider, entry.base_url, card.dataset.agent);
      return;
    }
    if (t.matches('select[data-act="assign"]')) {
      if (t.value) assign(t.value, t.dataset.name);
      t.value = "";
      return;
    }
    const field = t.closest(".su-field");
    if (field) noteField(field);
  });

  main.addEventListener("submit", (e) => {
    if (e.target.id !== "su-pull") return;
    e.preventDefault();
    const input = e.target.elements.name;
    const name = input.value.trim();
    if (!name) return;
    input.value = "";
    ollamaAction("pull", name);
  });

  main.addEventListener("click", (e) => {
    const t = e.target.closest("button, [data-goto]");
    if (!t) return;
    if (t.dataset.goto) { setTab(t.dataset.goto); return; }
    if (t.dataset.toolsMode) {
      const box = t.closest(".su-tools");
      const prev = box.dataset.mode;
      box.dataset.mode = t.dataset.toolsMode;
      for (const b of box.querySelectorAll("[data-tools-mode]")) b.setAttribute("aria-checked", String(b === t));
      const grid = box.querySelector(".su-tool-grid");
      grid.hidden = t.dataset.toolsMode !== "some";
      // "Only these" starts from "All": every tool, including ones that are
      // unavailable right now (camera off...), which "All" would offer later.
      if (t.dataset.toolsMode === "some" && prev === "all")
        for (const c of grid.querySelectorAll("input")) c.checked = true;
      noteField(box.closest(".su-field"));
      return;
    }
    if (t.dataset.reset) {
      const field = t.closest(".su-field");
      const sec = secById(field.closest("[data-sec]").dataset.sec);
      const f = sec.fields.find((x) => x.key === t.dataset.reset);
      const input = field.querySelector("[data-v]");
      if (f.type === "bool") input.checked = !!f.default; else input.value = f.default ?? "";
      noteField(field);
      t.remove();
      return;
    }
    const act = t.dataset.act;
    if (!act) return;
    const card = t.closest("[data-agent]");
    const a = card && agentById(card.dataset.agent);
    const row = t.closest(".su-entry");
    const i = row ? Number(row.dataset.i) : 0;
    switch (act) {
      case "reload": load(); break;
      case "test": runTest(a, i); break;
      case "save": saveAgent(a); break;
      case "discard":
        delete drafts[a.id];
        for (const k of Object.keys(tests)) if (k.startsWith(`${a.id}:`)) delete tests[k];
        paintAgent(a.id);
        break;
      case "add-fb": {
        const d = draftOf(a.id);
        const last = d.fallback.at(-1) ?? d;
        d.fallback.push({ provider: last.provider === "ollama" ? "groq" : "ollama", model: "", base_url: "" });
        paintAgent(a.id, `.su-entry[data-i="${d.fallback.length}"] [data-f="model"]`);
        primeLists(a);
        break;
      }
      case "up": case "down": case "remove": {
        const fb = draftOf(a.id).fallback, k = i - 1;
        if (act === "remove") fb.splice(k, 1);
        else {
          const j = act === "up" ? k - 1 : k + 1;
          [fb[k], fb[j]] = [fb[j], fb[k]];
        }
        for (const key of Object.keys(tests)) if (key.startsWith(`${a.id}:`) && key !== `${a.id}:0`) delete tests[key];
        paintAgent(a.id);
        break;
      }
      case "clear-key": draftOf(a.id).model_key = ""; paintAgent(a.id); break;
      case "relist": {
        const d = draftOf(a.id);
        ensureList(d.provider, d.base_url, a.id, true);
        break;
      }
      case "pull": ollamaAction("pull", t.dataset.name); break;
      case "cancel": ollamaAction("cancel", t.dataset.name); break;
      case "ol-load": ollamaAction("load", t.dataset.name); break;
      case "ol-unload": ollamaAction("unload", t.dataset.name); break;
      case "ol-delete": confirmDel = t.dataset.name; paintOllama(); break;
      case "ol-delete-no": confirmDel = ""; paintOllama(); break;
      case "ol-delete-yes": confirmDel = ""; ollamaAction("delete", t.dataset.name); break;
      case "ol-refresh": refreshOllama(); break;
      case "sec-save": saveSection(t.closest("[data-sec]").dataset.sec); break;
      case "sec-discard": delete fdrafts[t.closest("[data-sec]").dataset.sec]; render(); break;
      case "key-save": {
        const rowEl = t.closest("[data-field]");
        const value = rowEl.querySelector("[data-key-input]").value.trim();
        if (!value) { rowEl.querySelector("[data-key-input]").focus(); break; }
        saveKey(rowEl.dataset.field, value, rowEl);
        break;
      }
      case "key-clear": {
        const rowEl = t.closest("[data-field]");
        if (t.dataset.armed) { saveKey(rowEl.dataset.field, "", rowEl); break; }
        t.dataset.armed = "1";
        t.textContent = "Really remove?";
        setTimeout(() => { if (t.isConnected) { delete t.dataset.armed; t.textContent = "Remove"; } }, 4000);
        break;
      }
      case "key-check": {
        const rowEl = t.closest("[data-field]");
        const out = rowEl.querySelector("[data-key-result]");
        out.innerHTML = `<p class="su-note" data-level="info"><span class="spin"></span><span>Checking…</span></p>`;
        call("/api/setup/keys/check", "POST", { field: rowEl.dataset.field })
          .then((r) => r, (err) => ({ ok: false, error: errText(err) }))
          .then((r) => {
            out.innerHTML = `<p class="su-note" data-level="${r.ok ? "ok" : "bad"}">${icon(r.ok ? "t-check" : "t-x", "xs")}<span>${esc(r.ok ? r.detail : r.error)}</span></p>`;
          });
        break;
      }
    }
  });

  main.addEventListener("keydown", (e) => {
    // Enter in a key box saves it.
    if (e.key === "Enter" && e.target.matches("[data-key-input]")) {
      e.preventDefault();
      e.target.closest("[data-field]").querySelector('[data-act="key-save"]').click();
    }
  });

  $("setup-btn")?.addEventListener("click", () => open());
  $("drawer-setup")?.addEventListener("click", () => open());
  addEventListener("hashchange", () => {
    if (location.hash.startsWith("#setup")) {
      const want = location.hash.split("/")[1];
      if (page.hidden) open(want);
      else if (want && want !== tab && TABS.some((t) => t.id === want)) setTab(want);
    } else if (!page.hidden) close();
  });
  if (location.hash.startsWith("#setup")) open(location.hash.split("/")[1]);

  return { open, close, isOpen };
}
