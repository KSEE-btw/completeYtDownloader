#!/usr/bin/env python3
"""ytgrab native-messaging host.

A thin bridge between the Firefox extension and the queue window.  Firefox
spawns this process and kills it when the browser closes, so it does not own
the downloads: it forwards each request to the queue window over loopback IPC
(ytgrab_ipc) and starts that window on demand.  That is what lets a download
survive closing the browser popup -- and the browser itself.

It speaks the native messaging protocol on stdin/stdout:

    <uint32 little-endian length><utf-8 json>

Commands from the extension
---------------------------
    {"cmd": "ping",         "id": "..."}
    {"cmd": "list_formats", "id": "...", "url": "..."}
    {"cmd": "download",     "id": "...", "url": "...", "format": "...",
     "convert": true, "target": "h264|dnxhr|prores",
     "outdir": "...", "keep_original": false, "title": "..."}
    {"cmd": "cancel",       "id": "...", "job": "..."}
    {"cmd": "open_ui",      "id": "..."}
    {"cmd": "queue_status", "id": "..."}
    {"cmd": "get_config",   "id": "..."}
    {"cmd": "set_config",   "id": "...", "config": {...}}
    {"cmd": "choose_folder","id": "...", "start": "..."}

Everything the host sends back carries the id it is answering.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

import ytgrab_core as core
import ytgrab_ipc as ipc

APP = core.APP
VERSION = core.VERSION

# native messaging messages are capped at 1 MiB
MAX_MSG = 1024 * 1024 - 16

# fields the queue window accepts for a new download
ENQUEUE_FIELDS = ("url", "format", "mode", "convert", "target", "outdir",
                  "keep_original", "title",
                  "clip_start", "clip_end", "force_cfr", "audio_48k")

# Windows process creation flags for a detached, window-less child
CREATE_DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

DEBUG = core.DEBUG

# --------------------------------------------------------------------------- #
# logging / stdio plumbing
# --------------------------------------------------------------------------- #

if os.name == "nt":
    import msvcrt
    for _stream in (sys.stdin, sys.stdout):
        try:
            msvcrt.setmode(_stream.fileno(), os.O_BINARY)
        except (OSError, ValueError, AttributeError):
            pass

_write_lock = threading.Lock()


def log(*parts: object) -> None:
    if DEBUG:
        print("[ytgrab]", *parts, file=sys.stderr, flush=True)


def read_message():
    """Read one native-messaging message, or None at end of stream."""
    header = sys.stdin.buffer.read(4)
    if len(header) < 4:
        return None
    size = int.from_bytes(header, "little")
    if size <= 0:
        return None
    buf = bytearray()
    while len(buf) < size:
        chunk = sys.stdin.buffer.read(size - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    try:
        return json.loads(buf.decode("utf-8"))
    except Exception as exc:  # pragma: no cover - malformed input
        log("bad json:", exc)
        return {}


def send(obj: dict) -> None:
    """Write one native-messaging message."""
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_MSG:
        payload = json.dumps(
            {"type": "error", "message": "response too large"})
        payload = payload.encode("utf-8")
    with _write_lock:
        try:
            sys.stdout.buffer.write(len(payload).to_bytes(4, "little"))
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()
        except (BrokenPipeError, OSError):
            log("stdout closed")
            os._exit(0)


def reply(msg_id, **fields) -> None:
    out = dict(fields)
    out["id"] = msg_id          # the request id always wins
    send(out)


def fail(msg_id, message: str, **fields) -> None:
    out = {"type": "error", "id": msg_id, "message": message}
    out.update(fields)
    send(out)


# --------------------------------------------------------------------------- #
# starting / talking to the queue window
# --------------------------------------------------------------------------- #

def _launch_ui(show: bool = False) -> bool:
    """Start the queue window detached so it outlives this host process."""
    args = ["--ui"] + (["--show"] if show else [])
    if getattr(sys, "frozen", False):
        cmd = [sys.executable] + args
        workdir = os.path.dirname(sys.executable)
    else:
        exe = sys.executable
        pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(pythonw):
            exe = pythonw
        cmd = [exe, os.path.abspath(__file__)] + args
        workdir = os.path.dirname(os.path.abspath(__file__))
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
          "stderr": subprocess.DEVNULL, "close_fds": True, "cwd": workdir}
    if os.name == "nt":
        kw["creationflags"] = (CREATE_DETACHED_PROCESS
                               | CREATE_NEW_PROCESS_GROUP)
    try:
        subprocess.Popen(cmd, **kw)
        return True
    except Exception as exc:  # noqa: BLE001
        log("could not launch the queue window:", exc)
        return False


def ensure_ui(timeout: float = 15.0) -> bool:
    if ipc.is_running():
        return True
    if not _launch_ui():
        return False
    return ipc.wait_until_running(timeout)


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

def handle(msg: dict) -> None:
    cmd = msg.get("cmd")
    msg_id = msg.get("id")

    if cmd == "ping":
        info = core.tool_versions()
        reply(msg_id, type="pong", app=APP, version=VERSION,
              tools=info, config=core.load_config(),
              targets={k: v["label"] for k, v in core.TARGETS.items()},
              ok=bool(info["ytdlp"]["path"] and info["ffmpeg"]["path"]),
              ui_running=ipc.is_running(timeout=0.8))
        return

    if cmd == "get_config":
        reply(msg_id, type="config", config=core.load_config())
        return

    if cmd == "set_config":
        reply(msg_id, type="config",
              config=core.save_config(msg.get("config") or {}))
        return

    if cmd == "choose_folder":
        # Open the OS folder picker from here, not the popup: a browser page
        # cannot see real paths, and this keeps the dialog alive even when the
        # popup closes on blur.  The choice is persisted as the new default.
        start = core.expand(msg.get("start")
                            or core.load_config().get("outdir")
                            or core.DEFAULT_CONFIG["outdir"])
        try:
            path = core.choose_folder(start)
        except Exception as exc:  # noqa: BLE001
            fail(msg_id, f"could not open the folder picker: {exc}")
            return
        if not path:
            reply(msg_id, type="folder", cancelled=True)
            return
        reply(msg_id, type="folder", path=path,
              config=core.save_config({"outdir": path}))
        return

    if cmd == "list_formats":
        try:
            info = core.run_ytdlp_json(msg.get("url", ""))
            reply(msg_id, type="formats", **core.simplify_formats(info))
        except Exception as exc:  # noqa: BLE001
            fail(msg_id, str(exc) or exc.__class__.__name__)
        return

    if cmd == "reveal":
        path = core.expand(msg.get("path") or "")
        try:
            if os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", path],
                                 **core.child_kwargs())
            elif os.path.isdir(path):
                subprocess.Popen(["explorer", path], **core.child_kwargs())
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
                 "could not start the ytgrab queue window. Re-run "
                 "install.ps1, then try again.")
            return
        try:
            resp = ipc.request({"cmd": "enqueue", **spec}, timeout=15)
        except ipc.IpcError as exc:
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
        if ipc.is_running():
            try:
                ok = bool(ipc.request({"cmd": "cancel", "job": job_id},
                                      timeout=5).get("ok"))
            except ipc.IpcError:
                ok = False
        reply(msg_id, type="cancelled", job=job_id, ok=ok)
        return

    if cmd == "open_ui":
        opened = False
        if ipc.is_running():
            try:
                opened = bool(ipc.request({"cmd": "show"},
                                          timeout=5).get("ok"))
            except ipc.IpcError:
                opened = False
        else:
            opened = _launch_ui(show=True) and ipc.wait_until_running(15)
        reply(msg_id, type="ok", opened=opened)
        return

    if cmd == "queue_status":
        running = ipc.is_running(timeout=0.8)
        counts = {}
        if running:
            try:
                counts = ipc.request({"cmd": "status"}).get("counts") or {}
            except ipc.IpcError:
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


# --------------------------------------------------------------------------- #
# small CLI for testing without Firefox
# --------------------------------------------------------------------------- #

def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="ytgrab-host", description="ytgrab native messaging host")
    parser.add_argument("--ping", action="store_true",
                        help="print tool/config status and exit")
    parser.add_argument("--list", metavar="URL",
                        help="list formats for URL as JSON")
    parser.add_argument("--config-path", action="store_true")
    args = parser.parse_args()
    if args.config_path:
        print(core.CONFIG_PATH)
        return 0
    if args.ping:
        print(json.dumps({"tools": core.tool_versions(),
                          "config": core.load_config()},
                         indent=2, ensure_ascii=False))
        return 0
    if args.list:
        print(json.dumps(core.simplify_formats(core.run_ytdlp_json(args.list)),
                         indent=2, ensure_ascii=False))
        return 0
    parser.print_help()
    return 0


CLI_FLAGS = ("--ping", "--list", "--config-path", "-h", "--help")

# Flags that mean "run the queue window instead of the messaging host".
UI_FLAGS = ("--ui", "--headless", "--smoke", "--show",
            "--self-test-url", "--self-test-format", "--self-test-mode")


if __name__ == "__main__":
    # The queue window is the same executable in another mode.
    if any(arg in UI_FLAGS for arg in sys.argv[1:]):
        import ytgrab_ui
        sys.exit(ytgrab_ui.main(sys.argv[1:]))

    # Firefox launches the host with its own arguments (on Windows the parent
    # window handle), so "any argv present means CLI" is wrong: the process
    # would run the test CLI, argparse would reject the argument, and the host
    # would exit instantly -- which the extension sees as "host disconnected".
    # Only use the CLI when one of our own flags is actually present.
    if any(arg in CLI_FLAGS for arg in sys.argv[1:]):
        sys.exit(_cli())
    main()
