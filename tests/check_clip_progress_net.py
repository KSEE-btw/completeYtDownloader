#!/usr/bin/env python3
"""Real-network check that a clip download reports progress as it goes.

A short clip on a tiny video only ever prints one ffmpeg status line, so this
uses a 20-second section of a longer video: the whole point is that the queue
row shows motion well before the download finishes.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# --- ytgrab package (layered) ---
import ytgrab.services.downloader as downloader
import ytgrab.services.job as job
import ytgrab.infrastructure.probe as probe

FAILS = []
N = [0]


def check(label, got, want):
    N[0] += 1
    if got == want:
        print(f"  [PASS] {label}  ({got!r})")
    else:
        print(f"  [FAIL] {label}: got {got!r}, wanted {want!r}")
        FAILS.append(label)


WORK = tempfile.mkdtemp(prefix="ytgrab-clipprog-net-")
BIG = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"      # 10:34
TINY = "https://www.youtube.com/watch?v=jNQXAC9IVRw"     # 19s


def download(spec, tag):
    outdir = tempfile.mkdtemp(dir=WORK, prefix=f"out-{tag}-")
    events = []
    result = downloader.run_download(job.Job(f"clipprog-{tag}"),
                               dict(spec, outdir=outdir),
                               lambda ev: events.append(ev))
    return result, events


def interior(events):
    return [e for e in events
            if e.get("type") == "progress" and "seconds" in e]


print("=== a 20-second clip of a 10-minute video moves while it downloads ===")
res, events = download({
    "url": BIG, "format": "", "mode": "premiere_safe", "convert": True,
    "target": "h264", "clip_start": "0:10", "clip_end": "0:30"}, "mid")
steps = interior(events)
pcts = [round(e["percent"], 2) for e in steps]
print(f"    {len(steps)} interim samples: {pcts[:6]}{' ...' if len(pcts) > 6 else ''}")
print(f"    final sample: {pcts[-1:]}")
check("many samples arrived", len(steps) >= 3, True)
check("progress only ever moves forward", pcts == sorted(pcts), True)
check("at least one sample is mid-flight",
      any(10.0 < p < 90.0 for p in pcts), True)
check("the row is no longer stuck at zero", pcts[0] > 0, True)
info = probe.probe_file(res["path"])
print(f"    duration={info['duration']:.3f} {info['video']['name']}/{info['audio']['name']}")
check("the clip is about twenty seconds", 19.3 <= info["duration"] <= 20.7, True)
check("a real file landed", os.path.isfile(res["path"]), True)

print("=== an open-ended clip has no length, so no percentage is invented ===")
res, events = download({
    "url": TINY, "format": "", "mode": "premiere_safe", "convert": True,
    "target": "h264", "clip_start": "0:05", "clip_end": ""}, "open")
steps = interior(events)
check("no interim samples", steps, [])
pcts = [round(e["percent"], 2) for e in events if e.get("type") == "progress"]
check("only yt-dlp's closing event", pcts, [100.0])
info = probe.probe_file(res["path"])
print(f"    duration={info['duration']:.3f}")
check("it still fetched from 0:05 to the end",
      13.3 <= info["duration"] <= 14.7, True)

print()
passed = N[0] - len(FAILS)
print(f"{passed}/{N[0]} checks passed")
print("RESULT:", "ALL PASS" if not FAILS else "FAILED: " + ", ".join(FAILS))
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(1 if FAILS else 0)
