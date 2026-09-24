"""Tripo3D: text or a picture to a 3D mesh, in about a minute. Paid credits.

API v3 (https://developers.tripo3d.ai/en/docs): Bearer key; every answer is
{"code": 0, "data": {...}}, anything else is an error. Pictures are uploaded
to /files first and passed by file_token. The finished model is at
data.output.model_url. Untextured, a text model costs about 10 credits and
an image model about 20 (1 credit = $0.01).
"""

from __future__ import annotations

from . import Provider
from ..service import ForgeError

BASE = "https://openapi.tripo3d.ai/v3"
MODEL = "v3.1-20260211"
DONE_BAD = {"failed", "cancelled", "banned", "expired"}


class Tripo(Provider):
    name = "tripo"
    label = "Tripo"
    paid = True
    cost_hint = "about 10 credits from text, 20 from a picture"
    license = "yours (Tripo generation)"

    def unavailable(self) -> str | None:
        return None if self.settings.tripo_api_key else "needs TRIPO_API_KEY"

    @property
    def auth(self) -> dict:
        return {"Authorization": f"Bearer {self.settings.tripo_api_key}"}

    async def call(self, method: str, path: str, **kw) -> dict:
        r = await self.request(method, f"{BASE}{path}", headers=self.auth, **kw)
        body = r.json()
        if body.get("code", 0) != 0:
            msg = body.get("message") or "request failed"
            if body.get("code") == 2010:
                msg = "not enough Tripo credits"
            raise ForgeError(msg)
        return body.get("data") or {}

    async def generate(self, prompt: str, image: bytes | None, progress):
        if image is not None:
            progress(0.05, "uploading the picture")
            up = await self.call("POST", "/files", files={"file": ("snapshot.jpg", image, "image/jpeg")})
            task = await self.call("POST", "/generation/image-to-model", json={
                "input": up["file_token"], "model": MODEL, "texture": False, "pbr": False,
                "enable_image_autofix": True})
        else:
            task = await self.call("POST", "/generation/text-to-model", json={
                "prompt": prompt[:1000], "model": MODEL, "texture": False, "pbr": False})
        task_id = task["task_id"]

        async def check():
            t = await self.call("GET", f"/tasks/{task_id}", timeout=30)
            status = t.get("status")
            progress(0.1 + 0.85 * (t.get("progress") or 0) / 100, status or "")
            if status == "success":
                url = (t.get("output") or {}).get("model_url")
                if not url:
                    raise ForgeError("Tripo finished but sent no model")
                return url
            if status in DONE_BAD:
                raise ForgeError(t.get("error_message") or f"Tripo says the task {status}")
            return None

        return await self.poll(check, interval=2.0)
