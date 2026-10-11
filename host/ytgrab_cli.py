#!/usr/bin/env python3
"""ytgrab command-line tool: check for updates, install them, show what is new.

    ytgrab update          check, download, install, then print the notes
    ytgrab update --check  only report what is available
    ytgrab update --yes    skip the confirmation prompt
    ytgrab notes [VER]     (re)print the notes for a version (default: current)
    ytgrab version         print versions and paths
    ytgrab ui              open (or focus) the download queue window

Updates come from GitHub Releases for <REPO>.  One bundle carries both the
windowless host (ytgrab-host.exe) and this CLI, so `ytgrab update` replaces
both at once.  The notes it prints are the very file the feature PR added to
docs/releases/ -- the release workflow attaches it as the release body, so a
single document serves the release page, the bundle and this command.

Only ever runs when you ask: nothing checks for updates in the background.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

import ytgrab_core as core
import ytgrab_ipc as ipc

APP = core.APP
VERSION = core.VERSION

# GitHub coordinates.  Overridable so tests can point at a local mock server.
REPO = os.environ.get("YTGRAB_REPO", "KSEE-btw/youtubeDownloaderXConverter")
API_BASE = os.environ.get("YTGRAB_API_BASE", "https://api.github.com").rstrip("/")
RAW_BASE = os.environ.get(
    "YTGRAB_RAW_BASE",
    "https://raw.githubusercontent.com/" + REPO + "/main").rstrip("/")
UA = f"{APP}-cli/{VERSION}"

HOST_EXE = "ytgrab-host.exe"
CLI_EXE = "ytgrab.exe"
VERSION_FILE = "version.txt"
NOTES_FILE = "RELEASE-NOTES.md"
SUMS_FILE = "SHA256SUMS.txt"

# files a bundle may carry that belong in the install folder
COPY_FILES = (HOST_EXE, CLI_EXE, VERSION_FILE, "setup.ps1", "install.ps1",
              "uninstall.ps1", NOTES_FILE)


# --------------------------------------------------------------------------- #
# pretty output
# --------------------------------------------------------------------------- #

def _init_console() -> None:
    """Make non-ASCII release notes survive a legacy Windows console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def say(text: str = "") -> None:
    print(text, flush=True)


def head(text: str) -> None:
    say()
    say(f"== {text}")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# HTTP (stdlib only)
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# releases
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# stopping ytgrab before files are swapped
# --------------------------------------------------------------------------- #

def stop_ytgrab() -> None:
    # Ask the queue window to quit first so it writes queue.json cleanly.
    if ipc.is_running(timeout=1.0):
        say("  asking the queue window to stop...")
        try:
            ipc.request({"cmd": "shutdown"}, timeout=4)
        except Exception:                       # noqa: BLE001
            pass
        time.sleep(0.6)
    if os.name == "nt":
        # Any remaining native-messaging hosts keep the exe locked; Firefox
        # relaunches them on the next request.  Our own image is ytgrab.exe,
        # so this never kills the updater itself.
        subprocess.run(["taskkill", "/IM", HOST_EXE, "/F"],
                       capture_output=True, **core.child_kwargs())


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


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_update(opts) -> int:
    current_text = installed_version()
    current = parse_version(current_text)

    head("ytgrab update")
    say(f"  repository : {REPO}")
    say(f"  installed  : {APP} {current_text}  ({install_dir()})")

    try:
        rel = latest_release()
    except urllib.error.URLError as exc:
        say(f"  could not reach {API_BASE}: {exc}")
        return 1

    if rel is None:
        say("  no releases have been published yet -- nothing to update to.")
        return 0

    latest = release_version(rel)
    if latest is None:
        say("  the latest release has no parsable version tag; giving up.")
        return 1
    latest_text = fmt_version(latest)
    say(f"  latest     : {APP} {latest_text}  ({rel.get('name') or rel.get('tag_name')})")

    if current is not None and latest < current:
        say(f"  you are ahead of the latest release ({current_text}); nothing to do.")
        return 0
    if latest == current:
        say(f"  {APP} {current_text} is up to date.")
        return 0

    say()
    if opts.check:
        say(f"  update available: {current_text} -> {latest_text}")
        for ver, body in notes_between(current_text, latest_text):
            say()
            say(f"  --- what's new in {ver} ---")
            for line in body.splitlines():
                say(f"  {line}")
        return 0

    if not opts.yes and sys.stdin.isatty():
        ans = input(f"  Install {APP} {latest_text}? [Y/n] ").strip().lower()
        if ans not in ("", "y", "yes"):
            say("  cancelled.")
            return 0

    zip_name = f"{APP}-{latest_text}-windows.zip"
    asset = find_asset(rel, zip_name)
    if asset is None:
        for candidate in (rel.get("assets") or []):
            if (candidate.get("name") or "").endswith("-windows.zip"):
                asset = candidate
                zip_name = candidate["name"]
                break
    if asset is None:
        say(f"  the release has no bundle asset ({zip_name}); giving up.")
        return 1

    head("downloading")
    tmp = tempfile.mkdtemp(prefix="ytgrab-update-")
    try:
        zip_path = os.path.join(tmp, zip_name)
        download(asset["browser_download_url"], zip_path, zip_name)

        sums_asset = find_asset(rel, SUMS_FILE)
        if sums_asset:
            verify_sums(zip_path, get_text(sums_asset["browser_download_url"]))

        stage = os.path.join(tmp, "bundle")
        os.makedirs(stage, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(stage)
        src_root = find_bundle_root(stage)

        head("installing")
        install_from(src_root, latest_text)
        say(f"  installed {APP} {latest_text} into {install_dir()}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Print the notes now, while the user is still looking at the terminal.
    notes = notes_between(current_text, latest_text)
    head("what's new")
    if not notes:
        say("  (no release notes were published for this version)")
    for ver, body in notes:
        if len(notes) > 1:
            say(f"  --- {ver} ---")
        say()
        say(body or f"  (no notes for {ver})")
    say()
    say("Done. Run 'ytgrab notes' any time to see this again.")
    return 0


def cmd_notes(opts) -> int:
    version_text = opts.version or installed_version()
    wanted = parse_version(version_text)
    rel = None
    if wanted is not None:
        for candidate in all_releases():
            if release_version(candidate) == wanted:
                rel = candidate
                break
    body = notes_for(fmt_version(wanted) if wanted else version_text, rel)
    if not body:
        say(f"No release notes found for {APP} {version_text}.")
        return 1
    say(body)
    return 0


def cmd_version(opts) -> int:
    say(f"{APP} cli    {VERSION}")
    say(f"installed  {installed_version()}")
    say(f"install    {install_dir()}")
    say(f"repo       {REPO}")
    return 0


def cmd_ui(opts) -> int:
    if ipc.is_running(timeout=1.0):
        try:
            ipc.request({"cmd": "show"}, timeout=3)
            return 0
        except ipc.IpcError:
            pass
    exe = os.path.join(install_dir(), HOST_EXE)
    if getattr(sys, "frozen", False) or os.path.isfile(exe):
        subprocess.Popen([exe, "--ui", "--show"], **core.child_kwargs())
        return 0
    # running from a checkout
    here = os.path.dirname(os.path.abspath(__file__))
    subprocess.Popen([sys.executable, os.path.join(here, "ytgrab_host.py"),
                      "--ui", "--show"], **core.child_kwargs())
    return 0


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP, description="ytgrab updates and queue window")
    sub = parser.add_subparsers(dest="command")

    p_update = sub.add_parser("update", help="download and install an update")
    p_update.add_argument("--check", action="store_true",
                          help="only report what is available")
    p_update.add_argument("--yes", "-y", action="store_true",
                          help="do not prompt before installing")

    p_notes = sub.add_parser("notes", help="print a version's release notes")
    p_notes.add_argument("version", nargs="?", default="")

    sub.add_parser("version", help="print versions and paths")
    sub.add_parser("ui", help="open the download queue window")
    return parser


def main(argv=None) -> int:
    _init_console()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        args = ["update"]
    parser = build_parser()
    opts = parser.parse_args(args)

    cleanup_backups(install_dir())

    handlers = {"update": cmd_update, "notes": cmd_notes,
                "version": cmd_version, "ui": cmd_ui}
    handler = handlers.get(opts.command)
    if handler is None:
        parser.print_help()
        return 0
    try:
        return handler(opts)
    except KeyboardInterrupt:
        say("\ncancelled.")
        return 130
    except Exception as exc:                     # noqa: BLE001
        say(f"\n{APP}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
