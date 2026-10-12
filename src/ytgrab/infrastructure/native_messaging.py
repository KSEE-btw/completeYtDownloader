"""The native-messaging framing Firefox speaks."""

from __future__ import annotations

import json
import os
import sys
import threading

from ytgrab.log import log
# native messaging messages are capped at 1 MiB
MAX_MSG = 1024 * 1024 - 16


if os.name == "nt":
    import msvcrt
    for _stream in (sys.stdin, sys.stdout):
        try:
            msvcrt.setmode(_stream.fileno(), os.O_BINARY)
        except (OSError, ValueError, AttributeError):
            pass
_write_lock = threading.Lock()


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
