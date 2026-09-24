// The conversation pane: messages, tool cards, live timers.
//
// app.js tells this what happened (a transcript piece, a reply delta, a tool
// call...) and this decides how it looks. Nothing here talks to the socket;
// the one way back is onAsk, for chips and "Retry".

// How each tool reads in the chat. `args` turns the call's arguments into a
// few short tags; anything unknown falls back to the raw name.
const TOOLS = {
  set_timer:          { icon: "timer",  run: "Setting a timer",           done: "Timer set",             args: (a) => [dur(a.seconds), a.label && `“${a.label}”`] },
  get_datetime:       { icon: "clock",  run: "Checking the time",         done: "Checked the time" },
  web_search:         { icon: "search", run: "Searching the web",         done: "Searched the web",      args: (a) => [a.query && `“${a.query}”`] },
  read_webpage:       { icon: "page",   run: "Reading a page",            done: "Read a page",           args: (a) => [host(a.url)] },
  open_url:           { icon: "link",   run: "Opening a link",            done: "Opened a link",         args: (a) => [host(a.url)] },
  open_app:           { icon: "app",    run: "Opening an app",            done: "Opened an app",         args: (a) => [a.name] },
  remember:           { icon: "brain",  run: "Remembering",               done: "Remembered",            args: (a) => [a.fact && `“${a.fact}”`] },
  recall:             { icon: "brain",  run: "Searching memory",          done: "Searched memory",       args: (a) => [a.query && `“${a.query}”`] },
  forget:             { icon: "trash",  run: "Forgetting",                done: "Forgot",                args: (a) => [a.query && `“${a.query}”`] },
  look:               { icon: "eye",    run: "Looking through the camera", done: "Looked",               args: (a) => [a.question && `“${a.question}”`] },
  start_motion_watch: { icon: "video",  run: "Starting motion watch",     done: "Watching for motion" },
  stop_motion_watch:  { icon: "video",  run: "Stopping motion watch",     done: "Stopped watching" },
  camera_status:      { icon: "camera", run: "Checking the camera",       done: "Checked the camera" },
  web_task:           { icon: "globe",  run: "Asking the web agent",      done: "Handed to the web agent", args: (a) => [a.task && `“${a.task}”`] },
  web_reply:          { icon: "globe",  run: "Answering the web agent",   done: "Answered the web agent",  args: (a) => [a.answer && `“${a.answer}”`] },
  web_status:         { icon: "globe",  run: "Checking the web agent",    done: "Checked the web agent" },
  web_stop:           { icon: "globe",  run: "Stopping the web agent",    done: "Stopped the web agent" },
};
const toolInfo = (name) => TOOLS[name] ?? {
  icon: "tool", run: `Running ${name.replaceAll("_", " ")}`, done: name.replaceAll("_", " "),
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const icon = (id, cls = "ic") => `<svg class="${cls}"><use href="#t-${id}"/></svg>`;
const hhmm = () => new Date().toTimeString().slice(0, 5);
function dur(sec) {
  sec = Math.max(0, Math.round(Number(sec) || 0));
  const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
  if (h) return `${h} h${m ? ` ${m} min` : ""}`;
  if (m) return `${m} min${s ? ` ${s} s` : ""}`;
  return `${s} s`;
}
const clockOf = (sec) => {
  sec = Math.max(0, Math.ceil(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`;
};
function host(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}
const safeUrl = (u) => /^https?:\/\//i.test(u) ? u : null;

// web_search output: "1. Title (site.com): snippet <https://…>" per line.
function parseSearch(text) {
  const out = [];
  for (const line of String(text).split("\n")) {
    const m = line.match(/^\s*\d+\.\s*(.+?)\s*\(([^)]+)\):\s*(.*?)\s*<(https?:[^>]+)>\s*$/);
    if (m) out.push({ title: m[1], site: m[2], snippet: m[3], url: m[4] });
  }
  return out;
}

// A small, safe subset of Markdown for replies: code blocks, inline code,
// bold, italics, bullet and numbered lists, and bare links. Everything is
// escaped first, so model output can never inject markup.
function renderRich(text) {
  const blocks = [];
  let src = esc(text).replace(/```(\w*)\n?([\s\S]*?)(```|$)/g, (_, lang, code) => {
    blocks.push(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });
  const inline = (s) => s
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?]|$)/g, "$1<i>$2</i>")
    .replace(/(https?:\/\/[^\s<)]+[^\s<).,!?])/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
  const lines = src.split("\n");
  let html = "", list = null;
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const line of lines) {
    const ul = line.match(/^\s*[-*•]\s+(.*)$/), ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      const tag = ul ? "ul" : "ol";
      if (list !== tag) { close(); html += `<${tag}>`; list = tag; }
      html += `<li>${inline((ul || ol)[1])}</li>`;
    } else {
      close();
      html += line.trim() ? `<p>${inline(line)}</p>` : "";
    }
  }
  close();
  return html.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[i]);
}

export function createChat({ convo, empty, onAsk, onWeb }) {
  let live = null;            // the assistant message being streamed
  let userMsg = null, userTurn = null;
  let typing = null;
  const tools = [];           // cards still running, oldest first
  const timers = new Map();   // id -> { el, end, total, label }
  let timerSeq = 0;

  // ---------- plumbing ----------

  const scroller = convo;
  const atBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 90;
  const jump = document.createElement("button");
  jump.className = "jump";
  jump.type = "button";
  jump.innerHTML = `${icon("down")}<span>New messages</span>`;
  jump.hidden = true;
  jump.onclick = () => { scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" }); };
  convo.parentElement.append(jump);
  scroller.addEventListener("scroll", () => { if (atBottom()) jump.hidden = true; });

  const strip = document.createElement("div");
  strip.className = "timers";
  strip.hidden = true;
  convo.parentElement.insertBefore(strip, convo);

  function put(el, { force = false } = {}) {
    empty?.remove();
    const stick = force || atBottom();
    if (typing && el !== typing) convo.insertBefore(el, typing); else convo.append(el);
    settle(stick);
    return el;
  }
  function settle(stick) {
    if (stick) scroller.scrollTop = scroller.scrollHeight;
    else jump.hidden = false;
  }

  function meta(parts) {
    const m = document.createElement("div");
    m.className = "meta";
    m.innerHTML = parts.filter(Boolean).join("");
    return m;
  }

  // ---------- messages ----------

  function userRow(text, spoken) {
    const row = document.createElement("div");
    row.className = "msg user";
    const b = document.createElement("div");
    b.className = "bubble";
    b.textContent = text;
    row.append(b, meta([spoken ? `<span class="spoken" title="Spoken">${icon("mic", "ic xs")}</span>` : "", `<time>${hhmm()}</time>`]));
    row.bubble = b;
    return put(row, { force: true });
  }

  function assistantRow() {
    stopTyping();
    const row = document.createElement("div");
    row.className = "msg assistant";
    row.innerHTML = `<span class="avatar" aria-hidden="true">${icon("bot")}</span><div class="stack"><div class="bubble rich"></div></div>`;
    const stack = row.querySelector(".stack");
    const copy = `<button class="mini" data-copy title="Copy">${icon("copy", "ic xs")}</button>`;
    stack.append(meta([`<time>${hhmm()}</time>`, copy]));
    row.bubble = row.querySelector(".bubble");
    row.raw = "";
    row.querySelector("[data-copy]").onclick = async (e) => {
      try {
        await navigator.clipboard.writeText(row.raw);
        e.currentTarget.classList.add("ok");
        setTimeout(() => e.currentTarget?.classList.remove("ok"), 1200);
      } catch {}
    };
    return put(row);
  }

  function startTyping() {
    if (typing || live) return;
    typing = document.createElement("div");
    typing.className = "msg assistant typing";
    typing.innerHTML = `<span class="avatar" aria-hidden="true">${icon("bot")}</span><div class="bubble"><i></i><i></i><i></i></div>`;
    empty?.remove();
    const stick = atBottom();
    convo.append(typing);
    settle(stick);
  }
  function stopTyping() { typing?.remove(); typing = null; }

  // ---------- tools ----------

  function toolCall(msg) {
    stopTyping();
    live = null;  // what comes after a tool is a new thought
    const info = toolInfo(msg.name);
    let args = msg.args;
    if (typeof args === "string") { try { args = JSON.parse(args); } catch {} }
    const tags = (info.args?.(args ?? {}) ?? []).filter(Boolean);

    const el = document.createElement("div");
    el.className = "toolcard";
    el.dataset.status = "running";
    el.innerHTML =
      `<button class="tool-head" type="button" aria-expanded="false">` +
        `<span class="tool-icon">${icon(info.icon)}</span>` +
        `<span class="tool-main"><b>${esc(info.run)}</b>${tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</span>` +
        `<span class="tool-state"><span class="spin"></span><span class="elapsed">0.0 s</span></span>` +
        `<span class="chev">${icon("chev", "ic xs")}</span>` +
      `</button>` +
      `<div class="tool-extra"></div>` +
      `<div class="tool-body" hidden>` +
        `<div class="kv"><span>Tool</span><code>${esc(msg.name)}</code></div>` +
        (args && typeof args === "object" && Object.keys(args).length
          ? `<div class="kv"><span>Input</span><pre>${esc(JSON.stringify(args, null, 2))}</pre></div>` : "") +
        `<div class="kv result" hidden><span>Result</span><pre></pre></div>` +
      `</div>`;
    const head = el.querySelector(".tool-head"), body = el.querySelector(".tool-body");
    head.onclick = () => {
      const open = body.hidden;
      body.hidden = !open;
      head.setAttribute("aria-expanded", String(open));
    };
    const started = performance.now();
    el.tick = setInterval(() => {
      el.querySelector(".elapsed").textContent = `${((performance.now() - started) / 1000).toFixed(1)} s`;
    }, 100);
    tools.push({ name: msg.name, el, args, info });
    group(el);
  }

  // Tools used back to back share a group. From three on, only the newest
  // shows; the rest fold behind "Show N earlier steps".
  function group(el) {
    let last = convo.lastElementChild;
    if (last === typing) last = last.previousElementSibling;
    if (last?.classList.contains("toolcard")) {
      const g = document.createElement("div");
      g.className = "toolgroup";
      g.innerHTML = `<button class="tg-toggle" type="button"></button>`;
      last.replaceWith(g);
      g.append(last);
      g.querySelector(".tg-toggle").onclick = () => {
        g.dataset.touched = "1";  // your choice sticks as more steps arrive
        g.classList.toggle("collapsed");
        label(g);
      };
      last = g;
    }
    if (last?.classList.contains("toolgroup")) {
      const stick = atBottom();
      last.append(el);
      label(last);
      settle(stick);
    } else {
      put(el);
    }
  }
  function label(g) {
    const cards = g.querySelectorAll(".toolcard").length;
    const btn = g.querySelector(".tg-toggle");
    btn.hidden = cards < 3;
    if (!g.dataset.touched) g.classList.toggle("collapsed", cards >= 3);
    const earlier = cards - 1;
    btn.innerHTML = g.classList.contains("collapsed")
      ? `${icon("chev", "ic xs")}Show ${earlier} earlier step${earlier === 1 ? "" : "s"}`
      : `${icon("chev", "ic xs")}Hide earlier steps`;
  }

  function toolResult(msg) {
    const i = tools.findIndex((t) => t.name === msg.name);
    if (i < 0) return;
    const { el, args, info } = tools.splice(i, 1)[0];
    clearInterval(el.tick);
    el.dataset.status = msg.ok ? "ok" : "fail";
    el.querySelector(".tool-main b").textContent = msg.ok ? info.done : `${info.run} — failed`;
    el.querySelector(".tool-state").innerHTML = msg.ok
      ? `${icon("check", "ic xs")}<span>${fmtMs(msg.ms)}</span>`
      : `${icon("x", "ic xs")}<span>failed</span>`;
    const result = el.querySelector(".kv.result");
    if (msg.preview) {
      result.hidden = false;
      result.querySelector("pre").textContent = msg.preview;
    }
    const extra = el.querySelector(".tool-extra");
    if (!msg.ok) {
      extra.innerHTML = `<p class="tool-error">${esc(String(msg.preview || "The tool reported an error.").slice(0, 240))}</p>`;
    } else if (msg.name === "web_search") {
      const hits = parseSearch(msg.preview);
      if (!hits.length && /^no results/i.test(msg.preview || "")) {
        // It "worked", but found nothing: say so instead of a green tick.
        el.dataset.status = "empty";
        el.querySelector(".tool-state").innerHTML = `${icon("search", "ic xs")}<span>no results</span>`;
      }
      if (hits.length) {
        extra.innerHTML = `<div class="sources">` + hits.map((h, n) => {
          const url = safeUrl(h.url);
          return `<a class="source" href="${esc(url ?? "#")}" target="_blank" rel="noopener noreferrer" title="${esc(h.snippet)}">` +
            `<span class="num">${n + 1}</span><span class="src-text"><b>${esc(h.title)}</b><small>${esc(h.site)}</small></span></a>`;
        }).join("") + `</div>`;
      }
    } else if (msg.name === "set_timer" && args?.seconds) {
      addTimer(Number(args.seconds), args.label || "");
    } else if ((msg.name === "open_url" || msg.name === "read_webpage") && safeUrl(args?.url)) {
      extra.innerHTML = `<div class="sources"><a class="source" href="${esc(args.url)}" target="_blank" rel="noopener noreferrer">` +
        `<span class="num">${icon("link", "ic xs")}</span><span class="src-text"><b>${esc(host(args.url))}</b><small>${esc(args.url)}</small></span></a></div>`;
    }
    settle(atBottom());
  }
  const fmtMs = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.max(1, Math.round(ms))} ms`);

  // ---------- timers ----------

  function addTimer(seconds, label) {
    const id = ++timerSeq;
    const el = document.createElement("div");
    el.className = "timer";
    el.innerHTML = `${icon("timer")}<span class="t-label">${esc(label || "Timer")}</span>` +
      `<b class="t-left">${clockOf(seconds)}</b><span class="t-bar"><i></i></span>`;
    strip.append(el);
    strip.hidden = false;
    timers.set(id, { el, end: Date.now() + seconds * 1000, total: seconds, label });
  }

  setInterval(() => {
    for (const [, t] of timers) {
      if (t.done) continue;
      const left = (t.end - Date.now()) / 1000;
      t.el.querySelector(".t-left").textContent = clockOf(left);
      t.el.querySelector(".t-bar i").style.width = `${Math.max(0, Math.min(100, (left / t.total) * 100))}%`;
    }
  }, 250);

  function timerRang(data) {
    // The one due soonest, preferring a matching label.
    const label = data?.label ?? "";
    const active = [...timers].filter(([, t]) => !t.done).sort((a, b) => a[1].end - b[1].end);
    const [hit] = active.find(([, t]) => t.label === label) ?? active[0] ?? [null];
    if (hit === null) return;
    const t = timers.get(hit);
    t.done = true;
    t.el.classList.add("done");
    t.el.querySelector(".t-left").textContent = "done";
    setTimeout(() => {
      t.el.remove();
      timers.delete(hit);
      if (!timers.size) strip.hidden = true;
    }, 6000);
  }

  // ---------- web agent ----------

  // One card per web task, updated in place as the agent works: its steps,
  // the latest look at the page, and Yes / No when it needs your say-so.
  const webCards = new Map();
  const STATE_TEXT = { working: "working…", waiting: "needs you", done: "done", stopped: "stopped", failed: "failed" };

  function webCard(d) {
    let card = webCards.get(d.task);
    if (card) return card;
    stopTyping();
    live = null;
    card = document.createElement("div");
    card.className = "webcard";
    card.innerHTML =
      `<div class="wc-head">` +
        `<span class="tool-icon">${icon("globe")}</span>` +
        `<span class="wc-title"><b>Web agent</b><small></small></span>` +
        `<span class="wc-state"><span class="spin"></span><span></span></span>` +
        `<button class="mini-btn" type="button" data-stop>Stop</button>` +
      `</div>` +
      `<button class="wc-frame" type="button" hidden title="Click to enlarge"><img alt="What the web agent sees"><span></span></button>` +
      `<ol class="wc-steps"></ol>` +
      `<div class="wc-ask" hidden><p></p><div class="wc-ask-btns">` +
        `<button class="mini-btn yes" type="button" data-answer="yes" data-for="confirm">Yes</button>` +
        `<button class="mini-btn" type="button" data-answer="no" data-for="confirm">No</button>` +
        `<button class="mini-btn yes" type="button" data-answer="done" data-for="handover">Done</button></div>` +
        `<small>…or just say it</small></div>` +
      `<p class="wc-summary" hidden></p>`;
    card.querySelector(".wc-title small").textContent = d.goal || "";
    card.querySelector("[data-stop]").onclick = () => onWeb?.({ t: "web_stop" });
    card.querySelector(".wc-frame").onclick = (e) => e.currentTarget.classList.toggle("big");
    for (const b of card.querySelectorAll("[data-answer]")) {
      b.onclick = () => {
        onWeb?.({ t: "web_reply", answer: b.dataset.answer });
        card.querySelector(".wc-ask").hidden = true;
      };
    }
    webCards.set(d.task, card);
    put(card);
    return card;
  }

  function webState(card, state) {
    card.dataset.state = state;
    const box = card.querySelector(".wc-state");
    const busy = state === "working";
    box.innerHTML = busy ? `<span class="spin"></span><span></span>`
      : state === "done" ? `${icon("check", "ic xs")}<span></span>`
      : state === "waiting" ? `${icon("alert", "ic xs")}<span></span>`
      : `${icon("x", "ic xs")}<span></span>`;
    box.querySelector("span:last-child").textContent = STATE_TEXT[state] ?? state;
    card.querySelector("[data-stop]").hidden = !(busy || state === "waiting");
  }

  function webStep(card, text, note = false) {
    const list = card.querySelector(".wc-steps");
    const li = document.createElement("li");
    li.textContent = text;
    if (note) li.className = "note";
    list.append(li);
    // Keep it short: the last six steps, with a count of the rest.
    const items = [...list.querySelectorAll("li:not(.more)")];
    const hidden = items.length - 6;
    items.forEach((el, i) => { el.hidden = i < hidden; });
    let more = list.querySelector(".more");
    if (hidden > 0) {
      if (!more) { more = document.createElement("li"); more.className = "more"; list.prepend(more); }
      more.textContent = `+${hidden} earlier step${hidden === 1 ? "" : "s"}`;
    }
  }

  function web(msg) {
    const d = msg.data || {};
    if (d.task == null) return;
    const card = webCard(d);
    const stick = atBottom();
    switch (msg.kind) {
      case "web_start": webState(card, "working"); break;
      case "web_step":
        webStep(card, d.text + (d.kind !== "note" && d.result && /^(error|refused|no such)/i.test(d.result) ? ` — ${d.result}` : ""), d.kind === "note");
        webState(card, d.state || "working");
        break;
      case "web_frame": {
        const f = card.querySelector(".wc-frame");
        f.hidden = false;
        f.querySelector("img").src = `data:image/jpeg;base64,${d.jpeg}`;
        f.querySelector("span").textContent = d.title ? `${d.title} · ${host(d.url || "")}` : host(d.url || "");
        break;
      }
      case "web_question": {
        const ask = card.querySelector(".wc-ask");
        ask.hidden = false;
        ask.querySelector("p").textContent = d.question;
        // Yes/No for a confirmation; "Done" when you have to act in the
        // browser window yourself (log in, solve a CAPTCHA, enter an OTP).
        const handover = d.kind === "login" || d.kind === "action";
        ask.querySelector(".wc-ask-btns").hidden = !(d.kind === "confirm" || handover);
        for (const b of ask.querySelectorAll("[data-for]"))
          b.hidden = b.dataset.for !== (handover ? "handover" : "confirm");
        ask.querySelector("small").textContent = handover
          ? "Do it in the Chrome window, then press Done or just say “done”"
          : d.kind === "confirm" ? "…or just say yes or no" : "Say or type your answer";
        webState(card, "waiting");
        break;
      }
      case "web_done": {
        card.querySelector(".wc-ask").hidden = true;
        const sum = card.querySelector(".wc-summary");
        sum.hidden = !d.summary;
        sum.textContent = d.summary || "";
        webState(card, d.state || "done");
        break;
      }
    }
    settle(stick);
  }

  // ---------- public ----------

  return {
    /** A spoken transcript piece: one bubble per turn, however it arrives. */
    heard(text, turnId) {
      if (userMsg && userTurn === turnId) {
        userMsg.bubble.textContent += text;
      } else {
        userMsg = userRow(text.trimStart(), true);
        userTurn = turnId;
        live = null;
      }
      return userMsg.bubble.textContent;
    },

    /** A typed message. */
    typed(text) {
      userMsg = null;
      live = null;
      userRow(text, false);
    },

    /** Streaming reply text. Returns the reply so far. */
    reply(delta) {
      if (!live) live = assistantRow();
      live.raw += delta;
      const stick = atBottom();
      live.bubble.innerHTML = renderRich(live.raw);
      settle(stick);
      return live.raw;
    },

    /** Next reply text starts a new message (after a wake, tool, event...). */
    newThought() { live = null; },
    /** The next transcript piece starts a new user message. */
    newTurn() { userMsg = null; live = null; },

    thinking(on) { if (on) startTyping(); else stopTyping(); },

    /** The reply was cut off (barge-in, stop). */
    interrupted() {
      if (!live) return;
      live.classList.add("cut");
      live.querySelector(".meta").insertAdjacentHTML("beforeend", `<span class="cut-tag">interrupted</span>`);
      live = null;
    },

    toolCall, toolResult, web,

    event(msg) {
      stopTyping();
      live = null;
      const el = document.createElement("div");
      el.className = "notice";
      if (msg.kind === "timer") {
        timerRang(msg.data);
        el.dataset.kind = "timer";
        el.innerHTML = `${icon("timer")}<span><b>Timer done</b>${msg.data?.label ? ` · ${esc(msg.data.label)}` : ""}</span><time>${hhmm()}</time>`;
      } else if (msg.kind === "diag_alert") {
        el.dataset.kind = "diag";
        el.dataset.level = msg.data?.level || "warn";
        el.innerHTML = `${icon("alert")}<span><b>System</b> · ${esc(msg.text || msg.data?.facts || "")}</span><time>${hhmm()}</time>`;
      } else if (msg.kind === "motion") {
        el.dataset.kind = "motion";
        el.innerHTML = `${icon("eye")}<span><b>Motion detected</b></span><time>${hhmm()}</time>`;
      } else {
        el.innerHTML = `${icon("tool")}<span><b>${esc(msg.kind)}</b></span><time>${hhmm()}</time>`;
      }
      put(el);
    },

    /** Plain-language error; `retry` is the text to send again, if any. */
    error(text, retry = null) {
      stopTyping();
      const el = document.createElement("div");
      el.className = "notice error";
      el.innerHTML = `${icon("alert")}<span>${esc(text)}</span>` +
        (retry ? `<button class="mini-btn" type="button">Retry</button>` : "");
      if (retry) el.querySelector("button").onclick = () => { el.remove(); onAsk?.(retry); };
      put(el, { force: true });
    },

    clear() {
      for (const t of tools) clearInterval(t.el.tick);
      tools.length = 0;
      stopTyping();
      convo.replaceChildren();
      live = null;
      userMsg = null;
      jump.hidden = true;
      webCards.clear();
    },
  };
}
