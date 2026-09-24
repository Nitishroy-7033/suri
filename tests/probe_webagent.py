"""Web agent probe: real Chrome, real agent loop, scripted model.

    .venv\\Scripts\\python.exe tests\\probe_webagent.py          # scripted model
    .venv\\Scripts\\python.exe tests\\probe_webagent.py --live   # + the real model

Serves tests/fixtures over http.server, drives WebAgent against form.html and
webmcp.html, and checks that it acts by ref, refuses the password field,
asks before "Place order", finds the page's WebMCP tools, runs a site tool,
and asks before the consequential one. Needs Chrome 149+ for the WebMCP part
(skipped if the page has no document.modelContext).

A Chrome window opens while it runs; that is expected (WebMCP needs headed).
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import Settings  # noqa: E402
from backend.core.events import EventBus  # noqa: E402
from backend.domain.webagent import agent as agent_mod  # noqa: E402
from backend.domain.webagent import webmcp  # noqa: E402
from backend.domain.webagent.agent import WebAgent  # noqa: E402
from backend.domain.webagent.llm import Action  # noqa: E402

PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail else ''}")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


class ScriptedLlm:
    """Plays back actions; each entry may be a callable of the prompt."""

    def __init__(self, script):
        self.script = list(script)
        self.prompts: list[str] = []
        self.functions: list[list[str]] = []

    async def next_action(self, system, prompt, functions):
        self.prompts.append(prompt)
        self.functions.append([f["name"] for f in functions])
        step = self.script.pop(0) if self.script else ("finish", {"summary": "script ended"})
        if callable(step):
            step = step(prompt)
        return Action(*step)


class FakeRuntime:
    def __init__(self, settings):
        self.settings = settings
        self.events = EventBus()
        self.jobs = set()

    def spawn(self, coro):
        t = asyncio.create_task(coro)
        self.jobs.add(t)
        t.add_done_callback(self.jobs.discard)
        return t


def ref_of(prompt: str, label: str) -> int:
    for line in prompt.splitlines():
        if line.startswith("[") and f'"{label}' in line:
            return int(line[1:line.index("]")])
    raise AssertionError(f"no element labelled {label!r} in:\n{prompt[:800]}")


async def run_task(agent: WebAgent, runtime, goal: str, answers: dict[str, str]):
    """Start a task and answer its questions; returns (events, final task)."""
    q = runtime.events.subscribe()
    first = await agent.submit(goal)
    events = []
    while True:
        ev = await asyncio.wait_for(q.get(), 60)
        events.append(ev)
        if ev.kind == "web_question":
            text = ev.data["question"]
            reply = next((a for k, a in answers.items() if k.lower() in text.lower()), "no")
            await agent.reply(reply)
        if ev.kind == "web_done":
            break
    runtime.events.unsubscribe(q)
    return first, events, agent.task


async def main(live: bool) -> int:
    srv = http.server.ThreadingHTTPServer(
        ("127.0.0.1", PORT), functools.partial(Quiet, directory=str(ROOT / "tests" / "fixtures")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings = Settings(web_agent_enabled=True, browser_profile_dir=tempfile.mkdtemp(prefix="jarvis-probe-"),
                        web_agent_sync_wait_s=0.2)
    runtime = FakeRuntime(settings)
    agent = WebAgent(runtime)

    try:
        # -- 1. acting by ref, secrets, risky confirmation ----------------------
        print("form.html")
        llm = ScriptedLlm([
            ("open", {"target": f"{BASE}/form.html"}),
            lambda p: ("type", {"ref": ref_of(p, "Name"), "text": "Nitish"}),
            lambda p: ("type", {"ref": ref_of(p, "Password"), "text": "hunter2"}),
            lambda p: ("select", {"ref": ref_of(p, "Country"), "option": "India"}),
            lambda p: ("click", {"ref": ref_of(p, "Place order")}),   # user says no
            lambda p: ("click", {"ref": ref_of(p, "Place order")}),   # user says yes
            ("finish", {"summary": "Filled the form and placed the order."}),
        ])
        agent._llm = llm
        answers = iter(["no", "yes"])
        q = runtime.events.subscribe()
        await agent.submit("fill the checkout form")
        asked = []
        while True:
            ev = await asyncio.wait_for(q.get(), 60)
            if ev.kind == "web_question":
                asked.append(ev.data["question"])
                await agent.reply(next(answers))
            if ev.kind == "web_done":
                break
        runtime.events.unsubscribe(q)
        steps = agent.task.steps
        page = agent.browser.page
        check("snapshot lists elements with refs", any('textbox "Name' in p for p in llm.prompts))
        check("typed into the name field by ref", await page.input_value("input[name=name]") == "Nitish")
        check("refused the password field", await page.input_value("input[type=password]") == ""
              and any("refused" in s["result"] for s in steps))
        check("selected from the dropdown", await page.eval_on_selector("select", "s => s.value") == "India")
        check("asked before 'Place order' (twice)", len(asked) == 2 and "Place order" in asked[0], str(asked))
        check("'no' was respected, 'yes' went through",
              "user said no" in steps[4]["result"] and await page.inner_text("#out") == "ORDER PLACED")
        check("page text is marked untrusted", "<untrusted_page_text>" in llm.prompts[1])

        # -- 2. WebMCP ----------------------------------------------------------
        print("webmcp.html")
        llm = ScriptedLlm([
            ("open", {"target": f"{BASE}/webmcp.html"}),
            ("site__search_products", {"q": "usb"}),
            ("site__add_to_cart", {"product": "USB-C hub"}),
            ("finish", {"summary": "Added the USB-C hub to the cart."}),
        ])
        agent._llm = llm
        _, events, task = await run_task(agent, runtime, "add a usb hub to the cart", {"add to cart": "yes"})
        has_mc = await agent.browser.page.evaluate("() => !!document.modelContext")
        if not has_mc:
            print("  SKIP  this Chrome has no document.modelContext (needs Chrome 149+ with WebMCP)")
        else:
            fns = llm.functions[1]
            check("site tools offered as functions", "site__search_products" in fns and "site__add_to_cart" in fns, str(fns))
            check("declarative form tool ran", "Found: USB-C hub" in task.steps[1]["result"], task.steps[1]["result"])
            check("asked before the consequential tool", any(e.kind == "web_question" for e in events))
            check("imperative tool ran after yes",
                  "USB-C hub" in await agent.browser.page.inner_text("#cart"))
            check("site results are marked untrusted", "<untrusted_site_result>" in task.steps[2]["result"])
            ch = await webmcp._channel(agent.browser.page)
            check("tools come over DevTools WebMCP", ch is not None)
            await agent.browser.page.reload()
            await agent.browser.page.wait_for_timeout(500)
            names = [t["name"] for t in await webmcp.list_tools(agent.browser.page)]
            check("reload does not duplicate tools", sorted(names) == ["add_to_cart", "search_products"], str(names))

        # -- 3. a shop like Flipkart: late results, one tab, login hand-over ------
        print("shop.html (search, then a follow-up task with a login)")
        seen: dict = {}

        def remember(key):
            def f(p):
                seen[key] = p
                return None
            return f

        def step(key, action):
            def f(p):
                seen[key] = p
                return action(p)
            return f

        llm = ScriptedLlm([
            ("open", {"target": f"{BASE}/shop.html"}),
            lambda p: ("type", {"ref": ref_of(p, "Search for products"), "text": "headphones", "submit": True}),
            step("results", lambda p: ("finish", {"summary": "Found three headphones."})),
        ])
        agent._llm = llm
        await run_task(agent, runtime, "search the shop for headphones", {})
        check("waited for late results (names + prices visible)",
              "boAt Rockerz 450" in seen["results"] and "₹1,499" in seen["results"], seen["results"][-400:])

        llm = ScriptedLlm([
            step("followup", lambda p: ("click", {"ref": ref_of(p, "boAt Rockerz 450")})),
            lambda p: ("click", {"ref": ref_of(p, "Add to cart")}),          # confirm: yes -> login popup
            step("wall", lambda p: ("ask_user", {"question": "Please log in to Fixture Kart.", "kind": "login"})),
            lambda p: ("click", {"ref": ref_of(p, "Add to cart")}),          # confirm: yes -> added
            ("finish", {"summary": "Added boAt Rockerz 450 to the cart."}),
        ])
        agent._llm = llm
        q = runtime.events.subscribe()
        await agent.submit("add the first one to the cart")
        kinds = []
        while True:
            ev = await asyncio.wait_for(q.get(), 60)
            if ev.kind == "web_question":
                kinds.append(ev.data["kind"])
                if ev.data["kind"] == "login":
                    # Play the user: log in in the browser window, then say done.
                    page = agent.browser.page
                    await page.fill("#phone", "9999999999")
                    await page.click("#loginbtn")
                    await agent.reply("done")
                else:
                    await agent.reply("yes")
            if ev.kind == "web_done":
                break
        runtime.events.unsubscribe(q)
        page = agent.browser.page
        live_tabs = [p for p in agent.browser._ctx.pages if not p.is_closed()]
        check("follow-up sees the earlier task", "EARLIER TASKS IN THIS BROWSER" in seen["followup"]
              and "search the shop for headphones" in seen["followup"])
        check("product opened in the same tab", len(live_tabs) == 1, f"{len(live_tabs)} tabs")
        check("found the <div> 'Add to cart'", 'button "Add to cart"' in seen["wall"])
        check("login wall detected", "LOGIN WALL" in seen["wall"])
        check("handed over for login, then carried on", "login" in kinds, str(kinds))
        check("added to the cart after login", "boAt Rockerz 450" in await page.inner_text("#cart"),
              await page.inner_text("#cart"))

        # -- 4. the real model (optional) ---------------------------------------
        if live:
            print("live model")
            agent._llm = None
            _, events, task = await run_task(
                agent, runtime, f"Go to {BASE}/webmcp.html and add a USB hub to the cart.",
                {"": "yes"})
            used = [s["do"] for s in task.steps]
            print("   steps:", used, "\n   summary:", task.summary)
            check("live: used a site tool, not clicks", any("site tool" in u for u in used))
            check("live: finished", task.state == "done", task.state)
    finally:
        await agent.close()
        srv.shutdown()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    # Page text has characters like the rupee sign that cp1252 can't print.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    agent_mod.log.setLevel("WARNING")
    sys.exit(asyncio.run(main("--live" in sys.argv)))
