"""One real Chrome window the web agent drives, through Playwright.

Headed on purpose: WebMCP does not work headless, sites treat visible
browsers better, and you can watch -- and take over to type a password.
It uses its own profile (data/browser-profile), never your everyday Chrome,
so logins you make there stay there.

Every action returns a short sentence for the agent; nothing here raises for
ordinary failures (element gone, page slow), because a web page misbehaving
is normal and the agent should just try something else.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from urllib.parse import quote_plus

from ...config import Settings
from . import webmcp
from .snapshot import SNAPSHOT_JS, format_snapshot

log = logging.getLogger("jarvis.browser")

ROOT = Path(__file__).resolve().parents[3]
SEARCH_URL = "https://duckduckgo.com/?q={}"

# Keep everything in one tab: links that would open a new tab (Flipkart's
# product cards do) open in place instead, so a follow-up like "add it to the
# cart" finds the product right where the agent left it.
SINGLE_TAB_JS = """
(() => {
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[target]");
    if (a && a.target !== "_self") a.target = "_self";
  }, true);
  const open = window.open;
  window.open = function (url, name, features) {
    if (url) { location.assign(url); return window; }
    return open.apply(this, arguments);
  };
})();
"""

# Resolves once the page has stopped changing for a moment (or gives up):
# search results and product grids arrive well after "DOMContentLoaded".
QUIET_JS = """([quietMs, maxMs]) => new Promise((resolve) => {
  let last = performance.now();
  const obs = new MutationObserver(() => { last = performance.now(); });
  obs.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
  const start = performance.now();
  // A spinner or "Loading…" means results are still coming, however quiet
  // the page looks for a moment.
  const busy = () => {
    for (const el of document.querySelectorAll('[aria-busy=true], [class*=spinner], [class*=loader], [class*=loading], [role=progressbar]')) {
      const r = el.getBoundingClientRect();
      if (r.width > 4 && r.height > 4 && getComputedStyle(el).visibility !== 'hidden') return true;
    }
    return /\\bloading\\s*(\\.\\.\\.|…)/i.test(document.body?.innerText.slice(0, 20000) || '');
  };
  (function check() {
    const now = performance.now();
    if (now - start >= maxMs || (now - last >= quietMs && !busy())) { obs.disconnect(); resolve(Math.round(now - start)); }
    else setTimeout(check, 150);
  })();
})"""


def playwright_missing() -> str | None:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return "playwright is not installed (pip install playwright)"
    return None


class BrowserSession:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._pw = None
        self._ctx = None
        self.page = None
        self._lock = asyncio.Lock()
        self.last_used = 0.0
        self._last_frame = 0.0
        # Where the agent was, so an unexpected close (a crash, or the window
        # closed by hand) reopens on the same page instead of a blank one --
        # otherwise a follow-up like "open the first one" has nothing to act on.
        self._last_url = ""
        self._restore_url = ""
        self._closing = False
        self.recovered = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def open(self) -> bool:
        return self._ctx is not None

    async def ensure(self):
        async with self._lock:
            if self._ctx is not None:
                return self.page
            from playwright.async_api import async_playwright

            profile = Path(self.settings.browser_profile_dir)
            if not profile.is_absolute():
                profile = ROOT / profile
            profile.mkdir(parents=True, exist_ok=True)
            self._pw = await async_playwright().start()
            kwargs = dict(
                headless=False,
                no_viewport=True,
                args=["--enable-features=WebMCPTesting", "--start-maximized"],
            )
            if self.settings.browser_channel and self.settings.browser_channel != "chromium":
                kwargs["channel"] = self.settings.browser_channel
            self._ctx = await self._pw.chromium.launch_persistent_context(str(profile), **kwargs)
            self._ctx.set_default_timeout(8000)
            self._ctx.set_default_navigation_timeout(self.settings.browser_nav_timeout_s * 1000)
            if self.settings.browser_single_tab:
                await self._ctx.add_init_script(SINGLE_TAB_JS)
            self._ctx.on("page", self._adopt)
            self._ctx.on("close", lambda *_: self._forget())
            self.page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
            log.info("browser started (%s, profile %s)", self.settings.browser_channel, profile)
            if self._restore_url:
                url, self._restore_url = self._restore_url, ""
                try:
                    await self.page.goto(url, wait_until="domcontentloaded")
                    self.recovered = True
                    log.info("browser reopened on %s", url[:100])
                except Exception as exc:
                    log.warning("could not reopen %s: %s", url[:100], exc)
            return self.page

    def _adopt(self, page) -> None:
        """A tab opened anyway (the script above can't catch every kind)."""
        if not self.settings.browser_single_tab or self.page is None or self.page.is_closed():
            self.page = page  # follow it, like a person would
            return
        asyncio.get_running_loop().create_task(self._fold_into_current(page))

    async def _fold_into_current(self, popup) -> None:
        # Open what it was going to show in the current tab, then close it.
        try:
            await popup.wait_for_load_state("commit", timeout=8000)
        except Exception:
            pass
        url = popup.url
        try:
            await popup.close()
        except Exception:
            pass
        if url and url != "about:blank" and self.page is not None and not self.page.is_closed():
            log.info("new tab folded into the current one: %s", url[:100])
            try:
                await self.page.goto(url, wait_until="domcontentloaded")
            except Exception:
                pass

    def _forget(self) -> None:
        if not self._closing:
            log.warning("browser closed unexpectedly (was on %s); it will reopen there on next use",
                        self._last_url[:100] or "nothing")
            self._restore_url = self._last_url
        self._ctx = None
        self.page = None

    async def close(self) -> None:
        async with self._lock:
            self._closing = True
            if self._ctx is not None:
                try:
                    await self._ctx.close()
                except Exception:
                    pass
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
            self._ctx = self._pw = self.page = None
            self._closing = False

    async def _page(self):
        page = await self.ensure()
        if page.is_closed():
            live = [p for p in self._ctx.pages if not p.is_closed()]
            self.page = live[-1] if live else await self._ctx.new_page()
        self.last_used = time.monotonic()
        if self.page.url and self.page.url != "about:blank":
            self._last_url = self.page.url
        return self.page

    async def _settle(self, page, max_ms: int = 5000) -> None:
        """Wait until the page has actually reacted: loaded, and then quiet
        for 700 ms (search results render after load on most shops)."""
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=6000)
        except Exception:
            pass
        await page.wait_for_timeout(300)
        try:
            await page.evaluate(QUIET_JS, [700, max_ms])
        except Exception:
            # The page navigated while we watched it; wait for the new one.
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=6000)
                await page.evaluate(QUIET_JS, [700, max_ms])
            except Exception:
                pass

    async def bring_to_front(self) -> None:
        if self.page is not None and not self.page.is_closed():
            try:
                await self.page.bring_to_front()
            except Exception:
                pass

    async def wait(self, seconds: float) -> str:
        page = await self._page()
        await page.wait_for_timeout(int(max(0.5, min(seconds, 8)) * 1000))
        await self._settle(page, 3000)
        return f"waited; now on {page.url}"

    # -- observing ---------------------------------------------------------

    async def observe(self, scope: str = "visible") -> tuple[str, list[dict]]:
        """(snapshot text, WebMCP tools) for the current page."""
        page = await self._page()
        try:
            snap = await page.evaluate(SNAPSHOT_JS, {"scope": scope, "maxItems": 90,
                                                     "maxText": 1400 if scope == "visible" else 2600})
        except Exception as exc:
            await self._settle(page)
            try:
                snap = await page.evaluate(SNAPSHOT_JS, {"scope": scope})
            except Exception:
                return (f"URL: {page.url}\n(could not read the page: {exc})")[:300], []
        tools = await webmcp.list_tools(page)
        if page.url and page.url != "about:blank":
            self._last_url = page.url
        text = format_snapshot(snap, max_chars=2600 if scope == "visible" else 3400)
        if self.recovered:
            self.recovered = False
            text = ("NOTE: the browser window had closed; it was reopened on the last page. "
                    "Anything not saved by the site (like an unsaved form) is gone.\n" + text)
        return text, tools

    async def frame(self, force: bool = False) -> dict | None:
        """A small JPEG of the viewport for the UI, at most one a second."""
        if not self.open or self.page is None:
            return None
        now = time.monotonic()
        if not force and now - self._last_frame < 1.0:
            return None
        self._last_frame = now
        try:
            jpg = await self.page.screenshot(type="jpeg", quality=45, scale="css", timeout=4000)
            title = await self.page.title()
        except Exception:
            return None
        return {"jpeg": base64.b64encode(jpg).decode("ascii"), "url": self.page.url, "title": title}

    async def screenshot(self) -> bytes:
        page = await self._page()
        return await page.screenshot(type="jpeg", quality=70, scale="css")

    async def element_info(self, ref: int) -> dict | None:
        """What an element is, for safety checks before acting on it."""
        page = await self._page()
        return await page.evaluate(
            """(ref) => {
              const el = document.querySelector(`[data-jarvis-ref="${ref}"]`);
              if (!el) return null;
              const form = el.form || el.closest("form");
              const submit = form?.querySelector("[type=submit], button:not([type=button])");
              return {
                tag: el.tagName.toLowerCase(), type: el.getAttribute("type") || "",
                name: el.getAttribute("name") || "", id: el.id || "",
                autocomplete: el.getAttribute("autocomplete") || "",
                placeholder: el.getAttribute("placeholder") || "",
                aria: el.getAttribute("aria-label") || "",
                text: (el.innerText || el.value || el.getAttribute("aria-label") || "").trim().slice(0, 120),
                formSubmit: (submit?.innerText || submit?.value || "").trim().slice(0, 80),
                hasPasswordField: !!form?.querySelector("input[type=password]"),
              };
            }""",
            ref,
        )

    # -- acting ------------------------------------------------------------

    def _loc(self, page, ref: int):
        return page.locator(f'[data-jarvis-ref="{int(ref)}"]').first

    async def open_url(self, target: str) -> str:
        page = await self._page()
        target = target.strip()
        looks_like_url = ("." in target or target.startswith(("http:", "https:", "localhost"))) and " " not in target
        if target.startswith(("http://", "https://")) or not looks_like_url:
            urls = [target if looks_like_url else SEARCH_URL.format(quote_plus(target))]
        elif target.startswith(("localhost", "127.", "192.168.", "10.")):
            urls = [f"http://{target}"]  # local servers rarely speak https
        else:
            urls = [f"https://{target}", f"http://{target}"]
        err = ""
        for url in urls:
            try:
                await page.goto(url, wait_until="domcontentloaded")
                break
            except Exception as exc:
                err = str(exc).splitlines()[0][:160]
        else:
            return f"error: could not open {target}: {err}"
        await self._settle(page)
        return f"opened {page.url}"

    async def click(self, ref: int | None = None, text: str | None = None) -> str:
        page = await self._page()
        try:
            if ref is not None:
                loc = self._loc(page, ref)
                if await loc.count() == 0:
                    return f"no element [{ref}] -- read the page again, refs change after navigation"
            elif text:
                loc = page.get_by_role("button", name=text).or_(page.get_by_role("link", name=text)).or_(
                    page.get_by_text(text, exact=False)).first
            else:
                return "error: give a ref or text"
            await loc.scroll_into_view_if_needed(timeout=4000)
            await loc.click(timeout=6000)
        except Exception as exc:
            return f"error: click failed: {str(exc).splitlines()[0][:160]}"
        await self._settle(page)
        return f"clicked; now on {page.url}"

    async def type_text(self, ref: int, text: str, submit: bool = False) -> str:
        page = await self._page()
        loc = self._loc(page, ref)
        before = page.url
        try:
            if await loc.count() == 0:
                return f"error: no element [{ref}]"
            await loc.scroll_into_view_if_needed(timeout=4000)
            try:
                await loc.fill(text, timeout=4000)
            except Exception:
                # Not a real field (Flipkart labels a wrapper as the search
                # box): click it, then type into what took focus, or the
                # input hiding inside it.
                await loc.click(timeout=4000)
                inner = loc.locator("input, textarea, [contenteditable=true]").first
                if await inner.count():
                    await inner.fill(text, timeout=4000)
                else:
                    focused = await page.evaluate(
                        "() => { const a = document.activeElement; return !!a && "
                        "(a.matches('input,textarea') || a.isContentEditable); }")
                    if not focused:
                        return (f"error: [{ref}] is not a text field and clicking it focused nothing; "
                                "look for the real input in the new page listing")
                    await page.keyboard.press("Control+A")
                    await page.keyboard.type(text, delay=20)
            if submit:
                await page.keyboard.press("Enter")
                await self._settle(page)
        except Exception as exc:
            return f"error: typing failed: {str(exc).splitlines()[0][:160]}"
        if submit:
            moved = ("the page changed" if page.url != before
                     else "the address did not change -- check the page really shows results")
            return f"typed {text!r} and pressed Enter; {moved}; now on {page.url}"
        return f"typed {text!r} into [{ref}]"

    async def select(self, ref: int, option: str) -> str:
        page = await self._page()
        loc = self._loc(page, ref)
        try:
            try:
                await loc.select_option(label=option, timeout=4000)
            except Exception:
                await loc.select_option(value=option, timeout=4000)
        except Exception as exc:
            return f"select failed: {str(exc).splitlines()[0][:160]}"
        return f"selected {option!r} in [{ref}]"

    async def scroll(self, direction: str = "down") -> str:
        page = await self._page()
        js = {
            "down": "scrollBy(0, innerHeight * 0.8)", "up": "scrollBy(0, -innerHeight * 0.8)",
            "top": "scrollTo(0, 0)", "bottom": "scrollTo(0, document.documentElement.scrollHeight)",
        }.get(direction, "scrollBy(0, innerHeight * 0.8)")
        await page.evaluate(js)
        await page.wait_for_timeout(400)
        return f"scrolled {direction}"

    async def nav(self, action: str) -> str:
        page = await self._page()
        try:
            if action == "back":
                await page.go_back(wait_until="domcontentloaded")
            elif action == "forward":
                await page.go_forward(wait_until="domcontentloaded")
            else:
                await page.reload(wait_until="domcontentloaded")
        except Exception as exc:
            return f"{action} failed: {str(exc).splitlines()[0][:160]}"
        await self._settle(page)
        return f"went {action}; now on {page.url}"

    async def tabs(self, action: str = "list", index: int | None = None) -> str:
        await self._page()
        pages = [p for p in self._ctx.pages if not p.is_closed()]
        if action == "new":
            self.page = await self._ctx.new_page()
            return f"opened a new tab ({len(pages) + 1} tabs)"
        if action in ("switch", "close"):
            if index is None or not 1 <= index <= len(pages):
                return f"error: index must be 1..{len(pages)}"
            target = pages[index - 1]
            if action == "close":
                await target.close()
                live = [p for p in self._ctx.pages if not p.is_closed()]
                self.page = live[-1] if live else await self._ctx.new_page()
                return f"closed tab {index}"
            self.page = target
            await target.bring_to_front()
            return f"switched to tab {index}: {await target.title()}"
        out = []
        for i, p in enumerate(pages, 1):
            mark = " (current)" if p is self.page else ""
            out.append(f"{i}. {(await p.title())[:60]} -- {p.url[:80]}{mark}")
        return "tabs:\n" + "\n".join(out)

    async def site_tool(self, name: str, args: dict) -> tuple[bool, str]:
        page = await self._page()
        ok, text = await webmcp.call_tool(page, name, args)
        await self._settle(page)
        return ok, text
