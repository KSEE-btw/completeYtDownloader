"""yt-dlp facing bits: cookie lookup, base args, format listing."""

from __future__ import annotations

import json
import os
import subprocess

from ytgrab.infrastructure.config import load_config
from ytgrab.infrastructure.tools import child_kwargs, tools
from ytgrab.log import log
def download_speed_args(cfg: dict):
    args = []
    try:
        frags = int(cfg.get("concurrent_fragments") or 1)
    except (TypeError, ValueError):
        frags = 1
    if frags > 1:
        args += ["--concurrent-fragments", str(frags)]
    chunk = (cfg.get("http_chunk_size") or "").strip()
    if chunk:
        args += ["--http-chunk-size", chunk]
    want_aria = str(cfg.get("use_aria2c", "auto")).lower()
    if want_aria not in ("false", "0", "off", "no"):
        if tools().get("aria2c"):
            args += ["--downloader", "aria2c",
                     "--downloader-args",
                     "aria2c:-x16 -s16 -k1M --file-allocation=none"]
    return args
def _firefox_cookie_profile() -> str:
    """Path to a Firefox profile that has a cookie store, or "".

    Only used to make the "auto" cookie choice; yt-dlp itself locates the
    profile from the browser name.
    """
    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(os.path.join(appdata, "Mozilla", "Firefox", "Profiles"))
    home = os.path.expanduser("~")
    roots.append(os.path.join(home, "Library", "Application Support",
                              "Firefox", "Profiles"))
    roots.append(os.path.join(home, ".mozilla", "firefox"))
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            try:
                db = os.path.join(entry.path, "cookies.sqlite")
                if entry.is_dir() and os.path.isfile(db):
                    return entry.path
            except OSError:
                continue
    return ""
def resolve_cookies_from_browser(cfg: dict) -> str:
    """Which browser yt-dlp should borrow cookies from.

    Empty or "auto" (the default) uses Firefox when it actually has a cookie
    store -- the browser ytgrab runs in, where a YouTube sign-in normally
    lives -- so a fresh install clears YouTube's "confirm you're not a bot"
    wall with no setup.  "none"/"off" disables it; anything else is passed
    straight to yt-dlp (firefox, chrome, edge, brave, ...).
    """
    raw = str(cfg.get("cookies_from_browser") or "").strip()
    low = raw.lower()
    if low in ("none", "off", "no", "false", "0"):
        return ""
    if raw and low != "auto":
        return raw
    return "firefox" if _firefox_cookie_profile() else ""
def ytdlp_base_args(cfg: dict, progress: bool = False):
    args = ["--no-warnings", "--color", "never"]
    if not progress:
        args.append("--no-progress")
    if cfg.get("force_no_playlist"):
        args.append("--no-playlist")
    cookies = resolve_cookies_from_browser(cfg)
    if cookies:
        args += ["--cookies-from-browser", cookies]
    args += list(cfg.get("extra_ytdlp_args") or [])
    return args
def run_ytdlp_json(url: str, timeout: int = 90) -> dict:
    cfg = load_config()
    exe = tools()["ytdlp"]
    if not exe:
        raise RuntimeError("yt-dlp was not found on PATH")
    args = ([exe] + ytdlp_base_args(cfg)
            + ["--dump-single-json", "--skip-download", url])
    log("probe:", " ".join(args))
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, **child_kwargs())
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"yt-dlp did not finish reading this video within {timeout}s. "
            "Live streams, premieres and age-restricted videos are the usual "
            "cause -- try again, or pick a normal video.")
    if proc.returncode != 0 or not proc.stdout.strip():
        tail = (proc.stderr or "").strip().splitlines()
        raise RuntimeError(tail[-1] if tail else "yt-dlp could not read this URL")
    return json.loads(proc.stdout)
