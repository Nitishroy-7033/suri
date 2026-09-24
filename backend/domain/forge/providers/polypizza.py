"""Poly Pizza: thousands of free low-poly models, CC0 or CC-BY.

API v1.1 (https://poly.pizza/docs/api/v1.1): GET /search/{keyword} with an
`x-auth-token` header; fields are PascalCase ("Title", "Download",
"Licence"). CC-BY models must be credited, so the `Attribution` string is
kept with the saved model and shown in the workshop.
"""

from __future__ import annotations

from urllib.parse import quote

from . import Provider

BASE = "https://api.poly.pizza/v1.1"


class PolyPizza(Provider):
    name = "polypizza"
    label = "Poly Pizza"

    def unavailable(self) -> str | None:
        return None if self.settings.poly_pizza_api_key else "needs POLY_PIZZA_API_KEY"

    def can(self, with_image: bool) -> bool:
        return False  # it searches; it doesn't build

    async def search(self, query: str) -> list[dict]:
        r = await self.request("GET", f"{BASE}/search/{quote(query.strip())}", params={"Limit": 8},
                               headers={"x-auth-token": self.settings.poly_pizza_api_key}, timeout=20)
        hits = []
        for m in r.json().get("results", []):
            if not m.get("Download"):
                continue
            creator = (m.get("Creator") or {}).get("Username")
            hits.append({
                "title": m.get("Title") or "Model", "url": m["Download"], "creator": creator,
                "credit": m.get("Attribution") or (f"by {creator} on Poly Pizza" if creator else "Poly Pizza"),
                "license": m.get("Licence"),
            })
        return hits
