#!/usr/bin/env python3
"""Real-network checks for the 0.3.12 editor pack: an actual YouTube clip."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# --- ytgrab package (layered) ---
import ytgrab.infrastructure.config as config
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


URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"     # 19s, tiny
WORK = tempfile.mkdtemp(prefix="ytgrab-editor-net-")

cfg = config.load_config()


def download(spec):
    outdir = tempfile.mkdtemp(dir=WORK, prefix="out-")
    events = []
    result = downloader.run_download(job.Job("editor-net"),
                               dict(spec, outdir=outdir),
                               lambda ev: events.append(ev))
    return result, events


print("=== real clip 0:05-0:15 ===")
res, events = download({"url": URL, "format": "", "mode": "premiere_safe",
                        "convert": True, "target": "h264",
                        "clip_start": "0:05", "clip_end": "0:15"})
print("    note:", res["note"])
info = probe.probe_file(res["path"])
print(f"    duration={info['duration']:.3f} v={info['video']['name']} "
      f"a={(info['audio'] or {}).get('name')} "
      f"rate={(info['audio'] or {}).get('sample_rate')} "
      f"fps r={info['video']['fps']:.3f} avg={info['video']['fps_avg']:.3f}")
check("clip is about ten seconds", 9.3 <= info["duration"] <= 10.7, True)
check("a real file landed", os.path.isfile(res["path"]), True)
check("video is H.264", info["video"]["name"], "h264")
check("video-only pick still has audio", info["audio"]["name"] is not None, True)
check("download stage said clip",
      any(e.get("stage_label") == "Downloading the clip" for e in events), True)

print("=== real clip + 48 kHz ===")
res, events = download({"url": URL, "format": "", "mode": "premiere_safe",
                        "convert": True, "target": "h264", "audio_48k": True,
                        "clip_start": "0", "clip_end": "0:04"})
print("    note:", res["note"])
info = probe.probe_file(res["path"])
print(f"    duration={info['duration']:.3f} "
      f"a={(info['audio'] or {}).get('name')} "
      f"rate={(info['audio'] or {}).get('sample_rate')}")
check("clip is about four seconds", 3.4 <= info["duration"] <= 4.6, True)
check("audio came out at 48 kHz", info["audio"]["sample_rate"], 48000)
check("note mentions 48 kHz", "48 kHz audio" in res["note"], True)

print()
passed = N[0] - len(FAILS)
print(f"{passed}/{N[0]} checks passed")
print("RESULT:", "ALL PASS" if not FAILS else "FAILED: " + ", ".join(FAILS))
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(1 if FAILS else 0)
