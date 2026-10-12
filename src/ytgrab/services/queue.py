"""The download queue and its worker pool."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

from ytgrab.domain.models import Item
from ytgrab.errors import Cancelled
from ytgrab.infrastructure.config import CONFIG_DIR, load_config, save_config
from ytgrab.log import DEBUG, log
from ytgrab.services.downloader import run_download
from ytgrab.services.job import Job
QUEUE_PATH = os.environ.get("YTGRAB_QUEUE_PATH") or os.path.join(
    CONFIG_DIR, "queue.json")
HISTORY_LIMIT = 200
# the worker pool is always this big; max_concurrent gates how many may run
MAX_PARALLEL = 8


class QueueManager:
    """Owns the items and a pool of workers that drains the queue.

    The pool always holds MAX_PARALLEL idle threads; how many may run at once
    is the ``max_concurrent`` setting, and it can change while the queue is
    live.  Order matters: a worker always claims the earliest queued item, so
    the queue stays first-in-first-out however many run in parallel.
    """

    def __init__(self, path: str = QUEUE_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._save_lock = threading.Lock()   # one writer at a time
        self.items: dict = {}          # id -> Item, in queue order
        self._jobs: dict = {}
        self._stop = False
        try:
            start = int(load_config().get("max_concurrent") or 2)
        except (TypeError, ValueError):
            start = 2
        self._max_concurrent = max(1, min(MAX_PARALLEL, start))
        self.load()
        self._workers = [
            threading.Thread(target=self._loop, name=f"ytgrab-q{i}",
                             daemon=True)
            for i in range(MAX_PARALLEL)
        ]
        for worker in self._workers:
            worker.start()

    # --- persistence ---

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, ValueError, OSError):
            return
        for raw in (data.get("items") if isinstance(data, dict) else None) or []:
            try:
                item = Item.from_dict(raw)
            except Exception:
                continue
            self.items[item.id] = item

    def save(self) -> None:
        with self._lock:
            self._prune()
            payload = {"version": 1,
                       "items": [it.to_dict() for it in self.items.values()]}
        # with several workers running, more than one can finish at the same
        # instant -- keep the write to the shared .tmp file single-file
        with self._save_lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=1)
                os.replace(tmp, self.path)
            except OSError:
                pass

    def _prune(self) -> None:
        if len(self.items) <= HISTORY_LIMIT:
            return
        finished = ("done", "error", "cancelled")
        for item_id in list(self.items):
            if len(self.items) <= HISTORY_LIMIT:
                break
            if self.items[item_id].status in finished:
                del self.items[item_id]

    # --- mutations ---

    def add(self, spec: dict) -> str:
        with self._lock:
            item = Item(uuid.uuid4().hex, spec)
            self.items[item.id] = item
            self._cond.notify_all()
        self.save()
        return item.id

    def cancel(self, item_id: str) -> bool:
        with self._lock:
            item = self.items.get(item_id)
            if item is None or item.status not in ("queued", "running"):
                return False
            item.status = "cancelled"
            item.finished = time.time()
            job = self._jobs.get(item_id)
        self.save()
        if job is not None:
            job.cancel()
        return True

    def retry(self, item_id: str) -> bool:
        with self._lock:
            item = self.items.get(item_id)
            if item is None or item.status not in ("error", "cancelled"):
                return False
            item.status = "queued"
            item.stage = item.stage_label = item.error = ""
            item.percent = item.speed = item.eta = 0.0
            item.downloaded = item.total = 0.0
            item.finished = None
            self._cond.notify_all()
        self.save()
        return True

    def remove(self, item_id: str) -> bool:
        with self._lock:
            item = self.items.get(item_id)
            if item is None:
                return False
            job = self._jobs.get(item_id)
            if item.status == "running" and job is not None:
                item.status = "cancelled"
            del self.items[item_id]
        if job is not None:
            job.cancel()
        self.save()
        return True

    def pause(self, item_id: str) -> bool:
        """Hold an item: a running download stops mid-file so a resume can pick
        up the part file, a waiting one simply stays where it is."""
        with self._lock:
            item = self.items.get(item_id)
            if item is None or item.status not in ("queued", "running"):
                return False
            item.status = "paused"
            item.stage = ""
            item.stage_label = "Paused"
            item.speed = item.eta = 0.0
            job = self._jobs.get(item_id)
        self.save()
        if job is not None:
            job.cancel()
        return True

    def resume(self, item_id: str) -> bool:
        with self._lock:
            item = self.items.get(item_id)
            if item is None or item.status != "paused":
                return False
            item.status = "queued"
            item.stage_label = ""
            item.error = ""
            item.finished = None
            self._cond.notify_all()
        self.save()
        return True

    def pause_all(self) -> int:
        """Hold the whole queue: running items stop, waiting ones keep place."""
        with self._lock:
            jobs = []
            n = 0
            for item in self.items.values():
                if item.status == "running":
                    item.status = "paused"
                    item.stage = ""
                    item.stage_label = "Paused"
                    item.speed = item.eta = 0.0
                    n += 1
                    job = self._jobs.get(item.id)
                    if job is not None:
                        jobs.append(job)
                elif item.status == "queued":
                    item.status = "paused"
                    item.stage_label = "Paused"
                    n += 1
        if n:
            self.save()
        for job in jobs:
            job.cancel()
        return n

    def resume_all(self) -> int:
        with self._lock:
            n = 0
            for item in self.items.values():
                if item.status == "paused":
                    item.status = "queued"
                    item.stage_label = ""
                    item.error = ""
                    item.finished = None
                    n += 1
            if n:
                self._cond.notify_all()
        if n:
            self.save()
        return n

    def move(self, item_id: str, delta: int) -> bool:
        """Nudge an item one slot up (-1) or down (+1) the queue."""
        with self._lock:
            ids = list(self.items)
            if item_id not in ids:
                return False
            pos = ids.index(item_id)
            target = pos + (1 if delta > 0 else -1)
            if target < 0 or target >= len(ids):
                return False
            ids[pos], ids[target] = ids[target], ids[pos]
            self.items = {i: self.items[i] for i in ids}
        self.save()
        return True

    def cancel_many(self, item_ids) -> int:
        return sum(1 for i in list(item_ids or []) if self.cancel(i))

    def remove_many(self, item_ids) -> int:
        return sum(1 for i in list(item_ids or []) if self.remove(i))

    def max_concurrent(self) -> int:
        with self._lock:
            return self._max_concurrent

    def set_max_concurrent(self, value) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            n = 2
        with self._lock:
            self._max_concurrent = max(1, min(MAX_PARALLEL, n))
            self._cond.notify_all()
            result = self._max_concurrent
        self.save()
        try:                    # remember the choice for the next window
            save_config({"max_concurrent": result})
        except Exception as exc:  # noqa: BLE001
            log("could not save max_concurrent:", exc)
        return result

    def clear_finished(self) -> int:
        with self._lock:
            done = [i for i, it in self.items.items()
                    if it.status in ("done", "error", "cancelled")]
            for item_id in done:
                del self.items[item_id]
        if done:
            self.save()
        return len(done)

    def request_stop(self) -> None:
        with self._lock:
            self._stop = True
            self._cond.notify_all()

    def stopped(self) -> bool:
        with self._lock:
            return self._stop

    def shutdown(self) -> None:
        with self._lock:
            self._stop = True
            self._cond.notify_all()
            for item in self.items.values():
                if item.status == "running":
                    item.status = "queued"     # resume next launch
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel()
        self.save()
        for worker in self._workers:
            worker.join(timeout=3.0)

    # --- reads ---

    def snapshot(self) -> list:
        with self._lock:
            return [it.to_dict() for it in self.items.values()]

    def counts(self) -> dict:
        with self._lock:
            c = {"queued": 0, "running": 0, "paused": 0, "done": 0,
                 "error": 0, "cancelled": 0}
            for item in self.items.values():
                c[item.status] = c.get(item.status, 0) + 1
            c["total"] = len(self.items)
            return c

    # --- worker ---

    def _next_queued(self):
        for item in self.items.values():
            if item.status == "queued":
                return item
        return None

    def _running_locked(self) -> int:
        return sum(1 for it in self.items.values() if it.status == "running")

    def _loop(self) -> None:
        while True:
            with self._lock:
                if self._stop:
                    return
                # wait while the allowed number of downloads are busy, so the
                # item at the front of the queue is still the next to start
                while (not self._stop
                       and self._running_locked() >= self._max_concurrent):
                    self._cond.wait(timeout=0.5)
                if self._stop:
                    return
                item = self._next_queued()
                if item is None:
                    self._cond.wait(timeout=0.5)
                    continue
                item.status = "running"
                item.stage = "downloading"
                item.stage_label = "Starting..."
                item.percent = 0.0
                job = Job(item.id)
                self._jobs[item.id] = job
                self._cond.notify_all()
            self.save()
            self._run(item, job)
            self.save()
            with self._lock:
                self._cond.notify_all()

    def _run(self, item: "Item", job: Job) -> None:
        def emit(event: dict) -> None:
            with self._lock:
                if event.get("type") == "stage":
                    item.stage = event.get("stage", "")
                    item.stage_label = event.get("stage_label", "")
                    item.percent = 0.0
                    item.speed = 0.0
                    item.eta = 0.0
                elif event.get("type") == "progress":
                    item.stage = event.get("phase", item.stage)
                    if not item.stage_label or item.stage_label == "Starting...":
                        item.stage_label = "Downloading from the web"
                    item.percent = float(event.get("percent") or 0.0)
                    item.speed = float(event.get("speed") or 0.0)
                    item.eta = float(event.get("eta") or 0.0)
                    item.downloaded = float(event.get("downloaded") or 0.0)
                    if event.get("total"):
                        item.total = float(event.get("total") or 0.0)

        try:
            result = run_download(job, item.spec, emit)
            with self._lock:
                item.status = "done"
                item.percent = 100.0
                item.path = result.get("path", "")
                item.mode = result.get("mode", "")
                item.codecs = result.get("codecs", "")
                item.note = result.get("note", "")
                item.speed = item.eta = 0.0
        except Cancelled:
            # Either the user cancelled (status is already "cancelled") or
            # shutdown() re-queued it to resume next launch -- keep both.
            with self._lock:
                if item.status == "running":
                    item.status = "cancelled"
        except Exception as exc:  # noqa: BLE001 - surfaced in the window
            with self._lock:
                if item.status == "running":
                    item.status = "error"
                    item.error = str(exc) or exc.__class__.__name__
        finally:
            with self._lock:
                self._jobs.pop(item.id, None)
                if (item.status in ("done", "error", "cancelled")
                        and item.finished is None):
                    item.finished = time.time()
                item.stage = ""
                item.stage_label = ""
            self.save()
        if DEBUG:
            log("finished", item.id, item.status)
