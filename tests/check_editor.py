#!/usr/bin/env python3
"""Unit checks for the editor pack in ytgrab.domain.editor (0.3.12)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

FAILS = []
N = [0]


def check(label, got, want):
    N[0] += 1
    if got == want:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}: got {got!r}, wanted {want!r}")
        FAILS.append(label)


# --- ytgrab package (layered) ---
import ytgrab.domain.codecs as codecs
import ytgrab.infrastructure.config as config
import ytgrab.domain.editor as editor
import ytgrab.domain.encode as encode
import ytgrab.infrastructure.probe as probe
import ytgrab.domain.timecode as timecode
import ytgrab.infrastructure.tools as tools

print("=== parse_timecode ===")
for text, want in [
    ("90", 90.0), ("1:30", 90.0), ("0:01:30", 90.0), ("1:01:01.5", 3661.5),
    ("1,5", 1.5), ("  2:00  ", 120.0), ("30s", 30.0), ("0", 0.0),
    ("", None), (None, None), ("abc", None), ("1:2:3:4", None),
    ("-5", None), ("1:-2", None), (":", None), ("1:", None),
    (45, 45.0), (2.5, 2.5), (-1, None), (True, None),
]:
    check(f"parse {text!r}", timecode.parse_timecode(text), want)

print("=== format_timecode ===")
for secs, want in [(90, "1:30"), (3661, "1:01:01"), (3661.25, "1:01:01.250"),
                   (0, "0:00"), (5.5, "0:05.500"), (3599, "59:59")]:
    check(f"format {secs}", timecode.format_timecode(secs), want)

print("=== round trip ===")
for text, want in [("1:30", "1:30"), ("1:01:30", "1:01:30"),
                   ("2:03:04.5", "2:03:04.500"), ("0:00", "0:00")]:
    check(f"round trip {text}",
          timecode.format_timecode(timecode.parse_timecode(text)), want)

print("=== section_args ===")
FKC = "--force-keyframes-at-cuts"
check("no clip", editor.section_args(None, None), [])
check("both ends", editor.section_args(90, 120),
      ["--download-sections", "*1:30-2:00", FKC])
check("open end", editor.section_args(90, None),
      ["--download-sections", "*1:30-inf", FKC])
check("open start", editor.section_args(None, 120),
      ["--download-sections", "*0-2:00", FKC])
check("fractional", editor.section_args(0.5, 1.25),
      ["--download-sections", "*0:00.500-0:01.250", FKC])

print("=== clip_bounds ===")
CFG = {"clip_start": "", "clip_end": ""}
check("spec wins", editor.clip_bounds({"clip_start": "1:00", "clip_end": "2:00"},
                                    CFG), (60.0, 120.0))
check("blank spec falls back",
      editor.clip_bounds({"clip_start": "", "clip_end": ""},
                       {"clip_start": "10", "clip_end": "20"}), (10.0, 20.0))
check("missing keys fall back",
      editor.clip_bounds({}, {"clip_start": "10", "clip_end": "20"}), (10.0, 20.0))
check("start only", editor.clip_bounds({"clip_start": "5"}, CFG), (5.0, None))
check("end only", editor.clip_bounds({"clip_end": "5"}, CFG), (None, 5.0))
check("backwards range drops the end",
      editor.clip_bounds({"clip_start": "20", "clip_end": "10"}, CFG), (20.0, None))
check("equal range drops the end",
      editor.clip_bounds({"clip_start": "10", "clip_end": "10"}, CFG), (10.0, None))
check("nonsense is ignored",
      editor.clip_bounds({"clip_start": "wat", "clip_end": "also"}, CFG), (None, None))

print("=== editor_flags ===")
check("defaults off", editor.editor_flags({}, config.DEFAULT_CONFIG),
      {"clip_start": None, "clip_end": None,
       "force_cfr": False, "audio_48k": False})
check("spec booleans", editor.editor_flags({"force_cfr": True, "audio_48k": True},
                                         config.DEFAULT_CONFIG),
      {"clip_start": None, "clip_end": None,
       "force_cfr": True, "audio_48k": True})
for text, want in [("false", False), ("0", False), ("no", False),
                   ("off", False), ("true", True), ("yes", True)]:
    got = editor.editor_flags({"force_cfr": text}, config.DEFAULT_CONFIG)["force_cfr"]
    check(f"string flag {text!r}", got, want)
check("cfg default honoured",
      editor.editor_flags({}, {"clip_start": "", "clip_end": "",
                             "force_cfr": True, "audio_48k": False}),
      {"clip_start": None, "clip_end": None,
       "force_cfr": True, "audio_48k": False})

print("=== is_vfr / cfr_args ===")
CFR = {"video": {"fps": 29.97, "fps_avg": 29.97, "fps_raw": "30000/1001"}}
VFR = {"video": {"fps": 30.0, "fps_avg": 24.5, "fps_raw": "30/1"}}
NOV = {"video": {"fps": 0, "fps_avg": 0, "fps_raw": ""}}
check("constant is not vfr", editor.is_vfr(CFR), False)
check("wandering is vfr", editor.is_vfr(VFR), True)
check("unknown is not vfr", editor.is_vfr(NOV), False)
check("cfr keeps the rational", editor.cfr_args(CFR),
      ["-fps_mode", "cfr", "-r", "30000/1001"])
check("cfr keeps a plain rational too", editor.cfr_args(VFR),
      ["-fps_mode", "cfr", "-r", "30/1"])
check("cfr falls back to a number",
      editor.cfr_args({"video": {"fps": 25.0, "fps_avg": 25.0, "fps_raw": "25"}}),
      ["-fps_mode", "cfr", "-r", "25.000000"])
check("cfr gives up without a rate", editor.cfr_args(NOV), [])

print("=== audio_args_for ===")
C = config.DEFAULT_CONFIG
check("h264 + aac, no ask", encode.audio_args_for("h264", C, "aac"),
      ["-c:a", "copy"])
check("h264 + mp3, no ask", encode.audio_args_for("h264", C, "mp3"),
      ["-c:a", "copy"])
check("h264 + opus, no ask", encode.audio_args_for("h264", C, "opus"),
      ["-c:a", "aac", "-b:a", "192k"])
check("h264 + aac, 48k", encode.audio_args_for("h264", C, "aac", force_48k=True),
      ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"])
check("dnxhr + pcm, no ask", encode.audio_args_for("dnxhr", C, "pcm"),
      ["-c:a", "copy"])
check("dnxhr + pcm, 48k", encode.audio_args_for("dnxhr", C, "pcm", force_48k=True),
      ["-c:a", "pcm_s16le", "-ar", "48000"])
check("prores + aac, 48k",
      encode.audio_args_for("prores", C, "aac", force_48k=True),
      ["-c:a", "pcm_s16le", "-ar", "48000"])
check("no audio at all", encode.audio_args_for("h264", C, None, force_48k=True), [])

print("=== choose_action ===")
#            state      convert cfr    48k    want
CASES = [
    (("ok",       True,  False, False), "move"),
    (("ok",       False, False, False), "move"),
    (("ok",       True,  False, True),  "remux"),
    (("ok",       False, False, True),  "remux"),
    (("ok",       True,  True,  False), "convert"),
    (("remux",    True,  False, False), "remux"),
    (("remux",    False, False, False), "move"),
    (("remux",    True,  False, True),  "remux"),
    (("remux",    False, False, True),  "remux"),
    (("remux",    True,  True,  False), "convert"),
    (("transcode", True,  False, False), "convert"),
    (("transcode", False, False, False), "move"),
    (("transcode", False, False, True),  "convert"),
    (("transcode", False, True,  False), "convert"),
]
for (state, convert, cfr, k48), want in CASES:
    check(f"action {state} convert={convert} cfr={cfr} 48k={k48}",
          codecs.choose_action(state, convert, cfr, k48), want)

print("=== probe_file reads the new fields ===")
ffmpeg = tools.tools().get("ffmpeg")
if not ffmpeg:
    print("  [SKIP] no ffmpeg on PATH")
else:
    tmp = tempfile.mkdtemp(prefix="ytgrab-editor-")
    clip = os.path.join(tmp, "src.mp4")
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30000/1001:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", clip],
        check=True, capture_output=True, timeout=180)
    info = probe.probe_file(clip)
    check("probed fps", round(info["video"]["fps"], 3), 29.97)
    check("probed avg fps", round(info["video"]["fps_avg"], 3), 29.97)
    check("probed fps_raw", info["video"]["fps_raw"], "30000/1001")
    check("probed sample rate", info["audio"]["sample_rate"], 44100)
    check("probed channels", info["audio"]["channels"], 1)
    check("constant file is not vfr", editor.is_vfr(info), False)
    check("no clip requested means no section args",
          editor.section_args(*editor.clip_bounds({}, config.DEFAULT_CONFIG)), [])
    shutil.rmtree(tmp, ignore_errors=True)

print()
passed = N[0] - len(FAILS)
print(f"{passed}/{N[0]} checks passed")
print("RESULT:", "ALL PASS" if not FAILS else "FAILED: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
