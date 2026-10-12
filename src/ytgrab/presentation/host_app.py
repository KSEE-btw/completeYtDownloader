"""The native-messaging host: dispatch and the test CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

from ytgrab.domain.encode import TARGETS
from ytgrab.domain.formats import simplify_formats
from ytgrab.infrastructure.config import CONFIG_PATH, DEFAULT_CONFIG, choose_folder, load_config, save_config
from ytgrab.infrastructure.files import expand
from ytgrab.infrastructure.ipc import IpcError, is_running, request, wait_until_running
from ytgrab.infrastructure.native_messaging import fail, read_message, reply
from ytgrab.infrastructure.tools import child_kwargs, tool_versions
from ytgrab.log import log
from ytgrab.protocol import ENQUEUE_FIELDS
from ytgrab.services.catalog import run_ytdlp_json
from ytgrab.services.ui_launcher import _launch_ui, ensure_ui
from ytgrab import APP, NAME, VERSION



def run() -> None:
    """Dispatch argv: another mode of this executable, or the host loop."""
    # The queue window is the same executable in another mode.
    if any(arg in UI_FLAGS for arg in sys.argv[1:]):
        from ytgrab.presentation.ui import modes as ui_mod
        sys.exit(ui_mod.main(sys.argv[1:]))

    # Firefox launches the host with its own arguments (on Windows the
    # parent window handle), so "any argv present means CLI" is wrong: the
    # process would run the test CLI, argparse would reject the argument, and
    # the host would exit instantly -- which the extension sees as
    # "host disconnected".  Only use the CLI when one of our flags is present.
    if any(arg in CLI_FLAGS for arg in sys.argv[1:]):
        sys.exit(_cli())
    main()
def handle(msg: dict) -> None:
    cmd = msg.get("cmd")
    msg_id = msg.get("id")

    if cmd == "ping":
        info = tool_versions()
        reply(msg_id, type="pong", app=APP, version=VERSION,
              tools=info, config=load_config(),
              targets={k: v["label"] for k, v in TARGETS.items()},
              ok=bool(info["ytdlp"]["path"] and info["ffmpeg"]["path"]),
              ui_running=is_running(timeout=0.8))
        return

    if cmd == "get_config":
        reply(msg_id, type="config", config=load_config())
        return

    if cmd == "set_config":
        reply(msg_id, type="config",
              config=save_config(msg.get("config") or {}))
        return

    if cmd == "choose_folder":
        # Open the OS folder picker from here, not the popup: a browser page
        # cannot see real paths, and this keeps the dialog alive even when the
        # popup closes on blur.  The choice is persisted as the new default.
        start = expand(msg.get("start")
                            or load_config().get("outdir")
                            or DEFAULT_CONFIG["outdir"])
        try:
            path = choose_folder(start)
        except Exception as exc:  # noqa: BLE001
            fail(msg_id, f"could not open the folder picker: {exc}")
            return
        if not path:
            reply(msg_id, type="folder", cancelled=True)
            return
        reply(msg_id, type="folder", path=path,
              config=save_config({"outdir": path}))
        return

    if cmd == "list_formats":
        try:
            info = run_ytdlp_json(msg.get("url", ""))
            reply(msg_id, type="formats", **simplify_formats(info))
        except Exception as exc:  # noqa: BLE001
            fail(msg_id, str(exc) or exc.__class__.__name__)
        return

    if cmd == "reveal":
        path = expand(msg.get("path") or "")
        try:
            if os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", path],
                                 **child_kwargs())
            elif os.path.isdir(path):
                subprocess.Popen(["explorer", path], **child_kwargs())
            else:
                raise RuntimeError("path does not exist")
            reply(msg_id, type="ok")
        except Exception as exc:  # noqa: BLE001
            fail(msg_id, str(exc))
        return

    if cmd == "download":
        spec = {key: msg.get(key) for key in ENQUEUE_FIELDS}
        if not (spec.get("url") or "").strip():
            fail(msg_id, "no URL given")
            return
        if not ensure_ui():
            fail(msg_id,
                 f"could not start the {NAME} queue window. Re-run "
                 "install.ps1, then try again.")
            return
        try:
            resp = request({"cmd": "enqueue", **spec}, timeout=15)
        except IpcError as exc:
            fail(msg_id, f"the queue window did not accept the download: {exc}")
            return
        if not resp.get("ok"):
            fail(msg_id, resp.get("error") or "could not queue the download")
            return
        reply(msg_id, type="queued", job=resp.get("job"),
              url=spec.get("url"), title=spec.get("title"))
        return

    if cmd == "cancel":
        job_id = msg.get("job") or msg_id
        ok = False
        if is_running():
            try:
                ok = bool(request({"cmd": "cancel", "job": job_id},
                                      timeout=5).get("ok"))
            except IpcError:
                ok = False
        reply(msg_id, type="cancelled", job=job_id, ok=ok)
        return

    if cmd == "open_ui":
        opened = False
        if is_running():
            try:
                opened = bool(request({"cmd": "show"},
                                          timeout=5).get("ok"))
            except IpcError:
                opened = False
        else:
            opened = _launch_ui(show=True) and wait_until_running(15)
        reply(msg_id, type="ok", opened=opened)
        return

    if cmd == "queue_status":
        running = is_running(timeout=0.8)
        counts = {}
        if running:
            try:
                counts = request({"cmd": "status"}).get("counts") or {}
            except IpcError:
                running = False
        reply(msg_id, type="queue", running=running, counts=counts)
        return

    fail(msg_id, f"unknown command: {cmd}")
def _dispatch(msg: dict) -> None:
    try:
        handle(msg)
    except Exception as exc:  # noqa: BLE001
        log("handler crashed:", repr(exc))
        fail(msg.get("id"), str(exc))
# Commands that can block for a long time: a yt-dlp probe, or starting / talking
# to the queue window.  They run on their own thread so a slow one never delays
# the ping the popup uses to show the host version -- otherwise the popup looks
# stuck on "Reading available formats..." and the version check at the same
# time.  choose_folder stays on the main thread because Tk wants to own it.
_SLOW_COMMANDS = frozenset({"list_formats", "download", "open_ui"})
def main() -> None:
    log(f"{APP} host {VERSION} starting")
    while True:
        try:
            msg = read_message()
        except Exception as exc:  # pragma: no cover
            log("read failed:", exc)
            break
        if msg is None:
            break
        if not isinstance(msg, dict):
            continue
        if msg.get("cmd") in _SLOW_COMMANDS:
            threading.Thread(target=_dispatch, args=(msg,), daemon=True).start()
        else:
            _dispatch(msg)
    log("stdin closed, exiting")
def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="ytgrab-host", description=f"{NAME} native messaging host")
    parser.add_argument("--ping", action="store_true",
                        help="print tool/config status and exit")
    parser.add_argument("--list", metavar="URL",
                        help="list formats for URL as JSON")
    parser.add_argument("--config-path", action="store_true")
    args = parser.parse_args()
    if args.config_path:
        print(CONFIG_PATH)
        return 0
    if args.ping:
        print(json.dumps({"tools": tool_versions(),
                          "config": load_config()},
                         indent=2, ensure_ascii=False))
        return 0
    if args.list:
        print(json.dumps(simplify_formats(run_ytdlp_json(args.list)),
                         indent=2, ensure_ascii=False))
        return 0
    parser.print_help()
    return 0
CLI_FLAGS = ("--ping", "--list", "--config-path", "-h", "--help")
# Flags that mean "run the queue window instead of the messaging host".
UI_FLAGS = ("--ui", "--headless", "--smoke", "--show",
            "--self-test-url", "--self-test-format", "--self-test-mode")
