"""Where 3D models come from. One class per service, all the same shape:

    name, label, paid, cost_hint, license
    unavailable() -> reason or None     (cheap: no network)
    can(with_image) -> bool             (does it take a picture / text?)
    generate(prompt, image, progress) -> GLB bytes or a URL to download
    search(query) -> [{title, url, creator, credit, license}]   (search only)

The hologram draws its own look and ignores textures, so generators are
asked for untextured meshes: faster, and fewer credits.

API details checked against each service's docs in September 2026.
"""

from __future__ import annotations

import asyncio

import httpx

from ..service import ForgeError


class Provider:
    name = ""
    label = ""
    paid = False
    cost_hint = ""
    license: str | None = None

    def __init__(self, settings, runtime) -> None:
        self.settings = settings
        self.runtime = runtime

    @property
    def http(self) -> httpx.AsyncClient:
        if self.runtime.http is None:
            raise ForgeError("no network client")
        return self.runtime.http

    def unavailable(self) -> str | None:
        return None

    def can(self, with_image: bool) -> bool:
        return True

    async def request(self, method: str, url: str, **kw) -> httpx.Response:
        """One HTTP call, with the errors people can act on put in words."""
        try:
            r = await self.http.request(method, url, timeout=kw.pop("timeout", 60), **kw)
        except httpx.HTTPError as exc:
            raise ForgeError(f"{self.label} can't be reached ({type(exc).__name__})") from None
        if r.status_code in (401, 403):
            raise ForgeError(f"{self.label} refused the API key")
        if r.status_code == 402:
            raise ForgeError(f"not enough {self.label} credits")
        if r.status_code == 429:
            raise ForgeError(f"{self.label} is busy or rate-limited; try again in a minute")
        if r.status_code >= 400:
            raise ForgeError(f"{self.label} error {r.status_code}: {r.text[:160]}")
        return r

    @staticmethod
    async def poll(check, interval: float = 2.0):
        """Call `check()` until it returns something other than None."""
        while True:
            out = await check()
            if out is not None:
                return out
            await asyncio.sleep(interval)


def all_providers(settings, runtime) -> list[Provider]:
    from .local import LocalServer
    from .meshy import Meshy
    from .polypizza import PolyPizza
    from .tripo import Tripo

    return [cls(settings, runtime) for cls in (PolyPizza, LocalServer, Tripo, Meshy)]
