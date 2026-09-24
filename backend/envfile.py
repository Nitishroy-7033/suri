"""Read and rewrite .env without losing its comments, order or layout.

The Setup page saves here. A key already in the file is changed where it
stands; a new one goes next to its relatives (VOICE_LLM__TEMPERATURE after the
other VOICE_LLM__ lines, a new *_API_KEY after the other keys), or at the end
under a marker; None deletes it. Values are quoted only when python-dotenv
would otherwise misread them -- a " #" starts a comment, "${X}" interpolates.

Nothing is written here directly: `render` returns the new text, and the
caller validates it (a Settings built from it must load) before `write`
swaps it in atomically.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import dotenv_values

#: Same file pydantic-settings reads (Settings.model_config, relative to the cwd).
ENV_PATH = Path(".env")

MARKER = "# --- set from the Setup page ---"

_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def read(path: Path | None = None) -> dict[str, str]:
    """Parsed values, exactly as Settings will see them."""
    path = path or ENV_PATH
    if not path.exists():
        return {}
    return {k: v or "" for k, v in dotenv_values(path, encoding="utf-8").items()}


def format_value(value: str) -> str:
    # python-dotenv expands ${NAME} in every value, quoted or not, and has no
    # escape for it: such a value could be written but never read back.
    if "${" in value:
        raise ValueError("values can't contain '${' (.env would expand it)")
    value = value.replace("\r", "")
    plain = (value == value.strip() and "#" not in value and "\n" not in value
             and not value.startswith(("'", '"')))
    if plain:
        return value
    if "\n" not in value:
        # Single quotes decode only \\ and \'.
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _entries(lines: list[str]) -> list[tuple[str | None, int, int]]:
    """(key or None, first line, end line) per logical entry. A quoted value
    may run over several lines; those lines belong to its entry."""
    out = []
    i = 0
    while i < len(lines):
        m = _ASSIGN.match(lines[i])
        if not m:
            out.append((None, i, i + 1))
            i += 1
            continue
        key, rest = m.group(1), m.group(2)
        end = i + 1
        q = rest[:1]
        if q in ("'", '"') and not _closes(rest[1:], q):
            while end < len(lines) and not _closes(lines[end], q):
                end += 1
            end = min(end + 1, len(lines))
        out.append((key, i, end))
        i = end
    return out


def _closes(text: str, quote: str) -> bool:
    """True if `text` holds an unescaped closing `quote`."""
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == quote:
            return True
    return False


def _family(key: str) -> str:
    """Which existing lines a new key should sit next to."""
    if key.endswith("_API_KEY"):
        return "*_API_KEY"
    if "__" in key:
        return key.split("__", 1)[0] + "__"
    return key.split("_", 1)[0] + "_"


def _in_family(key: str, family: str) -> bool:
    return key.endswith("_API_KEY") if family == "*_API_KEY" else key.startswith(family)


def render(text: str, changes: dict[str, str | None]) -> str:
    """`text` with each KEY set to its new value (None = removed). Keys match
    without regard to case, as pydantic-settings reads them."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    # Upper-cased once here, so every comparison below is case-blind.
    entries = [(k.upper() if k else None, a, b) for k, a, b in _entries(lines)]
    changes = {k.upper(): v for k, v in changes.items()}
    last_of: dict[str, int] = {}
    for n, (key, _, _) in enumerate(entries):
        if key:
            last_of[key] = n

    # One output chunk per entry; a key that appears twice keeps only its
    # last, effective line.
    chunks: list[list[str]] = [lines[a:b] for _, a, b in entries]
    for n, (key, _, _) in enumerate(entries):
        if key in changes and last_of[key] != n:
            chunks[n] = []
    pending: list[str] = []
    for key, value in changes.items():
        if key in last_of:
            n = last_of[key]
            chunks[n] = [] if value is None else [f"{key}={format_value(value)}"]
        elif value is not None:
            pending.append(key)

    tail: list[str] = []
    for key in pending:
        line = f"{key}={format_value(changes[key])}"
        family = _family(key)
        near = [n for n, (k, _, _) in enumerate(entries) if k and chunks[n] and _in_family(k, family)]
        if near:
            chunks[near[-1]] = chunks[near[-1]] + [line]
        else:
            tail.append(line)
    if tail:
        body = [ln for c in chunks for ln in c]
        if MARKER not in body:
            while body and not body[-1].strip():
                body.pop()
            body += ["", MARKER] if body else [MARKER]
        body += tail
    else:
        body = [ln for c in chunks for ln in c]
    return nl.join(body) + nl


def write(text: str, path: Path | None = None) -> None:
    """Swap the new contents in, so a crash can't leave half a file."""
    path = path or ENV_PATH
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def load_text(path: Path | None = None) -> str:
    path = path or ENV_PATH
    if not path.exists():
        return ""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()
