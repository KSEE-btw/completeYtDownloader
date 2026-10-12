"""Headless, smoke and self-test modes of the queue window."""

from __future__ import annotations

import json
import os
import sys
import time

from ytgrab.infrastructure.ipc import IpcError, Server, is_running, request
from ytgrab.log import log
from ytgrab.presentation.ui.formatting import detail_text
from ytgrab.presentation.ui.toolkit import TK_OK
from ytgrab.services.commands import make_handler
from ytgrab.services.queue import QUEUE_PATH, QueueManager
from ytgrab import VERSION



# The window is optional: --headless and --smoke run without tkinter at all.
if TK_OK:
    from ytgrab.presentation.ui.window import QueueWindow  # noqa: F401
def _run_headless(qm: QueueManager) -> int:
    def on_command(cmd: str) -> None:
        if cmd in ("quit", "shutdown"):
            qm.request_stop()
    server = Server(make_handler(qm, on_command), version=VERSION)
    server.start()
    log("headless queue on port", server.port)
    try:
        while not qm.stopped():
            time.sleep(0.4)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        qm.shutdown()
    return 0
def _parse_clip(text: str):
    """ "0:05-0:25" -> ("0:05", "0:25"); either side may be left blank."""
    head, _, tail = (text or "").partition("-")
    return head.strip(), tail.strip()
def _self_test(url: str, fmt: str = "", mode: str = "premiere_safe",
               clip: str = "") -> int:
    """End-to-end check: queue + worker + IPC, with no window."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="ytgrab-selftest-")
    qm = QueueManager(os.path.join(tmp, "queue.json"))
    server = Server(make_handler(qm, lambda _c: None),
                        version=VERSION)
    server.start()
    print(f"server on 127.0.0.1:{server.port}, queue in {tmp}")

    clip_start, clip_end = _parse_clip(clip)
    job = request({"cmd": "enqueue", "url": url, "format": fmt,
                       "mode": mode, "convert": True,
                       "target": "h264", "outdir": tmp, "keep_original": False,
                       "title": "self-test",
                       "clip_start": clip_start, "clip_end": clip_end,
                       "force_cfr": False, "audio_48k": False})
    print("enqueue ->", job)
    print("status  ->", request({"cmd": "status"}))

    deadline = time.monotonic() + 600
    last = None
    while time.monotonic() < deadline:
        items = request({"cmd": "list"}).get("items", [])
        if items:
            item = items[0]
            line = f"  {item['status']:<10} {item['percent']:5.1f}%  {detail_text(item)}"
            if line != last:
                print(line, flush=True)
                last = line
            if item["status"] in ("done", "error", "cancelled"):
                break
        time.sleep(0.5)

    final = request({"cmd": "list"}).get("items", [])
    print("\nfinal:", json.dumps(final, indent=2)[:2000])
    server.stop()
    qm.shutdown()
    ok = bool(final) and final[0]["status"] == "done"
    print("\nSELF-TEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1
def _smoke() -> int:
    if not TK_OK:
        print("tkinter unavailable; cannot smoke-test the window")
        return 1
    import tempfile
    tmp = tempfile.mkdtemp(prefix="ytgrab-smoke-")
    qm = QueueManager(os.path.join(tmp, "queue.json"))
    root = QueueWindow(qm, show=False)
    root.withdraw()
    for _ in range(6):
        root.update()
        time.sleep(0.06)
    root._force_quit()
    qm.shutdown()
    print("SMOKE PASS")
    return 0
def main(args=None) -> int:
    args = list(args if args is not None else sys.argv[1:])
    headless = "--headless" in args
    show = "--show" in args

    # testing hooks, no window
    if "--self-test-url" in args:
        def value(flag, default=""):
            if flag in args:
                i = args.index(flag)
                if i + 1 < len(args):
                    return args[i + 1]
            return default
        return _self_test(value("--self-test-url"),
                          fmt=value("--self-test-format"),
                          mode=value("--self-test-mode", "premiere_safe"),
                          clip=value("--self-test-clip"))
    if "--smoke" in args:
        return _smoke()

    # single instance: if one is already running, just show it and leave
    if is_running():
        if show:
            try:
                request({"cmd": "show"})
            except IpcError:
                pass
        return 0

    qm = QueueManager(QUEUE_PATH)

    if headless or not TK_OK:
        if not TK_OK and not headless:
            log("tkinter unavailable; running headless")
        return _run_headless(qm)

    root = QueueWindow(qm, show=show)
    server = Server(make_handler(qm, root.post_command),
                        version=VERSION)
    server.start()
    try:
        root.mainloop()
    finally:
        server.stop()
        qm.shutdown()
    return 0
