// Injected by snapshot.py. Returns what the agent needs to act on a page:
// every visible interactive element with a short numbered ref, plus the
// headings and a trimmed slice of text. Refs are written onto the elements
// (data-jarvis-ref) so "click 7" finds the exact element again.
//
// Called as page.evaluate(SNAPSHOT_JS, { scope, maxItems, maxText }).
({ scope = "visible", maxItems = 70, maxText = 1400 }) => {
  const INTERACTIVE = [
    "a[href]", "button", "input:not([type=hidden])", "textarea", "select", "summary",
    "[role=button]", "[role=link]", "[role=tab]", "[role=menuitem]", "[role=checkbox]",
    "[role=radio]", "[role=switch]", "[role=combobox]", "[role=searchbox]", "[role=textbox]",
    "[role=option]", "[contenteditable=true]", "[onclick]",
  ].join(",");

  const vw = innerWidth, vh = innerHeight;
  const clean = (s, n = 80) => String(s ?? "").replace(/\s+/g, " ").trim().slice(0, n);

  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none" || Number(cs.opacity) === 0) return false;
    if (scope === "visible" && (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw)) return false;
    return true;
  }

  function kind(el) {
    const role = el.getAttribute("role");
    if (role) return role;
    const tag = el.tagName.toLowerCase();
    if (tag === "a") return "link";
    if (tag === "input") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      if (["submit", "button", "reset", "image"].includes(t)) return "button";
      if (t === "search") return "searchbox";
      return t === "text" ? "textbox" : t;
    }
    if (tag === "textarea") return "textbox";
    if (tag === "select") return "select";
    if (el.isContentEditable) return "textbox";
    return tag;
  }

  const GENERIC = /^(image|img|picture|photo|icon|logo|thumbnail)?$/i;
  const wordy = (s) => (String(s || "").match(/[a-z]/gi) || []).length >= 4;

  function label(el) {
    const aria = el.getAttribute("aria-label") || el.getAttribute("aria-labelledby") &&
      document.getElementById(el.getAttribute("aria-labelledby"))?.innerText;
    if (aria) return clean(aria);
    if (el.labels?.length) return clean(el.labels[0].innerText);
    const text = ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) ? "" : clean(el.innerText || el.value);
    const title = el.getAttribute("title");
    const alt = [...(el.querySelectorAll?.("img[alt]") || [])].map((i) => i.alt).find((a) => !GENERIC.test(a.trim()));
    if (title && wordy(title)) return clean(title, 100) + (text && !title.includes(text.slice(0, 20)) && !wordy(text) ? ` — ${text}` : "");
    if (text && wordy(text)) return text;
    if (alt) return clean(alt, 100) + (text ? ` — ${text}` : "");
    // A price-only or image-only link: name it after the product card it sits in.
    if (el.tagName === "A") {
      let n = el.parentElement;
      for (let i = 0; n && i < 5; i++, n = n.parentElement) {
        const named = n.querySelector("[title]")?.getAttribute("title") ||
          [...n.querySelectorAll("img[alt]")].map((x) => x.alt).find((a) => wordy(a) && !GENERIC.test(a.trim()));
        if (named && wordy(named)) return clean(named, 70) + (text ? ` — ${text}` : "");
      }
    }
    return clean(text || el.getAttribute("placeholder") || title || el.getAttribute("alt") ||
      el.getAttribute("name") || "");
  }

  // A popup in the way (login box, cookie banner, dialog): the page behind it
  // can't be clicked until it is dealt with, so the agent must know.
  function blocker() {
    for (const c of document.querySelectorAll("[role=dialog],[aria-modal=true],dialog[open]")) {
      if (visible(c)) return c;
    }
    let n = document.elementFromPoint(vw / 2, vh / 2);
    for (; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.position === "fixed" && (parseInt(cs.zIndex) || 0) >= 1) {
        const r = n.getBoundingClientRect();
        if (r.width * r.height > vw * vh * 0.08) return n;
      }
    }
    return null;
  }
  const popupEl = blocker();

  let next = 1;
  for (const el of document.querySelectorAll("[data-jarvis-ref]")) next = Math.max(next, +el.dataset.jarvisRef + 1);

  const items = [];
  const seen = new Set();
  for (const el of document.querySelectorAll(INTERACTIVE)) {
    if (items.length >= maxItems) break;
    if (!visible(el) || el.closest("[aria-hidden=true]") || el.disabled) continue;
    // A link wrapping a button (or the other way round) is one thing to click.
    const outer = el.parentElement?.closest(INTERACTIVE);
    if (outer && seen.has(outer)) continue;
    seen.add(el);
    if (!el.dataset.jarvisRef) el.dataset.jarvisRef = String(next++);
    const k = kind(el);
    const item = { ref: +el.dataset.jarvisRef, kind: k, label: label(el) };
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
      const t = (el.getAttribute("type") || "").toLowerCase();
      item.value = t === "password" ? (el.value ? "••••" : "") : clean(el.value, 40);
      if (el.type === "checkbox" || el.type === "radio") item.checked = el.checked;
    }
    if (el.tagName === "SELECT") {
      item.value = clean(el.selectedOptions[0]?.text, 40);
      item.options = [...el.options].slice(0, 12).map((o) => clean(o.text, 30));
    }
    if (k === "link") item.href = el.getAttribute("href")?.slice(0, 80);
    if (popupEl && popupEl.contains(el)) item.popup = true;
    items.push(item);
  }
  // Second pass: things that act like buttons without saying so. Many shops
  // (Flipkart's "Buy now" / "Add to cart") render plain <div>s with a click
  // handler; the only tell is the pointer cursor and a short label.
  const ACTION = /\b(add to (cart|bag|basket|wishlist)|buy( now)?|go to cart|view cart|checkout|continue|next|place order|sign in|log ?in|accept|close|apply|search|play|subscribe)\b/i;
  const pointerish = [];
  let checked = 0;
  for (const el of document.querySelectorAll("div, span, li, label")) {
    if (checked > 6000) break;
    const raw = el.textContent;
    if (!raw || raw.length > 60) continue;
    checked++;
    if (seen.has(el) || el.closest(INTERACTIVE) || el.querySelector(INTERACTIVE)) continue;
    if (getComputedStyle(el).cursor !== "pointer") continue;
    // Only the outermost of nested clickable boxes: an offer card is one
    // thing to click, not its price, its label and its "Apply" separately.
    const p = el.parentElement;
    if (p && (p.textContent || "").length <= 60 && getComputedStyle(p).cursor === "pointer") continue;
    const t = clean(el.innerText, 40);
    if (t.length < 2 || !visible(el)) continue;
    pointerish.push([el, t]);
  }
  // Last resort: an element whose whole text is a clear action ("Buy now",
  // "Add to cart") is listed even with no sign of being clickable --
  // Flipkart's buttons have no role, no pointer cursor and no tabindex.
  const EXACT = new RegExp(`^(${ACTION.source.slice(2, -2)})$`, "i");
  for (const el of document.querySelectorAll("div, span")) {
    if (el.childElementCount > 2) continue;
    const t = clean(el.textContent, 30);
    if (!t || !EXACT.test(t) || seen.has(el) || el.closest(INTERACTIVE)) continue;
    // The outermost box that still says only this.
    let box = el;
    while (box.parentElement && clean(box.parentElement.textContent, 31) === t) box = box.parentElement;
    if (pointerish.some(([x]) => x === box || x.contains(box) || box.contains(x)) || !visible(box)) continue;
    pointerish.push([box, t]);
  }
  // Real actions (Buy now, Add to cart, Continue) before offer chips.
  pointerish.sort((a, b) => (ACTION.test(b[1]) ? 1 : 0) - (ACTION.test(a[1]) ? 1 : 0));
  for (const [el, t] of pointerish) {
    if (items.length >= maxItems + 15) break;
    seen.add(el);
    if (!el.dataset.jarvisRef) el.dataset.jarvisRef = String(next++);
    const item = { ref: +el.dataset.jarvisRef, kind: "button", label: t };
    if (popupEl && popupEl.contains(el)) item.popup = true;
    items.push(item);
  }

  // Popup controls first: they are what can actually be clicked right now.
  if (popupEl) items.sort((a, b) => (b.popup ? 1 : 0) - (a.popup ? 1 : 0));

  const headings = [...document.querySelectorAll("h1,h2,h3")]
    .filter(visible).slice(0, 12).map((h) => `${h.tagName.toLowerCase()}: ${clean(h.innerText, 90)}`);

  // Text: what is on screen now, or the main content for a full read.
  let text = "";
  const main = document.querySelector("main, article, [role=main]") || document.body;
  if (scope === "page") {
    text = clean(main.innerText, maxText);
  } else {
    const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
    const parts = [];
    let total = 0;
    while (walker.nextNode() && total < maxText) {
      const n = walker.currentNode, s = clean(n.textContent, 300);
      if (s.length < 2 || !n.parentElement || !visible(n.parentElement)) continue;
      if (n.parentElement.closest("script,style,noscript")) continue;
      parts.push(s);
      total += s.length + 1;
    }
    text = parts.join(" ").slice(0, maxText);
  }

  const doc = document.documentElement;
  const scrollPct = Math.round((scrollY / Math.max(1, doc.scrollHeight - vh)) * 100);
  return {
    url: location.href, title: document.title, items, headings, text,
    popup: popupEl ? clean(popupEl.innerText, 160) : "",
    // Does the page want the user to sign in (or prove who they are)? Only
    // they can do that: the agent hands the window over and waits.
    login: !!(
      document.querySelector("input[type=password], input[autocomplete=one-time-code]") ||
      /(^|\/)(login|signin|sign-in|account\/login|ap\/signin)(\/|$|\?)/i.test(location.pathname) ||
      (popupEl && /\b(log ?in|sign ?in|phone number|mobile number|otp|verify)\b/i.test(popupEl.innerText))
    ),
    captcha: !!document.querySelector("iframe[src*=captcha], iframe[src*=recaptcha], iframe[title*=challenge i], #captcha, .g-recaptcha"),
    scroll: doc.scrollHeight <= vh + 4 ? "fits on screen" : `${Math.max(0, Math.min(100, scrollPct))}% down`,
  };
}
