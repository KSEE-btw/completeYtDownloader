#!/usr/bin/env python3
"""Loopback IPC between the native-messaging host and the queue window.

They are separate processes: Firefox spawns and kills the host, while the
queue window lives on its own so downloads survive the browser.  The window
opens a TCP listener on 127.0.0.1, then writes a small runtime file so the
host can find it:

    %USERPROFILE%\\.config\\ytgrab\\runtime.json
    {"port": 51234, "token": "...", "pid": 1234, "version": "0.3.0"}

Both sides speak newline-delimited JSON.  The token keeps other local
programs from driving the queue.

Standard library only.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time

from ytgrab_core import CONFIG_DIR

RUNTIME_PATH = os.path.join(CONFIG_DIR, "runtime.json")

DEFAULT_TIMEOUT = 5.0


class IpcError(Exception):
    """The queue window is not running, or did not answer."""


def read_runtime():
    try:
        with open(RUNTIME_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("port") and data.get("token"):
            return data
    except Exception:
        return None
    return None


def _write_runtime(data: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = RUNTIME_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, RUNTIME_PATH)


def clear_runtime(expected_pid=None) -> None:
    rt = read_runtime()
    if expected_pid is not None and rt and rt.get("pid") != expected_pid:
        return
    try:
        os.remove(RUNTIME_PATH)
    except OSError:
        pass


def request(payload: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Send one request to a running queue window and return its reply."""
    rt = read_runtime()
    if not rt:
        raise IpcError("the ytgrab queue window is not running")
    body = dict(payload)
    body["token"] = rt["token"]
    try:
        with socket.create_connection(("127.0.0.1", int(rt["port"])),
                                      timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall((json.dumps(body, ensure_ascii=False) + "\n")
                         .encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        if not buf:
            raise IpcError("the queue window did not reply")
        return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise IpcError(str(exc) or "could not reach the queue window")


def is_running(timeout: float = 1.5) -> bool:
    try:
        reply = request({"cmd": "status"}, timeout=timeout)
        return bool(reply.get("ok", False))
    except IpcError:
        return False


def wait_until_running(timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_running():
            return True
        time.sleep(0.25)
    return False


class Server:
    """Threaded JSON-lines server; discoverable through RUNTIME_PATH."""

    def __init__(self, handler, version: str = ""):
        self._handler = handler            # handler(req: dict) -> dict
        self._version = version
        self._sock = None
        self._thread = None
        self._stop = threading.Event()
        self.port = 0
        self.token = ""

    def start(self) -> int:
        self.token = os.urandom(16).hex()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(16)
        self._sock = srv
        self.port = int(srv.getsockname()[1])
        _write_runtime({
            "port": self.port,
            "token": self.token,
            "pid": os.getpid(),
            "version": self._version,
            "started": time.time(),
        })
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self.port

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            threading.Thread(target=self._client, args=(conn,),
                             daemon=True).start()

    def _client(self, conn) -> None:
        try:
            with conn:
                conn.settimeout(30.0)
                fh = conn.makefile("rwb")
                line = fh.readline()
                if not line:
                    return
                try:
                    req = json.loads(line.decode("utf-8"))
                except ValueError:
                    req = {}
                if not isinstance(req, dict) or req.get("token") != self.token:
                    reply = {"ok": False, "error": "bad token"}
                else:
                    try:
                        reply = self._handler(req) or {"ok": True}
                    except Exception as exc:  # noqa: BLE001
                        reply = {"ok": False, "error": str(exc)}
                fh.write((json.dumps(reply, ensure_ascii=False) + "\n")
                         .encode("utf-8"))
                fh.flush()
        except OSError:
            pass

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        clear_runtime(expected_pid=os.getpid())
