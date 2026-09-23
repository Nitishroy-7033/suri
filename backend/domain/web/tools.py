"""Web search and page reading, with no API key.

Search goes through DuckDuckGo's HTML endpoint: free, keyless, and fast
enough (~0.5 s) to sit inside a voice turn. It is a scrape, so if DuckDuckGo
changes its markup the regexes below are what breaks -- the tool then returns
an error the model can apologise with, rather than taking the turn down.
"""

from __future__ import annotations

import html
import ipaddress
import re
from urllib.parse import parse_qs, unquote, urlparse

from ...core.tools.base import ToolContext, tool

DDG_URL = "https://html.duckduckgo.com/html/"

_RESULT = re.compile(
    r'class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'.*?class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.S,
)
_TAG = re.compile(r"<[^>]+>")
_DROP = re.compile(r"<(script|style|noscript|svg|head|nav|footer)\b.*?</\1>", re.S | re.I)
_SPACE = re.compile(r"\s+")


def _clean(fragment: str) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub("", fragment))).strip()


def _real_url(href: str) -> str:
    """DuckDuckGo wraps results in a redirect; unwrap it."""
    if href.startswith("//"):
        href = "https:" + href
    q = parse_qs(urlparse(href).query)
    return unquote(q["uddg"][0]) if "uddg" in q else href


@tool(
    "web_search",
    "Search the web. Use for news, facts that may have changed, prices, "
    "weather, sports results, or anything you are unsure of.",
    params={
        "query": {"type": "string", "description": "The search query."},
        "max_results": {"type": "integer", "description": "1-8, default 5."},
    },
    required=["query"],
)
async def web_search(ctx: ToolContext, query: str, max_results: int = 5) -> str:
    resp = await ctx.runtime.http.post(DDG_URL, data={"q": query})
    resp.raise_for_status()
    hits = []
    for m in _RESULT.finditer(resp.text):
        url = _real_url(m["href"])
        if "duckduckgo.com/y.js" in url:  # ads
            continue
        hits.append(f"{_clean(m['title'])} ({urlparse(url).netloc}): "
                    f"{_clean(m['snippet'])} <{url}>")
        if len(hits) >= max(1, min(int(max_results), 8)):
            break
    if not hits:
        return f"no results for {query!r}"
    return "\n".join(f"{i}. {h}" for i, h in enumerate(hits, 1))


def _is_private(host: str) -> bool:
    if host in ("localhost", "") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


@tool(
    "read_webpage",
    "Fetch a web page and return its main text. Use after web_search when "
    "the snippets are not enough.",
    params={"url": {"type": "string", "description": "An http or https URL."}},
    required=["url"],
)
async def read_webpage(ctx: ToolContext, url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "error: only http and https URLs can be read"
    if _is_private(parsed.hostname or ""):
        return "error: local and private network addresses are not allowed"
    resp = await ctx.runtime.http.get(url)
    resp.raise_for_status()
    kind = resp.headers.get("content-type", "")
    if "html" not in kind and "text" not in kind:
        return f"error: not a text page ({kind or 'unknown type'})"
    text = _clean(_DROP.sub(" ", resp.text))
    return text[: ctx.settings.webpage_max_chars] or "the page had no readable text"
