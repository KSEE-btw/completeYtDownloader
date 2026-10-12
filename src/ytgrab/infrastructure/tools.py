"""Locating yt-dlp and ffmpeg."""

from __future__ import annotations

import os
import shutil
import subprocess

from ytgrab.infrastructure.config import load_config
def _path_dirs() -> list[str]:
    """Directories to search for tools.

    Firefox launches the native host with the environment Firefox had when it
    started, so tools installed since then (winget, setx, installer scripts)
    may not be on the inherited PATH yet.  On Windows we merge the persisted
    copy of the PATH stored in the registry (user first, then machine) with the
    inherited one, so freshly installed yt-dlp/ffmpeg are found without making
    the user restart the browser.
    """
    seen: dict[str, bool] = {}
    merged: list[str] = []

    def add(value) -> None:
        if not value:
            return
        for directory in str(value).split(os.pathsep):
            directory = directory.strip('"')
            if not directory:
                continue
            key = os.path.normcase(os.path.abspath(directory))
            if key in seen:
                continue
            seen[key] = True
            merged.append(os.path.expandvars(directory))

    add(os.environ.get("PATH"))
    if os.name == "nt":
        try:
            import winreg
        except ImportError:                 # pragma: no cover - Windows-only
            winreg = None
        if winreg is not None:
            locations = (
                (winreg.HKEY_CURRENT_USER, r"Environment"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            )
            for root, sub in locations:
                try:
                    with winreg.OpenKey(root, sub) as key:
                        add(winreg.QueryValueEx(key, "Path")[0])
                except OSError:
                    continue
    return merged
def _which(name: str, override: str = "") -> str:
    if override:
        candidate = os.path.expandvars(os.path.expanduser(override))
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        exe = name if name.lower().endswith(".exe") else name + ".exe"
        for base in _path_dirs() + [
            os.path.join(os.environ.get("LOCALAPPDATA", ""),
                         r"Microsoft\WinGet\Links"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""),
                         r"Microsoft\WindowsApps"),
            r"C:\ffmpeg\bin",
        ]:
            candidate = os.path.join(base, exe)
            if os.path.isfile(candidate):
                return candidate
    return ""
def tools() -> dict:
    cfg = load_config()
    return {
        "ytdlp": _which("yt-dlp", cfg.get("ytdlp", "")),
        "ffmpeg": _which("ffmpeg", cfg.get("ffmpeg", "")),
        "ffprobe": _which("ffprobe", cfg.get("ffprobe", "")),
        "aria2c": _which("aria2c", cfg.get("aria2c", "")),
    }
def child_kwargs() -> dict:
    """Popen options that keep child processes detached from our stdio.

    yt-dlp/ffmpeg must never read a parent's stdin, and on Windows they should
    not pop a console window.
    """
    kw = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kw
def tool_versions() -> dict:
    found = tools()
    info = {}
    for key, args in (("ytdlp", ["--version"]),
                      ("ffmpeg", ["-version"]),
                      ("ffprobe", ["-version"]),
                      ("aria2c", ["--version"])):
        path = found.get(key, "")
        if not path:
            info[key] = {"path": "", "version": ""}
            continue
        try:
            out = subprocess.run([path] + args, capture_output=True, text=True,
                                 timeout=20, errors="replace",
                                 **child_kwargs()).stdout
            info[key] = {"path": path,
                         "version": out.strip().splitlines()[0] if out else ""}
        except Exception as exc:
            info[key] = {"path": path, "version": "", "error": str(exc)}
    return info
