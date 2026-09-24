"""Meshy: text or a picture to a 3D mesh. Paid credits.

API (https://docs.meshy.ai/api): Bearer key. Text-to-3D has a "preview"
stage (the mesh) and a "refine" stage (textures); the hologram ignores
textures, so only the preview runs -- about 20 credits. Image-to-3D takes the
picture as a data URI. The finished model is at model_urls.glb.
"""

from __future__ import annotations

import base64

from . import Provider
from ..service import ForgeError

BASE = "https://api.meshy.ai/openapi"
DONE_BAD = {"FAILED", "CANCELED", "CANCELLED"}


class Meshy(Provider):
    name = "meshy"
    label = "Meshy"
    paid = True
    cost_hint = "about 20 credits"
    license = "yours (Meshy generation)"

    def unavailable(self) -> str | None:
        return None if self.settings.meshy_api_key else "needs MESHY_API_KEY"

    @property
    def auth(self) -> dict:
        return {"Authorization": f"Bearer {self.settings.meshy_api_key}"}

    async def generate(self, prompt: str, image: bytes | None, progress):
        if image is not None:
            path = "/v1/image-to-3d"
            body = {"image_url": "data:image/jpeg;base64," + base64.b64encode(image).decode(),
                    "ai_model": "latest", "should_texture": False}
        else:
            path = "/v2/text-to-3d"
            body = {"mode": "preview", "prompt": prompt[:780], "ai_model": "latest",
                    "should_remesh": True, "topology": "triangle", "target_polycount": 30000}
        r = await self.request("POST", f"{BASE}{path}", headers=self.auth, json=body)
        task_id = r.json().get("result")
        if not task_id:
            raise ForgeError("Meshy didn't start the task")

        async def check():
            t = (await self.request("GET", f"{BASE}{path}/{task_id}", headers=self.auth, timeout=30)).json()
            status = t.get("status")
            progress(0.05 + 0.9 * (t.get("progress") or 0) / 100, (status or "").lower().replace("_", " "))
            if status == "SUCCEEDED":
                url = (t.get("model_urls") or {}).get("glb")
                if not url:
                    raise ForgeError("Meshy finished but sent no GLB")
                return url
            if status in DONE_BAD:
                raise ForgeError((t.get("task_error") or {}).get("message") or f"Meshy says the task {status.lower()}")
            return None

        return await self.poll(check, interval=3.0)
