"""Reading GitHub releases: what is newer, and what changed."""

from __future__ import annotations

import urllib.error

from ytgrab.infrastructure.install import fmt_version, parse_version
from ytgrab.infrastructure.net import API_BASE, RAW_BASE, REPO, get_json, get_text
def latest_release():
    """The newest published release, or None when the repo has none yet."""
    try:
        return get_json(f"{API_BASE}/repos/{REPO}/releases/latest")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
def all_releases():
    try:
        data = get_json(f"{API_BASE}/repos/{REPO}/releases?per_page=100")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    return data if isinstance(data, list) else []
def release_version(rel):
    return parse_version((rel or {}).get("tag_name") or "")
def find_asset(rel, name: str):
    want = name.lower()
    for asset in (rel or {}).get("assets") or []:
        if (asset.get("name") or "").lower() == want:
            return asset
    return None
def notes_for(version_text: str, rel=None) -> str:
    """The doc for one version: release body first, then the raw repo file."""
    body = (rel or {}).get("body") or ""
    if body.strip():
        return body.strip()
    for name in (f"v{version_text}.md", f"{version_text}.md"):
        try:
            text = get_text(f"{RAW_BASE}/docs/releases/{name}")
            if text.strip():
                return text.strip()
        except urllib.error.HTTPError:
            continue
    return ""
def notes_between(old_text: str, new_text: str):
    """[(version, notes)] for every release newer than old, up to new."""
    old = parse_version(old_text)
    new = parse_version(new_text)
    picked = []
    for rel in all_releases():
        ver = release_version(rel)
        if ver is None or new is None:
            continue
        if (old is None or ver > old) and ver <= new:
            picked.append((ver, rel))
    if not picked:
        rel = latest_release()
        ver = release_version(rel) if rel else None
        if ver is not None:
            picked.append((ver, rel))
    picked.sort(key=lambda pair: pair[0])
    return [(fmt_version(ver), notes_for(fmt_version(ver), rel))
            for ver, rel in picked]
