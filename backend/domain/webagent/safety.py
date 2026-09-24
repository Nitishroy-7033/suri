"""What the web agent must never do on its own.

Enforced here, in code, not just asked for in the prompt: a model that was
told "never buy anything" can still be talked into it by a page. These checks
run on the real element or tool, whatever the model believes it is doing.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Clicking or submitting any of these needs the user's spoken yes.
RISKY = re.compile(
    r"\b(buy|purchase|order|checkout|check out|pay|payment|place order|book|booking|"
    r"confirm|delete|remove|unsubscribe|subscribe|send|post|publish|tweet|reply|share|"
    r"transfer|withdraw|donate|sign up|register|submit|add to cart|add to bag|cart)\b",
    re.I,
)

# Fields the agent refuses to type into; the user types these themselves.
SECRET_ATTR = re.compile(r"(pass(word|code)?|pwd|otp|one-time|cvv|cvc|card|cc-|iban|pin\b|ssn)", re.I)


def is_risky(text: str) -> bool:
    return bool(RISKY.search(text or ""))


def is_secret_field(attrs: dict) -> bool:
    """attrs: type, name, id, autocomplete, placeholder, aria-label of an input."""
    if (attrs.get("type") or "").lower() == "password":
        return True
    blob = " ".join(str(attrs.get(k) or "") for k in ("name", "id", "autocomplete", "placeholder", "aria"))
    return bool(SECRET_ATTR.search(blob))


def blocked(url: str, blocked_domains: str) -> str | None:
    """A reason if this URL is on the blocklist, else None."""
    host = (urlparse(url).hostname or "").lower()
    for d in (x.strip().lower() for x in blocked_domains.split(",")):
        if d and (host == d or host.endswith("." + d)):
            return f"{host} is on the blocked list"
    scheme = urlparse(url).scheme
    if scheme not in ("http", "https", "about"):
        return f"{scheme}: links are not allowed"
    return None
