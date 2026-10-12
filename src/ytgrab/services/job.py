"""A cancellable unit of work and the child process it runs."""

from __future__ import annotations

import threading

from ytgrab.infrastructure.process import kill_tree
class Job:
    """A cancellable unit of work; holds the child process it is running."""

    def __init__(self, job_id: str):
        self.id = job_id
        self.proc = None
        self.cancelled = False
        self.lock = threading.Lock()

    def attach(self, proc):
        with self.lock:
            self.proc = proc
            if self.cancelled and proc and proc.poll() is None:
                kill_tree(proc)

    def cancel(self):
        self.cancelled = True
        with self.lock:
            proc = self.proc
        if proc and proc.poll() is None:
            kill_tree(proc)
