"""Which files the workshop may touch.

You asked for whole drives, so everything on every local drive is in bounds
-- except what an assistant reading files for you must never read: Windows
and program folders, AppData (browser cookies, saved passwords, app tokens),
credential folders (.ssh, .aws...), key and secret files (*.pem, .env...),
Jarvis's own data (the web agent's browser profile, memory), and hidden or
system files. FS_DENY adds your own.

Every path is resolved first -- "..", symlinks and junctions all followed to
where they really lead -- and checked there *and* as given, so a link inside
Documents that points at C:\\Windows is refused like C:\\Windows itself.
Only local drive-letter paths: no network shares, no \\\\?\\ device paths.
"""

from __future__ import annotations

import ctypes
import fnmatch
import os
import re
import stat
import sys
import uuid
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[3]
JARVIS_DATA = ROOT / "data"

DENY_DIRS = {
    "windows", "program files", "program files (x86)", "programdata", "$recycle.bin",
    "system volume information", "recovery", "appdata", "$windows.~bt", "$windows.~ws",
    "config.msi", "msocache", "perflogs", "windowsapps", "$sysreset", "$winreagent",
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".config", ".gcloud",
}
DENY_FILES = [
    "*.pem", "*.key", "*.pfx", "*.p12", "*.kdbx", "*.kdb", "*.ppk", "*.keystore", "*.jks",
    ".env", ".env.*", "*.env", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials", ".pgpass", "*.ovpn",
    "credentials", "credentials.json", "pagefile.sys", "hiberfil.sys", "swapfile.sys",
    "dumpstack.log*", "ntuser.dat*", "ntuser.ini", "ntuser.pol",
]
_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2) | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 4)


class Refused(Exception):
    """Why a path is out of bounds, in words fit to say to the user."""


def _extra(settings) -> tuple[list[Path], list[str]]:
    dirs, globs = [], []
    for item in (s.strip() for s in (getattr(settings, "fs_deny", "") or "").split(",")):
        if not item:
            continue
        if re.match(r"^[a-zA-Z]:[\\/]", item) or "\\" in item or "/" in item:
            dirs.append(Path(item))
        else:
            globs.append(item.lower())
    return dirs, globs


def _under(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def _is_drive_root(p: Path) -> bool:
    return p.parent == p


def _hidden(p: Path) -> bool:
    try:
        attrs = getattr(os.stat(p, follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & _HIDDEN)


def denied(p: Path, settings=None) -> str | None:
    """Why this (already resolved) path is refused, or None."""
    parts = [s.lower() for s in p.parts[1:]]  # the drive itself is fine
    bad = next((s for s in parts if s in DENY_DIRS), None)
    if bad:
        return f"{bad} is off limits"
    name = p.name.lower()
    extra_dirs, extra_globs = _extra(settings)
    if any(fnmatch.fnmatch(name, g) for g in DENY_FILES + extra_globs):
        return "that looks like a key or secrets file"
    if _under(p, JARVIS_DATA):
        return "that is Jarvis's own private data"
    for d in extra_dirs:
        try:
            if _under(p, d.resolve()):
                return "that folder is on your deny list (FS_DENY)"
        except OSError:
            continue
    if not _is_drive_root(p) and _hidden(p):
        return "that is a hidden or system file"
    return None


def resolve_checked(path: str, settings=None) -> Path:
    """The real, allowed path for `path`, or Refused."""
    raw = str(path or "").strip().strip('"')
    if not raw:
        raise Refused("no path given")
    if raw.startswith(("\\\\", "//")):
        raise Refused("network and device paths are not allowed")
    if not re.match(r"^[a-zA-Z]:([\\/]|$)", raw):
        raise Refused("give a full path starting with a drive letter, like C:\\Users")
    if re.match(r"^[a-zA-Z]:$", raw):
        raw += "\\"
    given = Path(raw)
    try:
        real = given.resolve(strict=True)
    except (OSError, RuntimeError):
        raise Refused("that path does not exist") from None
    if str(real).startswith("\\\\"):
        raise Refused("that leads to a network or device path")
    for p in (real, Path(os.path.normpath(raw))):
        why = denied(p, settings)
        if why:
            raise Refused(why)
    return real


# -- names you would say out loud ---------------------------------------------

_KNOWN = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}
_ALIASES = {"photos": "pictures", "my documents": "documents", "docs": "documents",
            "download": "downloads", "movies": "videos", "songs": "music"}


def known_folder(name: str) -> Path:
    """Desktop, Documents... where Windows really keeps them (OneDrive too)."""
    name = _ALIASES.get(name, name)
    fallback = Path.home() / name.capitalize()
    if sys.platform != "win32" or name not in _KNOWN:
        return fallback

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16),
                    ("d4", ctypes.c_ubyte * 8)]

    guid = GUID()
    ctypes.memmove(ctypes.byref(guid), uuid.UUID(_KNOWN[name]).bytes_le, 16)
    out = ctypes.c_wchar_p()
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p,
                                             ctypes.POINTER(ctypes.c_wchar_p)]
    if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
        return fallback
    try:
        return Path(out.value)
    finally:
        ole32.CoTaskMemFree(out)


def places() -> dict[str, Path]:
    """The folders people mean without saying where they are."""
    out = {"home": Path.home(), "this project": ROOT, "jarvis": ROOT}
    for name in _KNOWN:
        out[name] = known_folder(name)
    return out


def resolve_spoken(text: str, settings=None, want_dir: bool = True) -> Path | None:
    """"downloads", "the E drive", "E:\\Games" -> a checked path. None = the
    list of drives ("this PC", "my computer", or nothing at all)."""
    t = re.sub(r"\b(my|the|folder|directory|please)\b", " ", str(text or "").strip().lower()).strip(" .")
    t = re.sub(r"\s+", " ", t)
    if t in ("", "drives", "this pc", "computer", "my computer", "all drives", "pc"):
        return None
    m = re.fullmatch(r"(?:drive )?([a-z])(?: drive|:)?\\?", t)
    if m:
        return resolve_checked(f"{m.group(1).upper()}:\\", settings)
    here = places()
    key = _ALIASES.get(t, t)
    if key in here:
        return resolve_checked(str(here[key]), settings)
    if re.match(r"^[a-zA-Z]:", text.strip()):
        p = resolve_checked(text, settings)
    else:
        # "my projects folder": a folder by that name right inside a usual place.
        p = None
        for base in (here["home"], here["desktop"], here["documents"], here["downloads"]):
            try:
                for child in base.iterdir():
                    if child.name.lower() == t and child.is_dir():
                        p = resolve_checked(str(child), settings)
                        break
            except OSError:
                continue
            if p:
                break
        if p is None:
            raise Refused(f"I don't know a folder called {text!r}; say a full path like E:\\Projects")
    if want_dir and not p.is_dir():
        raise Refused("that is a file, not a folder")
    return p


def display(p: Path) -> str:
    return str(PureWindowsPath(p)) if sys.platform == "win32" else str(p)
