#!/usr/bin/env python3
"""Checks the parallel queue engine: concurrency cap, FIFO order, pause/resume,
reordering, bulk cancel/remove and the config round-trip."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# --- ytgrab package (layered) ---
import ytgrab.services.commands as commands
import ytgrab.infrastructure.config as config
import ytgrab.errors as errors
import ytgrab.services.queue as queue

FAILS = []
CHECKS = [0]


def check(label, ok, extra=""):
    CHECKS[0] += 1
    if ok:
        print(f"PASS  {label}")
    else:
        print(f"FAIL  {label}  {extra}")
        FAILS.append(label)


# ---- isolate config -------------------------------------------------------
TMP = tempfile.mkdtemp(prefix="ytgrab-qtest-")
config.CONFIG_DIR = TMP
config.CONFIG_PATH = os.path.join(TMP, "config.json")
config.reset_config_cache()
config.save_config({"outdir": TMP, "max_concurrent": 2})


# ---- a controllable fake downloader ---------------------------------------
class Fake:
    def __init__(self):
        self.lock = threading.Lock()
        self.cur = 0
        self.peak = 0
        self.starts = []
        self.gate = threading.Event()
        self.gate.set()

    def run(self, job, spec, emit):
        url = spec.get("url") or "?"
        with self.lock:
            if spec.get("_gate"):
                self.gate.wait(10)
            self.cur += 1
            self.peak = max(self.peak, self.cur)
            self.starts.append(url)
        try:
            emit({"type": "stage", "stage": "downloading",
                  "stage_label": "Downloading"})
            steps = int(spec.get("_steps") or 6)
            for i in range(steps):
                if job.cancelled:
                    raise errors.Cancelled()
                emit({"type": "progress", "percent": i * 100.0 / steps,
                      "speed": 1e6, "eta": 1.0, "downloaded": i,
                      "total": steps})
                time.sleep(float(spec.get("_sleep") or 0.02))
            if job.cancelled:
                raise errors.Cancelled()
            return {"path": os.path.join(TMP, url + ".mp4"), "mode": "none",
                    "codecs": "h264", "note": "ok"}
        finally:
            with self.lock:
                self.cur -= 1


fake = Fake()
# patch it where the queue looks it up, not where it is defined
queue.run_download = fake.run


def wait_for(pred, timeout=15.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    print(f"  (timed out waiting for {what})")
    return False


def counts(qm):
    return qm.counts()


# ---- 1. concurrency cap + FIFO -------------------------------------------
fake.__init__()
qm = queue.QueueManager(os.path.join(TMP, "q1.json"))
qm.set_max_concurrent(3)
for name in "abcdef":
    qm.add({"url": name, "title": name})

ok = wait_for(lambda: counts(qm)["done"] == 6, 20, "6 done")
check("six items finish", ok, counts(qm))
check("three run at once (peak == 3)", fake.peak == 3, f"peak={fake.peak}")
# assignment is strictly FIFO; which of the three up-front workers reaches the
# recorder first is a race, so compare in groups of max_concurrent
check("first three added started first",
      set(fake.starts[:3]) == set("abc"), fake.starts)
check("next three added started next",
      set(fake.starts[3:]) == set("def"), fake.starts)
qm.shutdown()

# ---- 2. max_concurrent = 1 serialises ------------------------------------
fake.__init__()
qm = queue.QueueManager(os.path.join(TMP, "q2.json"))
qm.set_max_concurrent(1)
for name in "xyz":
    qm.add({"url": name, "title": name})
ok = wait_for(lambda: counts(qm)["done"] == 3, 20, "3 done")
check("serial run finishes", ok)
check("one at a time (peak == 1)", fake.peak == 1, f"peak={fake.peak}")
qm.shutdown()

# ---- 3. pause / resume / cancel ------------------------------------------
fake.__init__()
qm = queue.QueueManager(os.path.join(TMP, "q3.json"))
qm.set_max_concurrent(2)
long_id = qm.add({"url": "long", "_sleep": 0.25, "_steps": 40})
wait_for(lambda: counts(qm)["running"] == 1, 5, "long running")
check("long item runs", qm.items[long_id].status == "running")
qm.pause(long_id)
ok = wait_for(lambda: qm.items[long_id].status == "paused", 5, "paused")
check("pause stops the download", ok, qm.items[long_id].status)
check("paused counted", counts(qm)["paused"] == 1, counts(qm))
time.sleep(0.3)
check("paused item is not restarted while paused",
      qm.items[long_id].status == "paused", qm.items[long_id].status)
qm.resume(long_id)
ok = wait_for(lambda: qm.items[long_id].status in ("queued", "running"),
              5, "resumed")
check("resume requeues", ok, qm.items[long_id].status)
qm.cancel(long_id)
ok = wait_for(lambda: qm.items[long_id].status == "cancelled", 5, "cancelled")
check("cancel after resume marks cancelled", ok,
      qm.items[long_id].status)
qm.shutdown()

# ---- 4. reordering while gated -------------------------------------------
fake.__init__()
fake.gate.clear()                    # nothing may start
qm = queue.QueueManager(os.path.join(TMP, "q4.json"))
qm.set_max_concurrent(1)
ids = [qm.add({"url": n, "_gate": True}) for n in ("one", "two", "three")]
order = lambda: [qm.items[i].url for i in qm.items]      # noqa: E731
check("initial order", order() == ["one", "two", "three"], order())
check("move down works", qm.move(ids[0], +1) and
      order() == ["two", "one", "three"], order())
check("move up works", qm.move(ids[2], -1) and
      order() == ["two", "three", "one"], order())
check("move off the top is refused",
      qm.move(next(iter(qm.items)), -1) is False)
check("move off the bottom is refused",
      qm.move(list(qm.items)[-1], +1) is False)
check("bulk remove", qm.remove_many([ids[0], ids[1]]) == 2 and
      list(qm.items) == [ids[2]], list(qm.items))
fake.gate.set()
qm.shutdown()

# ---- 5. pause_all / resume_all -------------------------------------------
fake.__init__()
qm = queue.QueueManager(os.path.join(TMP, "q5.json"))
qm.set_max_concurrent(1)
for n in ("p", "q", "r"):
    qm.add({"url": n, "_sleep": 0.15, "_steps": 20})
n = qm.pause_all()
check("pause_all pauses everything", n == 3, n)
check("nothing left running/queued",
      counts(qm)["running"] == 0 and counts(qm)["queued"] == 0, counts(qm))
check("all three paused", counts(qm)["paused"] == 3, counts(qm))
n = qm.resume_all()
check("resume_all requeues", n == 3 and counts(qm)["paused"] == 0
      and counts(qm)["running"] + counts(qm)["queued"] == 3, counts(qm))
qm.cancel_many(list(qm.items))
qm.shutdown()

# ---- 6. clamping + persistence -------------------------------------------
qm = queue.QueueManager(os.path.join(TMP, "q6.json"))
check("set_max_concurrent(0) clamps to 1", qm.set_max_concurrent(0) == 1)
check("set_max_concurrent(99) clamps to 8", qm.set_max_concurrent(99) == 8)
check("set_max_concurrent(3) == 3", qm.set_max_concurrent(3) == 3)
config.reset_config_cache()
check("choice written to config",
      config.load_config().get("max_concurrent") == 3,
      config.load_config().get("max_concurrent"))
check("default is 2", config.DEFAULT_CONFIG["max_concurrent"] == 2)
qm.shutdown()

# ---- 7. IPC command surface ----------------------------------------------
replies = {}
qm = queue.QueueManager(os.path.join(TMP, "q7.json"))
handler = commands.make_handler(qm, lambda _c: None)
jid = handler({"cmd": "enqueue", "url": "ipc"})["job"]
replies["status"] = handler({"cmd": "status"})
replies["pause"] = handler({"cmd": "pause", "job": jid})
replies["resume"] = handler({"cmd": "resume", "job": jid})
replies["move"] = handler({"cmd": "move", "job": jid, "delta": 1})
replies["slots"] = handler({"cmd": "set_max_concurrent", "value": 4})
replies["pause_all"] = handler({"cmd": "pause_all"})
replies["resume_all"] = handler({"cmd": "resume_all"})
replies["cancel_many"] = handler({"cmd": "cancel_many", "jobs": [jid]})
replies["remove_many"] = handler({"cmd": "remove_many", "jobs": [jid]})
check("status reports paused", "paused" in replies["status"])
check("pause_all via IPC", replies["pause_all"]["ok"] is True)
check("resume_all via IPC", replies["resume_all"]["ok"] is True)
check("cancel_many via IPC", replies["cancel_many"]["ok"] is True)
check("remove_many via IPC", replies["remove_many"]["ok"] is True)
check("set_max_concurrent via IPC", replies["slots"]["max_concurrent"] == 4,
      replies["slots"])
qm.shutdown()

# ---- 8. paused items stay paused across a restart -------------------------
qm = queue.QueueManager(os.path.join(TMP, "q8.json"))
qm.set_max_concurrent(1)
pid = qm.add({"url": "keepme"})
qm.pause(pid)                          # queued -> paused is allowed
qm.shutdown()
qm2 = queue.QueueManager(os.path.join(TMP, "q8.json"))
qm2.set_max_concurrent(1)
check("paused survives restart", qm2.items[pid].status == "paused",
      qm2.items[pid].status)
qm2.shutdown()

# ---- 9. eight workers finishing at once keep the file valid ---------------
fake.__init__()
qm = queue.QueueManager(os.path.join(TMP, "q9.json"))
qm.set_max_concurrent(8)
for i in range(24):
    qm.add({"url": f"s{i}", "_steps": 12})
ok = wait_for(lambda: counts(qm)["done"] == 24, 30, "24 done")
check("24 items finish on 8 workers", ok, counts(qm))
check("eight ran at once", fake.peak == 8, f"peak={fake.peak}")
qm.shutdown()
with open(os.path.join(TMP, "q9.json"), encoding="utf-8") as fh:
    saved = json.load(fh)
check("queue file survived concurrent saves",
      len(saved.get("items", [])) == 24, len(saved.get("items", [])))

print()
print(f"{CHECKS[0] - len(FAILS)}/{CHECKS[0]} checks passed")
if FAILS:
    print("FAILED:", ", ".join(FAILS))
    sys.exit(1)
print("ALL QUEUE CHECKS PASS")
