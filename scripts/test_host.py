#!/usr/bin/env python3
"""Tiny native-messaging client, for testing the ytgrab host without Firefox.

    python scripts/test_host.py                # ping
    python scripts/test_host.py URL            # list formats
    python scripts/test_host.py URL dl 137     # queue a download (format 137)
    python scripts/test_host.py ui             # open/focus the queue window
    python scripts/test_host.py qs             # queue status

A download is handed to the queue window, so the reply is a short "queued"
message rather than a stream of progress.
"""
import json
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
HOST = os.path.join(SRC, "ytgrab", "entrypoints", "host.py")
PYTHON = os.environ.get("YTGRAB_PYTHON", sys.executable)


def send(proc, obj):
    data = json.dumps(obj).encode("utf-8")
    proc.stdin.write(len(data).to_bytes(4, "little"))
    proc.stdin.write(data)
    proc.stdin.flush()


def reader(proc):
    while True:
        header = proc.stdout.read(4)
        if len(header) < 4:
            print("[stream closed]")
            return
        size = int.from_bytes(header, "little")
        body = proc.stdout.read(size)
        msg = json.loads(body.decode("utf-8"))
        print(json.dumps(msg, indent=2, ensure_ascii=False))


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    env = dict(os.environ, PYTHONPATH=os.environ.get("PYTHONPATH", "") +
               (os.pathsep + SRC if os.environ.get("PYTHONPATH") else SRC))
    proc = subprocess.Popen([PYTHON, HOST], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, env=env)
    threading.Thread(target=reader, args=(proc,), daemon=True).start()

    if not url:
        send(proc, {"cmd": "ping", "id": "p1"})
    elif url == "ui":
        send(proc, {"cmd": "open_ui", "id": "u1"})
    elif url == "qs":
        send(proc, {"cmd": "queue_status", "id": "q1"})
    elif len(sys.argv) > 2 and sys.argv[2] == "dl":
        send(proc, {"cmd": "download", "id": "d1", "url": url,
                    "format": sys.argv[3] if len(sys.argv) > 3 else "",
                    "convert": True, "target": "h264",
                    "outdir": os.environ.get("YTGRAB_OUT", os.path.join(
                        os.path.expanduser("~"), "Downloads"))})
    else:
        send(proc, {"cmd": "list_formats", "id": "l1", "url": url})

    input("press enter to quit\n")
    proc.kill()


if __name__ == "__main__":
    main()
