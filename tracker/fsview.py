"""List a local directory for the left-hand file browser."""

from __future__ import annotations

import os
import string
from pathlib import Path

from .bench import parse_name

CAP = 400


def normalize(raw) -> Path:
    """A path this Python can list.

    MSYS Python accepts ``C:\\...`` but ``Path.resolve()`` turns it into ``/:\\...``,
    which does not exist. A drive path is rewritten to ``/c/...`` when that exists.
    """
    text = str(raw).strip()
    if len(text) >= 2 and text[1] == ":":
        rest = text[2:].replace("\\", "/").lstrip("/")
        alt = Path("/" + text[0].lower())
        if rest:
            alt = alt / rest
        if alt.exists():
            text = str(alt)
    path = Path(text)
    try:
        resolved = path.resolve()
    except OSError:
        return path
    if path.exists() and not resolved.exists():
        return path
    return resolved


def default_root() -> str:
    home = Path.home()
    if home.is_dir():
        return str(home)
    return str(normalize(r"C:\msys64\home\akao"))


def _drives() -> list[Path]:
    found = []
    for letter in string.ascii_uppercase:
        posix = Path("/" + letter.lower())
        windows = Path(f"{letter}:\\")
        if posix.is_dir():
            found.append(posix)
        elif windows.is_dir():
            found.append(windows)
    return found


def _kind(path: Path) -> str:
    if path.is_dir():
        return "dir"
    if path.suffix.lower() == ".bta":
        return "bta"
    if parse_name(path.name):
        return "bench"
    return "file"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _newest_first(path: Path) -> tuple:
    return (-_mtime(path), path.name.lower())


def _entry(path: Path) -> dict:
    kind = "file"
    try:
        kind = _kind(path)
    except OSError:
        kind = "file"
    return {"name": path.name or str(path), "path": str(path), "kind": kind}


def list_dir(raw: str) -> dict:
    if not raw:
        entries = [_entry(path) for path in _drives()]
        home = Path.home()
        if home.is_dir() and all(item["path"] != str(home) for item in entries):
            entries.insert(0, {"name": "Home", "path": str(home), "kind": "dir"})
        start = default_root()
        if all(item["path"] != start for item in entries):
            entries.insert(0, {"name": "Work", "path": start, "kind": "dir"})
        return {"path": "", "parent": None, "crumbs": [], "entries": entries, "truncated": False}
    try:
        path = normalize(raw)
    except OSError as exc:
        raise FileNotFoundError(str(exc)) from exc
    if not path.exists():
        raise FileNotFoundError(f"not found: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"not a directory: {path}")
    crumbs = []
    cursor = path
    while True:
        crumbs.append({"name": cursor.name or str(cursor), "path": str(cursor)})
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    crumbs.reverse()
    parent = None if path.parent == path else str(path.parent)
    entries = []
    truncated = False
    try:
        children = sorted(path.iterdir(), key=_newest_first)
    except OSError as exc:
        raise PermissionError(str(exc)) from exc
    for child in children:
        if child.name.startswith("."):
            continue
        if len(entries) >= CAP:
            truncated = True
            break
        try:
            entries.append(_entry(child))
        except OSError:
            continue
    return {
        "path": str(path),
        "parent": parent,
        "crumbs": crumbs,
        "entries": entries,
        "truncated": truncated,
    }
