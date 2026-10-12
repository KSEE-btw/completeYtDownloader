#!/usr/bin/env python3
"""End-to-end checks for the 0.3.12 editor pack.

Drives the real run_download()/ffmpeg pipeline, but swaps the yt-dlp child for
a copy of a locally-built fixture, so the clip/CFR/48k decisions can be checked
without the network.  The section arguments really are asserted off the argv.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# --- ytgrab package (layered) ---
import ytgrab.infrastructure.config as config
import ytgrab.services.downloader as downloader
import ytgrab.domain.editor as editor
import ytgrab.services.job as job
import ytgrab.infrastructure.probe as probe
import ytgrab.infrastructure.tools as tools

FAILS = []
N = [0]


def check(label, got, want):
    N[0] += 1
    if got == want:
        print(f"  [PASS] {label}  ({got!r})")
    else:
        print(f"  [FAIL] {label}: got {got!r}, wanted {want!r}")
        FAILS.append(label)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


FFMPEG = tools.tools()["ffmpeg"]
if not FFMPEG:
    print("no ffmpeg on PATH")
    sys.exit(2)

WORK = tempfile.mkdtemp(prefix="ytgrab-editor-e2e-")


def run(*args, ok=True):
    proc = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                           *args],
                          capture_output=True, text=True, timeout=300)
    if ok and proc.returncode != 0:
        raise RuntimeError(" ".join(args) + "\n" + proc.stderr)
    return proc


print("=== building fixtures ===")
CFR_441 = os.path.join(WORK, "cfr-441.mp4")
run("-f", "lavfi", "-i", "testsrc=size=160x120:rate=30000/1001:duration=2",
    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2",
    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-shortest", CFR_441)

CFR_48K = os.path.join(WORK, "cfr-48k.mp4")
run("-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
    "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=2",
    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-shortest", CFR_48K)

# a genuinely variable-rate file: a 30 fps second glued to a 15 fps one with
# -c copy, so the frame spacing really does change midway
B1 = os.path.join(WORK, "b1.mp4")
B2 = os.path.join(WORK, "b2.mp4")
run("-f", "lavfi", "-i", "testsrc=size=160x120:rate=30:duration=1",
    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
    "-video_track_timescale", "90000", B1)
run("-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=1",
    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
    "-video_track_timescale", "90000", B2)
LIST = os.path.join(WORK, "list.txt")
with open(LIST, "w", newline="") as fh:
    fh.write(f"file '{B1}'\nfile '{B2}'\n")
VFR = os.path.join(WORK, "vfr.mp4")
run("-f", "concat", "-safe", "0", "-i", LIST, "-c", "copy", VFR)

for name, path in (("cfr-441", CFR_441), ("cfr-48k", CFR_48K), ("vfr", VFR)):
    info = probe.probe_file(path)
    v, a = info["video"], info["audio"]
    print(f"  {name}: dur={info['duration']:.3f} "
          f"v={v['name']} r={v['fps']:.3f} avg={v['fps_avg']:.3f} "
          f"raw={v['fps_raw']!r} a={(a or {}).get('name')} "
          f"rate={(a or {}).get('sample_rate')} vfr={editor.is_vfr(info)}")

vfr_info = probe.probe_file(VFR)
check("vfr fixture really is variable", editor.is_vfr(vfr_info), True)

REAL_YTDLP = tools.tools()["ytdlp"]
REAL_POPEN = subprocess.Popen
CALLS = []


def make_fake(fixture):
    """A subprocess.Popen that copies `fixture` instead of running yt-dlp."""
    def fake_popen(args, **kwargs):
        if args and os.path.abspath(str(args[0])) == os.path.abspath(REAL_YTDLP):
            CALLS.append([str(a) for a in args])
            outdir = WORK
            if "-o" in args:
                outdir = os.path.dirname(args[args.index("-o") + 1])
            shutil.copy(fixture, os.path.join(outdir, "downloaded.mp4"))

            class FakeProc:
                def __init__(self):
                    self.stdout = io.StringIO("")
                    self.stderr = io.StringIO("")
                    self.returncode = 0
                    self.pid = 0
                    self._done = False

                def wait(self):
                    self._done = True
                    return self.returncode

                def poll(self):
                    return self.returncode if self._done else None

                def kill(self):
                    pass

            return FakeProc()
        return REAL_POPEN(args, **kwargs)
    return fake_popen


CONFIG = dict(config.DEFAULT_CONFIG)
CONFIG["cookies_from_browser"] = "none"
# patch it where run_download looks it up, so later CONFIG edits stay live
downloader.load_config = lambda: dict(CONFIG)


def download(spec, fixture):
    outdir = tempfile.mkdtemp(dir=WORK, prefix="out-")
    spec = dict(spec, outdir=outdir)
    events = []
    subprocess.Popen = make_fake(fixture)
    try:
        result = downloader.run_download(job.Job("editor-e2e"), spec,
                                   lambda ev: events.append(ev))
    finally:
        subprocess.Popen = REAL_POPEN
    return result, events, outdir


def stages(events):
    return [e.get("stage") for e in events if e.get("type") == "stage"]


def edit_actions(events):
    return [s for s in stages(events) if s in ("remuxing", "converting")]


print("=== 48 kHz rewrite keeps the video (state=ok, convert on) ===")
res, events, _ = download({
    "url": "https://example.invalid/x", "format": "", "mode": "premiere_safe",
    "convert": True, "target": "h264", "audio_48k": True,
}, CFR_441)
info = probe.probe_file(res["path"])
check("audio rate is 48 kHz", info["audio"]["sample_rate"], 48000)
check("video still h264", info["video"]["name"], "h264")
check("video was not re-encoded", info["video"]["family"], "h264")
check("note mentions 48 kHz", "48 kHz audio" in res["note"], True)
check("codecs were already fine", res["mode"], "ok")
check("remuxed through ffmpeg", edit_actions(events), ["remuxing"])
check("output differs from the source", sha(res["path"]) != sha(CFR_441), True)

print("=== 48 kHz not asked -> byte-identical move ===")
res, events, _ = download({
    "url": "https://example.invalid/x", "format": "", "mode": "premiere_safe",
    "convert": True, "target": "h264", "audio_48k": False,
}, CFR_441)
check("moved untouched", sha(res["path"]), sha(CFR_441))
check("no ffmpeg step", edit_actions(events), [])
res, events, _ = download({
    "url": "https://example.invalid/x", "format": "", "mode": "premiere_safe",
    "convert": False, "target": "h264", "audio_48k": True,
}, CFR_441)
check("48k beats conversion-off (rewritten)", sha(res["path"]) != sha(CFR_441), True)
info = probe.probe_file(res["path"])
check("conversion-off 48k lands at 48 kHz", info["audio"]["sample_rate"], 48000)

print("=== 48 kHz source is left alone ===")
res, events, _ = download({
    "url": "https://example.invalid/x", "format": "", "mode": "premiere_safe",
    "convert": True, "target": "h264", "audio_48k": True,
}, CFR_48K)
check("already 48 kHz -> byte-identical", sha(res["path"]), sha(CFR_48K))
check("no ffmpeg step", edit_actions(events), [])

print("=== VFR -> CFR re-encode ===")
res, events, _ = download({
    "url": "https://example.invalid/x", "format": "", "mode": "premiere_safe",
    "convert": True, "target": "h264", "force_cfr": True,
}, VFR)
info = probe.probe_file(res["path"])
nominal, average = editor.frame_rates(info)
print(f"    out fps nominal={nominal:.4f} avg={average:.4f}")
check("output is constant", editor.is_vfr(info), False)
check("output keeps the nominal rate", round(nominal, 3),
      round(vfr_info["video"]["fps"], 3))
check("note mentions the frame rate", "constant" in res["note"], True)
check("a real re-encode happened", sha(res["path"]) != sha(VFR), True)
check("mode reported as ok (codecs were fine)", res["mode"], "ok")

print("=== VFR without the tick is left alone ===")
res, events, _ = download({
    "url": "https://example.invalid/x", "format": "", "mode": "premiere_safe",
    "convert": True, "target": "h264", "force_cfr": False,
}, VFR)
check("untouched", sha(res["path"]), sha(VFR))
check("no ffmpeg step", edit_actions(events), [])
check("still variable on disk", editor.is_vfr(probe.probe_file(res["path"])), True)

print("=== clip range reaches yt-dlp ===")
CALLS.clear()
res, events, _ = download({
    "url": "https://example.invalid/x", "format": "", "mode": "premiere_safe",
    "convert": True, "target": "h264",
    "clip_start": "0:30", "clip_end": "0:45",
}, CFR_441)
argv = CALLS[-1]
check("download stage says clip", "Downloading the clip" in
      [e.get("stage_label") for e in events if e.get("type") == "stage"], True)
check("section args present", "--download-sections" in argv, True)
check("section range", argv[argv.index("--download-sections") + 1], "*0:30-0:45")
check("keyframes forced", "--force-keyframes-at-cuts" in argv, True)

CALLS.clear()
download({"url": "https://example.invalid/x", "convert": True, "target": "h264",
          "clip_start": "1:00"}, CFR_441)
argv = CALLS[-1]
check("open-ended clip runs to the end",
      argv[argv.index("--download-sections") + 1], "*1:00-inf")

CALLS.clear()
download({"url": "https://example.invalid/x", "convert": True, "target": "h264",
          "clip_end": "0:20"}, CFR_441)
argv = CALLS[-1]
check("clip with only an end starts at zero",
      argv[argv.index("--download-sections") + 1], "*0-0:20")

CALLS.clear()
download({"url": "https://example.invalid/x", "convert": True, "target": "h264",
          "clip_start": "20", "clip_end": "10"}, CFR_441)
argv = CALLS[-1]
check("backwards range drops the end",
      argv[argv.index("--download-sections") + 1], "*0:20-inf")

CALLS.clear()
download({"url": "https://example.invalid/x", "convert": True, "target": "h264"},
         CFR_441)
check("no clip -> no section args", "--download-sections" in CALLS[-1], False)

print("=== config supplies the default clip ===")
CONFIG["clip_start"] = "0:02"
CONFIG["clip_end"] = "0:06"
CALLS.clear()
download({"url": "https://example.invalid/x", "convert": True, "target": "h264"},
         CFR_441)
argv = CALLS[-1]
check("config clip used",
      argv[argv.index("--download-sections") + 1], "*0:02-0:06")
CONFIG["clip_start"] = CONFIG["clip_end"] = ""

print()
passed = N[0] - len(FAILS)
print(f"{passed}/{N[0]} checks passed")
print("RESULT:", "ALL PASS" if not FAILS else "FAILED: " + ", ".join(FAILS))
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(1 if FAILS else 0)
