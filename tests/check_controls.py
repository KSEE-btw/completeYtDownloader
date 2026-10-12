#!/usr/bin/env python3
"""Checks the queue window's new controls: the "at once" picker, pause/resume,
reorder buttons, the tick boxes and the bulk actions."""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# --- ytgrab package (layered) ---
import ytgrab.infrastructure.config as config
import ytgrab.presentation.ui.formatting as formatting
import ytgrab.domain.models as models
import ytgrab.services.queue as queue
import ytgrab.presentation.ui.window as window

FAILS = []
N = [0]


def check(label, got, want):
    N[0] += 1
    if got == want:
        print(f"  [PASS] {label}: {got!r}")
    else:
        print(f"  [FAIL] {label}: got {got!r}, wanted {want!r}")
        FAILS.append(label)


TMP = tempfile.mkdtemp(prefix="ytgrab-ctl-")
config.CONFIG_DIR = TMP
config.CONFIG_PATH = os.path.join(TMP, "config.json")
config.reset_config_cache()
config.save_config({"outdir": TMP, "max_concurrent": 3})

qm = queue.QueueManager(os.path.join(TMP, "queue.json"))
qm.request_stop()
for w in qm._workers:
    w.join(timeout=2)


def seed(item_id, status, title):
    item = models.Item(item_id, {"url": f"https://example.com/{item_id}",
                             "title": title})
    item.status = status
    if status == "running":
        item.stage = "downloading"
        item.percent = 42.0
    qm.items[item_id] = item
    return item


seed("q1", "queued", "waiting one")
seed("q2", "queued", "waiting two")
seed("r1", "running", "busy one")
seed("p1", "paused", "held one")
seed("d1", "done", "finished one")

print("=== paused detail text ===")
check("paused detail", formatting.detail_text(
    {"status": "paused", "stage": "", "stage_label": "", "percent": 0,
     "speed": 0, "eta": 0, "total": 0, "path": "", "note": "",
     "mode": "", "error": "", "url": "u"}), "Paused \u2014 press Resume to "
    "carry on")

root = window.QueueWindow(qm, show=False)
root.withdraw()
root.update()
root._tick(force=True)

print("=== toolbar reflects the queue ===")
check("slots picker shows config", root.slots.get(), "3")
check("summary counts paused", "1 paused" in root.summary.cget("text"), True)
check("pause button offers pause", root.pauseBtn.cget("text"), "Pause all")
check("nothing selected -> cancel disabled",
      str(root.selCancel.cget("state")), "disabled")
check("nothing selected -> remove disabled",
      str(root.selRemove.cget("state")), "disabled")

print("=== per-row buttons by status ===")
names = lambda i: list(root.rows[i]["buttons"])            # noqa: E731
check("queued row: cancel/remove/pause/arrows",
      sorted(names("q1")), sorted(["Cancel", "Remove", "Pause",
                                   "\u25bc", "\u25b2"]))
check("running row: pause + cancel",
      sorted(names("r1")), sorted(["Pause", "Cancel"]))
check("paused row: resume/remove/arrows",
      sorted(names("p1")), sorted(["Resume", "Remove", "\u25bc", "\u25b2"]))
check("done row: show file + remove",
      sorted(names("d1")), sorted(["Show file", "Remove"]))

print("=== tick boxes and selection ===")
check("unselected glyph", root.rows["q1"]["check"].cget("text"), "\u2610")
root._toggle_select("q1")
check("selected glyph", root.rows["q1"]["check"].cget("text"), "\u2611")
check("selected border is accent",
      root.rows["q1"]["frame"].cget("highlightbackground"),
      root.p["accent"])
check("cancel now enabled", str(root.selCancel.cget("state")), "normal")
check("cancel label stays fixed", root.selCancel.cget("text"),
      "Cancel selected")
check("summary counts the pick",
      "1 selected" in root.summary.cget("text"), True)
root._toggle_select("q2")
check("two picked shown in summary",
      "2 selected" in root.summary.cget("text"), True)
root._toggle_select("q2")
check("untick drops the count",
      "1 selected" in root.summary.cget("text"), True)
root._toggle_select("q1")
check("untick all disables", str(root.selRemove.cget("state")), "disabled")
check("no selection left in summary",
      "selected" not in root.summary.cget("text"), True)

print("=== the at-once picker saves ===")
root.slots.set("5")
root._on_slots()
config.reset_config_cache()
check("qm limit updated", qm.max_concurrent(), 5)
check("written to config", config.load_config().get("max_concurrent"), 5)
check("footer mentions it", "5 at a time" in root.footer.cget("text"), True)

print("=== pause all / resume all from the toolbar ===")
root._toggle_pause_all()
check("running/queued all held", qm.counts()["paused"], 4)
check("nothing queued or running",
      qm.counts()["queued"] + qm.counts()["running"], 0)
check("button flips to resume", root.pauseBtn.cget("text"), "Resume all")
root._toggle_pause_all()
check("resume all releases them",
      qm.counts()["paused"], 0)
check("all four waiting again", qm.counts()["queued"], 4)
check("button back to pause", root.pauseBtn.cget("text"), "Pause all")

print("=== bulk remove ===")
root._toggle_select("q1")
root._toggle_select("q2")
root._remove_selected()
check("both removed", qm.counts()["queued"], 2)
check("selection cleared", len(root.selected), 0)
check("footer reports", "Removed 2 item(s)." in root.footer.cget("text"), True)

root._force_quit()
qm.shutdown()

print()
print(f"{N[0] - len(FAILS)}/{N[0]} checks passed")
print("RESULT:", "ALL PASS" if not FAILS else "FAILED: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
