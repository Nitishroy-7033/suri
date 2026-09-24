"""Who may talk to this server.

It only listens on 127.0.0.1, but that is not the same as "only Jarvis's own
page". Any website open in any browser on this machine -- including the pages
the web agent visits -- can try `ws://127.0.0.1:8080/ws` or fetch
`http://127.0.0.1:8080/api/...`. Three checks close that, all in code:

1. Host: every request must name this machine (127.0.0.1, localhost, ::1 or
   the configured host). A DNS-rebinding page arrives as "evil.com:8080".
2. Origin: a browser always sends one on a WebSocket handshake, and it must be
   this very server. Non-browser clients (tests, probes) send none and pass.
3. Token: made fresh each start and handed to the page in the "ready"
   message. Routes that read files or change the machine require it -- a
   foreign page can neither read it nor guess it.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlparse

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

#: The per-process secret. A restart invalidates every page's copy, and a
#: reconnect hands the page the new one.
TOKEN = secrets.token_urlsafe(24)

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def allowed_hostnames(host: str, extra: str = "") -> set[str]:
    names = set(LOOPBACK)
    if host and host not in ("0.0.0.0", "::"):
        names.add(host.lower())
    names |= {h.strip().lower() for h in extra.split(",") if h.strip()}
    return names


def hostname_of(netloc: str) -> str:
    """"127.0.0.1:8080" -> "127.0.0.1", "[::1]:8080" -> "::1"."""
    return (urlparse(f"//{netloc}").hostname or "").lower()


def origin_ok(origin: str | None, host_header: str | None) -> bool:
    """Same-origin only: scheme http(s) and exactly the host:port we serve."""
    if origin is None:
        return True  # not a browser: a browser always sends Origin on a handshake
    parsed = urlparse(origin)
    return (parsed.scheme in ("http", "https") and bool(host_header)
            and parsed.netloc.lower() == host_header.lower())


def token_ok(given: str | None) -> bool:
    return bool(given) and secrets.compare_digest(given, TOKEN)


class HostGuard:
    """ASGI middleware: refuse any request whose Host is not this machine."""

    def __init__(self, app: ASGIApp, hostnames: set[str]) -> None:
        self.app = app
        self.hostnames = hostnames

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            host = next((v.decode("latin-1") for k, v in scope.get("headers", [])
                         if k == b"host"), "")
            if hostname_of(host) not in self.hostnames:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await JSONResponse({"error": "unknown host"}, status_code=403)(
                        scope, receive, send)
                return
        await self.app(scope, receive, send)
