"""Starting the queue window as a detached process."""

from __future__ import annotations

import os
import subprocess
import sys

from ytgrab.infrastructure.ipc import is_running, wait_until_running
from ytgrab.infrastructure.process import CREATE_DETACHED_PROCESS, CREATE_NEW_PROCESS_GROUP
from ytgrab.log import log
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
    if is_running():
        return True
    if not _launch_ui():
        return False
    return wait_until_running(timeout)
