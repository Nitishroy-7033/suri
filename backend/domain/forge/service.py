"""The forge: finds free 3D models and builds new ones, in the background.

Shaped like the web agent -- submit, run as a background job, report through
events, and have Jarvis say so when it is done -- but with no model of its
own: which service to try is fixed logic, not reasoning.

    find   free model search (Poly Pizza), seconds; answered in the same turn
    build  generation (your own TRELLIS / Hunyuan3D server, Tripo, Meshy),
           a minute or two; Jarvis says when it is ready

Paid services only run after the user's explicit yes (FORGE_REQUIRE_CONFIRM,
enforced here, not left to the prompt). Every finished model is saved to
data/models/ and the library, and put on the projector if the workshop is
open.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import secrets
import time
from dataclasses import dataclass, field

from ...core.events import Event

log = logging.getLogger("jarvis.forge")

MAX_BYTES = 120_000_000


class ForgeError(RuntimeError):
    """A provider failed; the message is fit to tell the user."""


@dataclass
class Job:
    id: str
    prompt: str
    image: bytes | None = None
    state: str = "working"  # working | done | failed | cancelled
    provider: str = ""
    progress: float = 0.0
    note: str = ""
    entry: dict | None = None
    error: str = ""
    started: float = field(default_factory=time.time)
    task: asyncio.Task | None = None

    def public(self) -> dict:
        return {"id": self.id, "prompt": self.prompt, "state": self.state, "provider": self.provider,
                "progress": round(self.progress, 2), "note": self.note, "error": self.error or None,
                "entry": self.entry, "seconds": round(time.time() - self.started)}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "model"


def title(text: str) -> str:
    t = re.sub(r"^(a|an|the|me|build|make|generate|create)\s+", "", text.strip(), flags=re.I)
    return (t[:1].upper() + t[1:])[:60] or "Model"


def is_glb(data: bytes) -> bool:
    return data[:4] == b"glTF"


class Forge:
    def __init__(self, runtime, providers: list | None = None) -> None:
        self.runtime = runtime
        self.settings = runtime.settings
        if providers is None:
            from .providers import all_providers

            providers = all_providers(self.settings, runtime)
        self.providers = {p.name: p for p in providers}
        self.job: Job | None = None
        self.last: Job | None = None

    # -- what is set up ----------------------------------------------------------

    def searcher(self):
        p = self.providers.get("polypizza")
        return p if p and not p.unavailable() else None

    def builders(self, with_image: bool) -> list:
        """Generators in FORGE_PROVIDERS order that can take this request."""
        order = [s.strip() for s in self.settings.forge_providers.split(",") if s.strip()]
        out = []
        for name in order:
            p = self.providers.get(name)
            if p is None or p.unavailable() or not p.can(with_image):
                continue
            out.append(p)
        return out

    # -- find ----------------------------------------------------------------------

    async def find(self, query: str) -> str:
        search = self.searcher()
        if search is None:
            return "free model search isn't set up (POLY_PIZZA_API_KEY)"
        try:
            hits = await search.search(query)
        except ForgeError as exc:
            return f"the search failed: {exc}"
        if not hits:
            return f"no free model found for {query!r}"
        hit = hits[0]
        try:
            data = await self.download(hit["url"])
        except ForgeError as exc:
            return f"found {hit['title']!r} but couldn't download it: {exc}"
        entry = self.save(data, hit["title"], query, license=hit.get("license"), credit=hit.get("credit"))
        self.show(entry)
        others = ", ".join(h["title"] for h in hits[1:4])
        who = hit.get("creator") or hit.get("credit") or "an unknown artist"
        return f"found {entry['name']} by {who} and put it on screen" + (
            f". Other matches: {others}" if others else "")

    # -- build -----------------------------------------------------------------------

    async def build(self, prompt: str, image: bytes | None = None, confirmed: bool = False) -> str:
        if self.job and self.job.state == "working":
            return f"already building {self.job.prompt!r} ({round(self.job.progress * 100)}%); wait or cancel it"
        chain = self.builders(with_image=image is not None)
        if not chain:
            need = "image-to-3D service" if image else "3D generator"
            return (f"no {need} is set up. Add TRIPO_API_KEY or MESHY_API_KEY (paid credits), or run a "
                    "local TRELLIS / Hunyuan3D server and set FORGE_LOCAL_URL.")
        # Without the user's yes, paid services are skipped -- including as a
        # fallback after a free one fails.
        needs_yes = self.settings.forge_require_confirm and not confirmed
        if needs_yes and chain[0].paid:
            paid = [p for p in chain if p.paid]
            names = " or ".join(p.label for p in paid[:2])
            return (f"confirm first: building this uses paid {names} credits ({paid[0].cost_hint}). Ask the "
                    "user; if they say yes, call forge_build again with confirmed=true.")
        chain = [p for p in chain if not (needs_yes and p.paid)]
        job = Job(id=secrets.token_hex(4), prompt=prompt.strip()[:300], image=image)
        self.job = job
        job.task = self.runtime.spawn(self._run(job, chain))
        self._publish("forge_start", job)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(job.task), timeout=self.settings.forge_sync_wait_s)
        if job.state == "done":
            return f"built {job.entry['name']} and put it on screen"
        if job.state == "failed":
            return f"couldn't build it: {job.error}"
        return (f"building {job.prompt!r} with {job.provider or chain[0].label}; it takes a minute or two. "
                "Jarvis will say when it's ready -- don't guess the result.")

    async def _run(self, job: Job, chain: list) -> None:
        errors = []
        try:
            for provider in chain:
                if job.state != "working":
                    return
                job.provider, job.progress, job.note = provider.label, 0.0, "starting"
                self._publish("forge_progress", job)

                def progress(frac: float, note: str = "") -> None:
                    job.progress = max(job.progress, min(1.0, frac))
                    job.note = note or job.note
                    self._publish("forge_progress", job)

                try:
                    data = await asyncio.wait_for(
                        provider.generate(job.prompt, job.image, progress),
                        timeout=self.settings.forge_timeout_s)
                    if isinstance(data, str):
                        data = await self.download(data)
                    if not is_glb(data):
                        raise ForgeError("the service sent something that isn't a GLB model")
                except (ForgeError, asyncio.TimeoutError) as exc:
                    why = str(exc) or "it took too long"
                    log.warning("forge: %s failed: %s", provider.name, why)
                    errors.append(f"{provider.label}: {why}")
                    continue
                job.entry = self.save(data, title(job.prompt), job.prompt,
                                      license=provider.license, credit=f"generated with {provider.label}")
                job.state, job.progress, job.note = "done", 1.0, "ready"
                self.show(job.entry)
                self._publish("forge_done", job,
                              say=f"The user's 3D model of {job.prompt} is ready and on the hologram. "
                                  "Tell them in a few words.")
                return
            job.state, job.error = "failed", "; ".join(errors) or "no generator worked"
            self._publish("forge_done", job, say=f"Building the 3D model of {job.prompt} failed "
                                                 f"({job.error[:160]}). Tell the user briefly.")
        except asyncio.CancelledError:
            job.state = "cancelled"
            self._publish("forge_done", job)
            raise
        finally:
            self.last = job

    # -- helpers ---------------------------------------------------------------------

    async def download(self, url: str) -> bytes:
        http = self.runtime.http
        if http is None:
            raise ForgeError("no network client")
        try:
            async with http.stream("GET", url, timeout=120) as r:
                if r.status_code != 200:
                    raise ForgeError(f"download failed ({r.status_code})")
                chunks, size = [], 0
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ForgeError("the model is too big (over 120 MB)")
                    chunks.append(chunk)
        except ForgeError:
            raise
        except Exception as exc:  # httpx's many network errors
            raise ForgeError(f"download failed: {type(exc).__name__}") from None
        return b"".join(chunks)

    def save(self, data: bytes, name: str, query: str, license: str | None = None,
             credit: str | None = None) -> dict:
        catalog = self.runtime.holo_models
        catalog.user_dir.mkdir(parents=True, exist_ok=True)
        model_id = f"{slug(name)}-{secrets.token_hex(3)}"
        (catalog.user_dir / f"{model_id}.glb").write_bytes(data)
        words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2]
        return catalog.add({
            "id": model_id, "name": name, "aliases": list(dict.fromkeys([query.lower(), *words]))[:6],
            "src": f"/models/{model_id}.glb", "license": license, "credit": credit,
        })

    def show(self, entry: dict) -> None:
        if self.runtime.holo.open:
            self.runtime.events.publish(Event("holo_show", data={"entry": entry}))

    def _publish(self, kind: str, job: Job, say: str | None = None) -> None:
        self.runtime.events.publish(Event(kind, say=say, data=job.public()))

    def status(self) -> dict:
        job = self.job or self.last
        if job is None:
            return {"state": "idle", "search": bool(self.searcher()),
                    "builders": [p.label for p in self.builders(False)]}
        return job.public()

    async def cancel(self) -> str:
        job = self.job
        if not job or job.state != "working" or not job.task:
            return "nothing is being built"
        job.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await job.task
        return f"stopped building {job.prompt!r}"

    async def close(self) -> None:
        if self.job and self.job.task and not self.job.task.done():
            self.job.task.cancel()
