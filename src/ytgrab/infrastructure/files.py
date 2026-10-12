"""Safe file names, unique paths, output picking."""

from __future__ import annotations

import os
import re

from ytgrab.domain.models import VIDEO_EXTS
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
def safe_name(name: str, fallback: str = "video") -> str:
    name = _ILLEGAL.sub("_", (name or "").strip())
    name = name.rstrip(" .")
    if not name:
        name = fallback
    return name[:120]
def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"
def expand(path: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def _pick_output(tmpdir: str):
    best = None
    for name in os.listdir(tmpdir):
        path = os.path.join(tmpdir, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
            continue
        if best is None or os.path.getmtime(path) > os.path.getmtime(best):
            best = path
    return best
