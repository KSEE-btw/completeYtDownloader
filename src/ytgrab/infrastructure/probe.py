"""Inspecting a finished file with ffprobe."""

from __future__ import annotations

import json
import subprocess

from ytgrab.domain.codecs import audio_family, video_family
from ytgrab.domain.timecode import _int, _ratio
from ytgrab.infrastructure.tools import child_kwargs, tools
def probe_file(path: str) -> dict:
    exe = tools()["ffprobe"]
    if not exe:
        raise RuntimeError("ffprobe was not found on PATH")
    args = [exe, "-v", "error", "-show_entries",
            "stream=index,codec_type,codec_name,profile,width,height,"
            "r_frame_rate,avg_frame_rate,sample_rate,channels",
            "-show_entries", "format=format_name,duration,bit_rate",
            "-of", "json", path]

    def _run(extra):
        return subprocess.run(args + extra, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120,
                              **child_kwargs())

    proc = _run([])
    if proc.returncode != 0:
        # retry without the container-header assumptions (mkv/webm edge cases)
        proc = _run(["-f", "matroska"])
        if proc.returncode != 0:
            proc = _run(["-f", "mpegts"])
    data = json.loads(proc.stdout or "{}")
    result = {"video": None, "audio": None, "container": "",
              "duration": 0.0, "format_name": ""}
    fmt = data.get("format") or {}
    result["format_name"] = fmt.get("format_name", "")
    result["container"] = (fmt.get("format_name", "").split(",")[0] or "")
    try:
        result["duration"] = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        result["duration"] = 0.0
    for stream in data.get("streams") or []:
        fam = (video_family(stream.get("codec_name"))
               if stream.get("codec_type") == "video" else None)
        if stream.get("codec_type") == "video" and result["video"] is None:
            result["video"] = {
                "family": fam,
                "name": stream.get("codec_name"),
                "width": stream.get("width"),
                "height": stream.get("height"),
                "fps": _ratio(stream.get("r_frame_rate")),
                "fps_avg": _ratio(stream.get("avg_frame_rate")),
                "fps_raw": (stream.get("r_frame_rate") or "").strip(),
            }
        elif stream.get("codec_type") == "audio" and result["audio"] is None:
            result["audio"] = {
                "family": audio_family(stream.get("codec_name")),
                "name": stream.get("codec_name"),
                "sample_rate": _int(stream.get("sample_rate")),
                "channels": _int(stream.get("channels")),
            }
    return result
