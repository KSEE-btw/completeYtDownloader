"""Minimal HTTP client for GitHub releases."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request

from ytgrab.console import say
from ytgrab import APP, VERSION
# GitHub coordinates.  Overridable so tests can point at a local mock server.
REPO = os.environ.get("YTGRAB_REPO", "KSEE-btw/youtubeDownloaderXConverter")
API_BASE = os.environ.get("YTGRAB_API_BASE", "https://api.github.com").rstrip("/")
RAW_BASE = os.environ.get(
    "YTGRAB_RAW_BASE",
    "https://raw.githubusercontent.com/" + REPO + "/main").rstrip("/")
UA = f"{APP}-cli/{VERSION}"


def _get(url: str, accept: str = "") -> bytes:
    headers = {"User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()
def get_json(url: str):
    return json.loads(_get(url, "application/vnd.github+json").decode("utf-8"))
def get_text(url: str) -> str:
    return _get(url).decode("utf-8", "replace")
def download(url: str, dest: str, label: str = "") -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    sys.stdout.write(f"\r  {label} {pct:3d}%")
                    sys.stdout.flush()
    if total:
        sys.stdout.write("\r" + " " * (len(label) + 8) + "\r")
    say(f"  downloaded {os.path.basename(dest)}"
        + (f" ({done // 1024} KB)" if done else ""))
def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
def verify_sums(zip_path: str, sums_text: str) -> None:
    wanted = os.path.basename(zip_path).lstrip("*")
    expected = None
    for line in (sums_text or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == wanted:
            expected = parts[0].lower()
            break
    if not expected:
        say(f"  (no checksum listed for {wanted}; skipping verification)")
        return
    actual = sha256_file(zip_path)
    if actual != expected:
        raise RuntimeError(
            f"checksum mismatch for {wanted}\n"
            f"    expected {expected}\n    actual   {actual}")
    say("  checksum OK")
