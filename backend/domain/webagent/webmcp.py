"""WebMCP: tools a website publishes for agents (developer.chrome.com/docs/ai/webmcp).

A site registers them with document.modelContext.registerTool(...) or by
annotating a <form toolname=...>. Sites that register nothing have no tools:
WebMCP never clicks or types on its own, so the agent falls back to the page
snapshot and refs for those (almost every site, today).

The agent reads and runs tools through Chrome's DevTools protocol domain
"WebMCP" (Chrome 149+ with --enable-features=WebMCPTesting, headed only):

    WebMCP.enable           -> toolsAdded for every tool already registered
    toolsAdded/toolsRemoved -> kept as a live registry per page
    WebMCP.invokeTool       -> invocationId, then toolResponded with the output

That is the browser's own agent-side channel: schemas arrive as objects,
annotations as readOnly/consequential, errors as a status. The in-page API
(document.modelContext.getTools / executeTool) is kept as a fallback for a
Chrome without the domain; its shape has changed across builds (inputSchema
and the input were JSON strings up to ~153, objects later), so both are
handled there.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import weakref

log = logging.getLogger("jarvis.webmcp")

INVOKE_TIMEOUT_S = 20.0

LIST_JS = """async () => {
  const mc = document.modelContext ?? navigator.modelContext;
  if (!mc?.getTools) return null;
  const tools = await mc.getTools();
  return tools.map((t) => ({
    name: t.name, description: t.description || "",
    inputSchema: t.inputSchema ?? null, annotations: t.annotations ?? null,
  }));
}"""

EXEC_JS = """async ([name, input]) => {
  const mc = document.modelContext ?? navigator.modelContext;
  const tool = (await mc.getTools()).find((t) => t.name === name);
  if (!tool) return { ok: false, text: `no site tool named ${name}` };
  const norm = (r) => typeof r === "string" ? r : JSON.stringify(r);
  try { return { ok: true, text: norm(await mc.executeTool(tool, JSON.stringify(input))) }; }
  catch (first) {
    try { return { ok: true, text: norm(await mc.executeTool(tool, input)) }; }
    catch { return { ok: false, text: String(first) }; }
  }
}"""

_BAD_CHARS = re.compile(r"[^A-Za-z0-9_.-]")

# CDP names the hints without the "Hint" suffix; the agent reads the page
# API's names (readOnlyHint, consequentialHint), so map them back.
_HINTS = {"readOnly": "readOnlyHint", "consequential": "consequentialHint",
          "untrustedContent": "untrustedContentHint"}


def fn_name(site_tool: str) -> str:
    """The name the agent's model sees: namespaced and within Gemini's rules."""
    return ("site__" + _BAD_CHARS.sub("_", site_tool))[:64]


def parse_schema(raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if not isinstance(raw, dict) or raw.get("type") != "object":
        return {"type": "object", "properties": {}}
    return raw


def _annotations(raw: dict | None) -> dict:
    return {_HINTS.get(k, k): v for k, v in (raw or {}).items()}


def _text(output) -> str:
    """A tool's output as text: a string, MCP-style {content: [{text}]}, or JSON."""
    if isinstance(output, str):
        return output
    if isinstance(output, dict) and isinstance(output.get("content"), list):
        parts = [c.get("text", "") for c in output["content"] if isinstance(c, dict)]
        if any(parts):
            return "\n".join(p for p in parts if p)
    return json.dumps(output, ensure_ascii=False)


class Channel:
    """One page's WebMCP tools, kept current by DevTools events."""

    def __init__(self, cdp) -> None:
        self.cdp = cdp
        self.main_frame = ""
        #: (frameId, name) -> tool, in registration order.
        self.tools: dict[tuple[str, str], dict] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._early: dict[str, dict] = {}  # responses that beat invokeTool's reply

    @classmethod
    async def attach(cls, page) -> "Channel":
        cdp = await page.context.new_cdp_session(page)
        ch = cls(cdp)
        cdp.on("WebMCP.toolsAdded", ch._added)
        cdp.on("WebMCP.toolsRemoved", ch._removed)
        cdp.on("WebMCP.toolResponded", ch._responded)
        cdp.on("Page.frameNavigated", ch._navigated)
        await cdp.send("Page.enable")
        tree = await cdp.send("Page.getFrameTree")
        ch.main_frame = tree["frameTree"]["frame"]["id"]
        await cdp.send("WebMCP.enable")  # raises on a Chrome without the domain
        return ch

    # -- events --------------------------------------------------------------

    def _added(self, e: dict) -> None:
        for t in e.get("tools", []):
            self.tools[(t["frameId"], t["name"])] = t

    def _removed(self, e: dict) -> None:
        for t in e.get("tools", []):
            self.tools.pop((t["frameId"], t["name"]), None)

    def _navigated(self, e: dict) -> None:
        # A new document in a frame: its old tools went with the old one.
        # (Chrome does not always send toolsRemoved for that; seen on a reload.)
        frame = e["frame"]["id"]
        for key in [k for k in self.tools if k[0] == frame]:
            del self.tools[key]

    def _responded(self, e: dict) -> None:
        fut = self._pending.pop(e["invocationId"], None)
        if fut is None:
            self._early[e["invocationId"]] = e
        elif not fut.done():
            fut.set_result(e)

    # -- use -----------------------------------------------------------------

    def listed(self) -> list[dict]:
        """The page's own tools first; a same-named one in an iframe is skipped."""
        seen, out = set(), []
        for t in sorted(self.tools.values(), key=lambda t: t["frameId"] != self.main_frame):
            if t["name"] not in seen:
                seen.add(t["name"])
                out.append(t)
        return out

    async def invoke(self, name: str, args: dict) -> tuple[bool, str]:
        tool = next((t for t in self.listed() if t["name"] == name), None)
        if tool is None:
            return False, f"no site tool named {name} on this page any more"
        r = await self.cdp.send("WebMCP.invokeTool",
                                {"frameId": tool["frameId"], "toolName": name, "input": args or {}})
        inv = r["invocationId"]
        fut = asyncio.get_running_loop().create_future()
        if inv in self._early:
            fut.set_result(self._early.pop(inv))
        else:
            self._pending[inv] = fut
        try:
            e = await asyncio.wait_for(fut, INVOKE_TIMEOUT_S)
        except TimeoutError:
            self._pending.pop(inv, None)
            try:
                await self.cdp.send("WebMCP.cancelInvocation", {"invocationId": inv})
            except Exception:
                pass
            return False, f"the site tool gave no answer in {INVOKE_TIMEOUT_S:.0f} s"
        if e.get("status") == "Completed":
            return True, _text(e.get("output"))
        why = e.get("errorText") or (e.get("exception") or {}).get("description") or e.get("status")
        return False, str(why)


# page -> Channel, or None once DevTools WebMCP proved unavailable on it.
_channels: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


async def _channel(page) -> Channel | None:
    if page in _channels:
        return _channels[page]
    try:
        ch = await Channel.attach(page)
    except Exception as exc:
        log.info("DevTools WebMCP unavailable (%s); using the in-page API", str(exc).splitlines()[0][:120])
        ch = None
    _channels[page] = ch
    return ch


def _entry(name: str, description: str, schema, annotations: dict) -> dict:
    return {"name": name, "fn": fn_name(name), "description": (description or "")[:400],
            "schema": parse_schema(schema), "annotations": annotations or {}}


async def list_tools(page) -> list[dict]:
    """The page's WebMCP tools, or [] if it has none or WebMCP is off."""
    ch = await _channel(page)
    if ch is not None:
        return [_entry(t["name"], t.get("description", ""), t.get("inputSchema"),
                       _annotations(t.get("annotations"))) for t in ch.listed()]
    try:
        tools = await page.evaluate(LIST_JS)
    except Exception as exc:  # navigation mid-call, closed page...
        log.debug("webmcp list failed: %s", exc)
        return []
    return [_entry(t["name"], t.get("description", ""), t.get("inputSchema"), t.get("annotations"))
            for t in tools or []]


async def call_tool(page, name: str, args: dict) -> tuple[bool, str]:
    before = page.url
    try:
        ch = await _channel(page)
        if ch is not None:
            ok, text = await ch.invoke(name, args)
        else:
            r = await page.evaluate(EXEC_JS, [name, args or {}])
            ok, text = bool(r.get("ok")), str(r.get("text", ""))
    except Exception as exc:
        # A declarative form with toolautosubmit often navigates away, which
        # destroys the page context mid-call. That is success, not failure.
        if "context was destroyed" in str(exc) or "navigation" in str(exc).lower():
            return True, "submitted; the page changed"
        return False, f"{type(exc).__name__}: {exc}"[:300]
    if not ok and page.url != before:
        # The form submitted and the page moved on before the tool answered.
        return True, "submitted; the page changed"
    return ok, text[:1500]
