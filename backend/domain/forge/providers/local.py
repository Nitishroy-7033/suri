"""Your own GPU: a local image-to-3D server. Free, private, needs a 16 GB+
NVIDIA card.

FORGE_LOCAL_URL points at Tencent's Hunyuan3D-2.1 `api_server.py`
(`python api_server.py`, port 8081 by default), or anything that speaks the
same protocol:

    POST /send   {"image": "<bare base64>"}  ->  {"uid": "..."}
    GET  /status/{uid}  ->  {"status": "processing" | "texturing" | "completed",
                             "model_base64": "<GLB, base64>"}   (when completed)

Microsoft's TRELLIS.2 ships only a Gradio demo, so it needs a small wrapper
that answers those two routes. Notes from the Hunyuan3D-2.1 source: send the
image as bare base64, not a data URI; it removes no background itself, so a
plain background works best; and if texturing fails it can report
"texturing" forever -- FORGE_TIMEOUT_S ends the wait.

These servers turn a *picture* into 3D. For a text request the picture is
drawn first by FORGE_IMAGE_MODEL (a Gemini image model, GEMINI_API_KEY).
"""

from __future__ import annotations

import base64

from . import Provider
from ..service import ForgeError


class LocalServer(Provider):
    name = "local"
    label = "your local GPU"
    license = "yours (generated locally)"

    @property
    def url(self) -> str:
        return self.settings.forge_local_url.rstrip("/")

    def unavailable(self) -> str | None:
        return None if self.url else "needs FORGE_LOCAL_URL"

    def can(self, with_image: bool) -> bool:
        return with_image or bool(self.settings.forge_image_model and self.settings.gemini_api_key)

    async def draw(self, prompt: str) -> bytes:
        """A reference picture for a text request: one object, plain background."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.settings.gemini_api_key)
        try:
            resp = await client.aio.models.generate_content(
                model=self.settings.forge_image_model,
                contents=(f"A single {prompt}, the whole object in view, centred, on a plain white "
                          "background, soft studio lighting, product photo, no text, no people."),
                config=types.GenerateContentConfig(response_modalities=["IMAGE"]))
        except Exception as exc:  # the SDK's error types vary by version
            raise ForgeError(f"couldn't draw the reference picture ({type(exc).__name__})") from None
        for cand in resp.candidates or []:
            for part in (cand.content.parts if cand.content else []):
                if part.inline_data and part.inline_data.data:
                    return part.inline_data.data
        raise ForgeError("the image model returned no picture")

    async def generate(self, prompt: str, image: bytes | None, progress):
        if image is None:
            progress(0.02, "drawing a reference picture")
            image = await self.draw(prompt)
        progress(0.08, "sending to your GPU")
        r = await self.request("POST", f"{self.url}/send", json={"image": base64.b64encode(image).decode()},
                               timeout=30)
        uid = r.json().get("uid")
        if not uid:
            raise ForgeError("the local server didn't start the job")
        steps = {"processing": 0.35, "texturing": 0.75}

        async def check():
            s = (await self.request("GET", f"{self.url}/status/{uid}", timeout=30)).json()
            status = s.get("status", "")
            progress(steps.get(status, 0.2), status)
            if status == "completed" and s.get("model_base64"):
                return base64.b64decode(s["model_base64"])
            if status in ("error", "failed"):
                raise ForgeError(s.get("message") or "the local server failed")
            return None

        return await self.poll(check, interval=2.0)
