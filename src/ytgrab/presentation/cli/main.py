"""The `ytgrab` console tool."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import zipfile

from ytgrab.console import _init_console, head, say
from ytgrab.infrastructure.config import DEFAULT_CONFIG, load_config, save_config
from ytgrab.infrastructure.install import HOST_EXE, SUMS_FILE, cleanup_backups, find_bundle_root, fmt_version, install_dir, install_from, installed_version, parse_version
from ytgrab.infrastructure.ipc import IpcError, is_running, request
from ytgrab.infrastructure.net import API_BASE, REPO, download, get_text, verify_sums
from ytgrab.infrastructure.tools import child_kwargs
from ytgrab.services.releases import all_releases, find_asset, latest_release, notes_between, notes_for, release_version
from ytgrab import APP, SRC_ROOT, VERSION
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
    if is_running(timeout=1.0):
        try:
            request({"cmd": "show"}, timeout=3)
            return 0
        except IpcError:
            pass
    exe = os.path.join(install_dir(), HOST_EXE)
    if getattr(sys, "frozen", False) or os.path.isfile(exe):
        subprocess.Popen([exe, "--ui", "--show"], **child_kwargs())
        return 0
    # running from a checkout
    subprocess.Popen([sys.executable, "-m", "ytgrab.entrypoints.host",
                      "--ui", "--show"], cwd=SRC_ROOT, **child_kwargs())
    return 0
def _parse_setting_value(text: str):
    """JSON when it parses (true / 18 / [] / "x"), otherwise the raw string."""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text
def cmd_config(opts) -> int:
    cfg = load_config()
    if not opts.key:
        say(json.dumps(cfg, indent=2, ensure_ascii=False))
        return 0
    if opts.key not in DEFAULT_CONFIG:
        say(f"{APP}: unknown setting '{opts.key}'")
        say(f"  known: {', '.join(DEFAULT_CONFIG)}")
        return 1
    if opts.value is None:
        value = cfg[opts.key]
        say(json.dumps(value, ensure_ascii=False)
            if isinstance(value, (dict, list)) else str(value))
        return 0
    save_config({opts.key: _parse_setting_value(opts.value)})
    value = load_config()[opts.key]
    say(f"{opts.key} = {json.dumps(value, ensure_ascii=False)}")
    return 0
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

    p_config = sub.add_parser("config", help="show or change stored settings")
    p_config.add_argument("key", nargs="?", default="",
                          help="setting name (omit to show them all)")
    p_config.add_argument("value", nargs="?", default=None,
                          help="new value (omit to print the current one)")
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
                "version": cmd_version, "ui": cmd_ui, "config": cmd_config}
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
