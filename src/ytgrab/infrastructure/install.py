"""The install folder: versions, backups and swapping files."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

from ytgrab.console import say
from ytgrab.infrastructure.ipc import is_running, request
from ytgrab.infrastructure.tools import child_kwargs
from ytgrab import VERSION
HOST_EXE = "ytgrab-host.exe"
CLI_EXE = "ytgrab.exe"
VERSION_FILE = "version.txt"
NOTES_FILE = "RELEASE-NOTES.md"
SUMS_FILE = "SHA256SUMS.txt"
# files a bundle may carry that belong in the install folder
COPY_FILES = (HOST_EXE, CLI_EXE, VERSION_FILE, "setup.ps1", "install.ps1",
              "uninstall.ps1", NOTES_FILE)


def install_dir() -> str:
    override = os.environ.get("YTGRAB_INSTALL_DIR")
    if override:
        return os.path.abspath(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return os.path.join(local, "ytgrab")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "ytgrab")
def parse_version(text):
    """'v1.2.3' -> (1, 2, 3); anything unparseable -> None."""
    match = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", text or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())
def fmt_version(parts) -> str:
    return ".".join(str(p) for p in parts)
def installed_version() -> str:
    path = os.path.join(install_dir(), VERSION_FILE)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
        if text:
            return text
    except OSError:
        pass
    return VERSION
def write_version(path_dir: str, version: str) -> None:
    os.makedirs(path_dir, exist_ok=True)
    with open(os.path.join(path_dir, VERSION_FILE), "w",
              encoding="utf-8") as fh:
        fh.write(version + "\n")
def cleanup_backups(path_dir: str) -> None:
    """Delete leftover *.old-* files from a previous self-replace."""
    try:
        names = os.listdir(path_dir)
    except OSError:
        return
    for name in names:
        if ".old-" in name:
            try:
                os.remove(os.path.join(path_dir, name))
            except OSError:
                pass                      # still mapped by a running process


def stop_ytgrab() -> None:
    # Ask the queue window to quit first so it writes queue.json cleanly.
    if is_running(timeout=1.0):
        say("  asking the queue window to stop...")
        try:
            request({"cmd": "shutdown"}, timeout=4)
        except Exception:                       # noqa: BLE001
            pass
        time.sleep(0.6)
    if os.name == "nt":
        # Any remaining native-messaging hosts keep the exe locked; Firefox
        # relaunches them on the next request.  Our own image is ytgrab.exe,
        # so this never kills the updater itself.
        subprocess.run(["taskkill", "/IM", HOST_EXE, "/F"],
                       capture_output=True, **child_kwargs())
def copy_replace(src: str, dst: str) -> None:
    """Copy src onto dst, working around a locked (running) destination."""
    try:
        shutil.copy2(src, dst)
        return
    except OSError:
        pass
    stamp = time.strftime("%Y%m%d%H%M%S")
    backup = f"{dst}.old-{stamp}"
    try:
        os.replace(dst, backup)
    except OSError:
        pass
    shutil.copy2(src, dst)
def find_bundle_root(stage: str) -> str:
    wanted = HOST_EXE.lower()
    for root, _dirs, files in os.walk(stage):
        if wanted in [f.lower() for f in files]:
            return root
    return stage
def install_from(src_root: str, version: str) -> None:
    dst = install_dir()
    stop_ytgrab()
    os.makedirs(dst, exist_ok=True)
    for name in COPY_FILES:
        src = os.path.join(src_root, name)
        if os.path.isfile(src):
            copy_replace(src, os.path.join(dst, name))
    firefox = os.path.join(src_root, "lib", "firefox.ps1")
    if os.path.isfile(firefox):
        os.makedirs(os.path.join(dst, "lib"), exist_ok=True)
        copy_replace(firefox, os.path.join(dst, "lib", "firefox.ps1"))
    write_version(dst, version)                 # authoritative record
