"""Running and stopping child processes."""

from __future__ import annotations

import os
import subprocess

from ytgrab.infrastructure.tools import child_kwargs, tools
from ytgrab.log import log



from typing import TYPE_CHECKING

if TYPE_CHECKING:                      # annotation only, no runtime import
    from ytgrab.services.job import Job  # noqa: F401
def kill_tree(proc) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, **child_kwargs())
        else:
            proc.terminate()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _ffmpeg_run(job: Job, args, total_seconds: float, phase: str, emit) -> None:
    exe = tools()["ffmpeg"]
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-nostats",
           "-progress", "pipe:1"] + args
    log("ffmpeg:", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1,
                            encoding="utf-8", errors="replace",
                            **child_kwargs())
    job.attach(proc)
    for line in proc.stdout:
        line = line.strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key in ("out_time_us", "out_time_ms"):
            try:
                seconds = int(value) / 1_000_000
            except ValueError:
                continue
            pct = (seconds / total_seconds * 100) if total_seconds else 0
            emit({"type": "progress", "phase": phase,
                  "percent": max(0.0, min(100.0, pct)), "seconds": seconds,
                  "total": total_seconds})
        elif key == "progress" and value == "end":
            emit({"type": "progress", "phase": phase, "percent": 100.0,
                  "seconds": total_seconds, "total": total_seconds})
    proc.wait()
    err = (proc.stderr.read() or "").strip().splitlines()
    if proc.returncode != 0:
        raise RuntimeError(err[-1] if err else "ffmpeg failed")


# Windows process creation flags for a detached, window-less child
CREATE_DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
