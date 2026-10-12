#!/usr/bin/env python3
"""Checks that a clip download reports progress.

`--download-sections` hands the whole clip to ffmpeg, whose position is not
surfaced through yt-dlp's progress hook -- the queue row used to sit at 0% for
the entire download.  These checks cover the parser that reads ffmpeg's own
status line, and drive the real run_download() against a faked yt-dlp that
prints exactly what a real clip download prints.
"""
from __future__ import annotations

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
import ytgrab.presentation.ui.formatting as formatting
import ytgrab.services.job as job
import ytgrab.domain.models as models
import ytgrab.domain.progress as progress
import ytgrab.services.queue as queue
import ytgrab.infrastructure.tools as tools
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


def near(label, got, want, tol=0.01):
    N[0] += 1
    if abs(float(got) - float(want)) <= tol:
        print(f"  [PASS] {label}: {got!r}")
    else:
        print(f"  [FAIL] {label}: got {got!r}, wanted {want!r}")
        FAILS.append(label)


FFMPEG = tools.tools()["ffmpeg"]
if not FFMPEG:
    print("no ffmpeg on PATH")
    sys.exit(2)

WORK = tempfile.mkdtemp(prefix="ytgrab-clipprog-")

print("=== ffmpeg's status line is recognised ===")
check("normal status line", progress._is_ffmpeg_status(
    "frame=  150 fps=0.0 q=-1.0 Lsize=  374KiB time=00:00:10.00 bitrate= "
    "306.3kbits/s speed=27.4x elapsed=0:00:00.36"), True)
check("no-frame variant", progress._is_ffmpeg_status(
    "size=   16384KiB time=00:00:30.00 bitrate=4642.0kbits/s speed=1.16x"),
    True)
check("yt-dlp progress line is not one", progress._is_ffmpeg_status(
    "@@P@@1024 629172 0 0 0"), False)
check("plain log line is not one", progress._is_ffmpeg_status(
    "[download] Destination: clip.mp4"), False)

print("=== position, percentage and ETA come out of it ===")
LINE = ("frame=   90 fps= 30 q=28.0 size=     384KiB time=00:00:15.00 "
        "bitrate= 209.7kbits/s speed=2.00x elapsed=0:00:07.50")
ev = progress._ffmpeg_progress_event(LINE, 30.0, 0.0)
check("percent", round(ev["percent"], 2), 50.0)
check("seconds", ev["seconds"], 15.0)
near("eta from ffmpeg's own speed", ev["eta"], 7.5)
check("phase", ev["phase"], "downloading")
check("no byte count is invented", (ev["downloaded"], ev["total"]), (0, 0))

check("nothing before the first frame", progress._ffmpeg_progress_event(
    "frame=    0 fps=0.0 q=0.0 size=       0KiB time=N/A bitrate=N/A "
    "speed=N/A elapsed=0:00:02.06", 30.0, 0.0), None)
check("a repeated position is dropped", progress._ffmpeg_progress_event(
    LINE, 30.0, 15.0), None)
check("a going-backwards position is dropped", progress._ffmpeg_progress_event(
    LINE, 30.0, 20.0), None)
check("no length known, no event", progress._ffmpeg_progress_event(
    LINE, 0.0, 0.0), None)
check("the last line is clamped to 100%", round(progress._ffmpeg_progress_event(
    "frame=  1800 fps= 70 q=-1.0 Lsize=   17000KiB time=00:00:30.40 "
    "bitrate=4642.0kbits/s speed=1.16x elapsed=0:00:25.88",
    30.0, 0.0)["percent"], 2), 100.0)

print("=== the span yt-dlp announces ===")
near("bounded range", progress._section_length(
    "[info] jNQXAC9IVRw: Downloading 1 time ranges: 5.0-25.0"), 20.0)
check("open end stays unknown", progress._section_length(
    "[info] jNQXAC9IVRw: Downloading 1 time ranges: 5.0-inf"), 0.0)
near("clamped end is picked up", progress._section_length(
    "[info] abc: Downloading 1 time ranges: 0.0-12.0"), 12.0)
check("unrelated line", progress._section_length("[download] Destination: x"), 0.0)

print("=== clip_length ===")
near("both bounds", editor.clip_length({"clip_start": 5, "clip_end": 25}), 20.0)
near("start only", editor.clip_length({"clip_start": 5, "clip_end": None}), 0.0)
near("end only", editor.clip_length({"clip_start": None, "clip_end": 12}), 12.0)
near("nothing", editor.clip_length({"clip_start": None, "clip_end": None}), 0.0)
near("backwards clamps to none", editor.clip_length(
    {"clip_start": 25, "clip_end": 5}), 0.0)

print("=== the row says something truthful while ffmpeg warms up ===")
CLIP_SPEC = {"url": "u", "clip_start": "0:05", "clip_end": "0:25"}
check("clip, no position yet", formatting.detail_text({
    "status": "running", "stage": "downloading", "stage_label": "",
    "percent": 0.0, "speed": 0, "eta": 0, "total": 0, "spec": CLIP_SPEC,
    "path": "", "note": "", "mode": "", "error": "", "url": "u"}),
    "Downloading the clip \u00b7 clip 0:05\u20130:25")
check("clip, real progress", formatting.detail_text({
    "status": "running", "stage": "downloading", "stage_label": "",
    "percent": 62.0, "speed": 0, "eta": 0, "total": 0, "spec": CLIP_SPEC,
    "path": "", "note": "", "mode": "", "error": "", "url": "u"}),
    "Downloading 62% \u00b7 clip 0:05\u20130:25")
check("no clip, 0% is honest", formatting.detail_text({
    "status": "running", "stage": "downloading", "stage_label": "",
    "percent": 0.0, "speed": 0, "eta": 0, "total": 0, "spec": {"url": "u"},
    "path": "", "note": "", "mode": "", "error": "", "url": "u"}),
    "Downloading 0%")

print("=== and the bar spins instead of parking at 0% ===")
TMP = tempfile.mkdtemp(prefix="ytgrab-clipprog-ui-")
config.CONFIG_DIR = TMP
config.CONFIG_PATH = os.path.join(TMP, "config.json")
config.reset_config_cache()
config.save_config({"outdir": TMP})

qm = queue.QueueManager(os.path.join(TMP, "queue.json"))
qm.request_stop()
for w in qm._workers:
    w.join(timeout=2)


def seed(item_id, percent, spec):
    item = models.Item(item_id, spec)
    item.status = "running"
    item.stage = "downloading"
    item.percent = percent
    qm.items[item_id] = item
    return item


seed("c0", 0.0, dict(CLIP_SPEC))
seed("c40", 40.0, dict(CLIP_SPEC))
seed("plain0", 0.0, {"url": "u"})

root = window.QueueWindow(qm, show=False)
root.withdraw()
root.update()
root._tick(force=True)
check("clip at 0% spins", root.rows["c0"]["bar_mode"], "indeterminate")
check("clip at 40% is determinate", root.rows["c40"]["bar_mode"],
      "determinate")
check("a plain download at 0% is determinate",
      root.rows["plain0"]["bar_mode"], "determinate")

# --- the whole pipeline, against a faked yt-dlp ------------------------------ #

print("=== a real clip download reports motion ===")
FIXTURE = os.path.join(WORK, "fixture.mp4")
subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
                "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=48000:duration=2",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt",
                "yuv420p", "-c:a", "aac", "-shortest", FIXTURE],
               capture_output=True, text=True, timeout=120, check=True)

REAL_YTDLP = tools.tools()["ytdlp"]
REAL_POPEN = subprocess.Popen
CALLS = []

STATUS = ("frame=   {f} fps= 30 q=28.0 size=     256KiB time=00:00:{t} "
          "bitrate= 209.7kbits/s speed=2.00x elapsed=0:00:{t}")


def clip_script(o_arg, ranges):
    """What yt-dlp really prints for a sectioned download.

    ffmpeg's `time=` counts from zero at the *start of the section*, so the
    positions are generated as fractions of the span yt-dlp announced.
    """
    first, second = ranges.split("-")
    start = float(first)
    end = None if second == "inf" else float(second)
    lines = [
        "Extracting cookies from firefox",
        "Extracted 1082 cookies from firefox",
        "[youtube] abc: Downloading webpage",
        "[info] abc: Downloading 1 format(s): 18",
        f"[info] abc: Downloading 1 time ranges: {ranges}",
        f"[download] Destination: {o_arg % {'title': 'clip', 'ext': 'mp4'}}",
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'https://example.invalid':",
        "frame=    0 fps=0.0 q=0.0 size=       0KiB time=N/A bitrate=N/A "
        "speed=N/A elapsed=0:00:00.50",
    ]
    marks = []
    if end is not None and end > start:
        length = end - start
        marks = [length * f for f in (0.25, 0.5, 0.75, 1.0)]
    for frame, secs in zip((30, 60, 90), marks):
        lines.append(STATUS.format(f=frame, t=f"{secs:05.2f}"))
    if marks:
        lines.append("frame=  120 fps= 30 q=-1.0 Lsize=     512KiB "
                     f"time=00:00:{marks[-1]:05.2f} bitrate= 209.7kbits/s "
                     "speed=2.00x elapsed=0:00:10.00")
    lines.append("[download] 100% of    512KiB in 00:00:10 at 51.20KiB/s")
    lines.append("@@P@@512000 512000 0 51200 0")
    return "\n".join(lines) + "\n"


def make_fake(ranges):
    def fake_popen(args, **kwargs):
        if args and os.path.abspath(str(args[0])) == os.path.abspath(REAL_YTDLP):
            CALLS.append([str(a) for a in args])
            o_arg = args[args.index("-o") + 1]
            outdir = os.path.dirname(o_arg)
            shutil.copy(FIXTURE, os.path.join(outdir, "clip.mp4"))
            text = clip_script(o_arg, ranges)

            class FakeProc:
                def __init__(self):
                    self.stdout = io.StringIO(text)
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


def download(spec):
    outdir = tempfile.mkdtemp(dir=WORK, prefix="out-")
    events = []
    subprocess.Popen = make_fake(spec["_ranges"])
    try:
        result = downloader.run_download(
            job.Job("clipprog"), dict(spec, outdir=outdir,
                                       _ranges=spec["_ranges"]),
            lambda ev: events.append(ev))
    finally:
        subprocess.Popen = REAL_POPEN
    return result, events


SPEC = {"url": "https://example.invalid/x", "format": "", "mode":
        "premiere_safe", "convert": True, "target": "h264",
        "clip_start": "0:05", "clip_end": "0:25"}

res, events = download(dict(SPEC, _ranges="5.0-25.0"))
arg = CALLS[-1]
check("yt-dlp was asked for the section", arg[arg.index(
    "--download-sections") + 1], "*0:05-0:25")
steps = [e for e in events if e.get("type") == "progress" and "seconds" in e]
check("positions reported", [e["seconds"] for e in steps],
      [5.0, 10.0, 15.0, 20.0])
check("percentages reported", [round(e["percent"], 2) for e in steps],
      [25.0, 50.0, 75.0, 100.0])
check("no ETA until ffmpeg's speed has settled",
      [round(e["eta"], 2) for e in steps], [0.0, 0.0, 2.5, 0.0])
pcts = [e["percent"] for e in events if e.get("type") == "progress"]
check("progress never goes backwards",
      pcts == sorted(pcts), True)
check("the row never sits at a stale 0 mid-download",
      [p for p in pcts if 0 < p < 100], [25.0, 50.0, 75.0])
check("the clip landed", os.path.basename(res["path"]), "clip.mp4")

print("=== an open-ended clip has no length, so no invented percentage ===")
res, events = download(dict(SPEC, clip_end="", _ranges="5.0-inf"))
steps = [e for e in events if e.get("type") == "progress" and "seconds" in e]
check("no ffmpeg positions", steps, [])
check("only yt-dlp's closing event", [round(e["percent"], 2) for e in events
                                     if e.get("type") == "progress"], [100.0])
arg = CALLS[-1]
check("asked for everything from 0:05 on",
      arg[arg.index("--download-sections") + 1], "*0:05-inf")

print("=== an end past the video is clamped, and we still track it ===")
res, events = download(dict(SPEC, clip_end="99:00", _ranges="5.0-12.0"))
steps = [e for e in events if e.get("type") == "progress" and "seconds" in e]
check("positions are 0-based inside the section",
      [e["seconds"] for e in steps], [1.75, 3.5, 5.25, 7.0])
check("measured against yt-dlp's clamped span, not the typed end",
      [round(e["percent"], 2) for e in steps], [25.0, 50.0, 75.0, 100.0])

print("=== a plain download is untouched ===")
res, events = download({"url": "https://example.invalid/x", "format": "",
                        "mode": "premiere_safe", "convert": True,
                        "target": "h264", "_ranges": "0.0-2.0"})
check("no section was asked for", "--download-sections" in CALLS[-1], False)
pcts = [round(e["percent"], 2) for e in events if e.get("type") == "progress"]
check("the normal byte progress still arrives", pcts, [100.0])
check("its result is still a move", os.path.basename(res["path"]), "clip.mp4")

print()
if FAILS:
    print(f"FAILED {len(FAILS)}/{N[0]}: " + "; ".join(FAILS))
    sys.exit(1)
print(f"all {N[0]} checks passed")
