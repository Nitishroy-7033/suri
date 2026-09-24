"""Turn a page into a few hundred words the agent's model can act on.

snapshot.js does the work inside the page; this formats it. The format is
deliberately terse -- one line per element, numbered refs the model can quote
back ("click 7") -- because every character is model input on every step.
"""

from __future__ import annotations

from pathlib import Path

SNAPSHOT_JS = (Path(__file__).with_name("snapshot.js")).read_text(encoding="utf-8")


def format_snapshot(snap: dict, *, max_chars: int = 2600) -> str:
    lines = [f"URL: {snap.get('url', '')}", f"Title: {snap.get('title', '')}",
             f"Scroll: {snap.get('scroll', '')}"]
    if snap.get("popup"):
        lines.append(f"POPUP OPEN over the page: \"{snap['popup']}\". Close it (its ✕ / close / "
                     "not now button, marked (popup)) or use it before anything behind it.")
    if snap.get("login"):
        lines.append("LOGIN WALL: this page asks the user to sign in (password / phone / OTP). You cannot do "
                     "that. If the task needs it (cart, checkout, account), call ask_user with kind=login; "
                     "otherwise close it and carry on.")
    if snap.get("captcha"):
        lines.append("CAPTCHA: a human check is showing. Call ask_user with kind=action so the user solves it.")
    if snap.get("headings"):
        lines.append("Headings: " + " | ".join(snap["headings"]))
    lines.append("Elements (use the number as ref):")
    for it in snap.get("items", []):
        s = f"[{it['ref']}] {it['kind']} \"{it.get('label', '')}\""
        if it.get("popup"):
            s += " (popup)"
        if it.get("value"):
            s += f" value=\"{it['value']}\""
        if "checked" in it:
            s += " (checked)" if it["checked"] else " (unchecked)"
        if it.get("options"):
            s += " options: " + ", ".join(it["options"])
        lines.append(s)
    if not snap.get("items"):
        lines.append("(none visible -- try scrolling)")
    body = "\n".join(lines)
    text = snap.get("text", "")
    room = max(200, max_chars - len(body) - 80)
    # Page text is someone else's writing: mark it so the model treats it as
    # data, never as instructions from the user.
    return (f"{body}\n<untrusted_page_text>\n{text[:room]}\n</untrusted_page_text>")
