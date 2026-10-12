"""Parsing yt-dlp / ffmpeg progress lines."""

from __future__ import annotations

import re
def _ytdlp_progress_event(parts):
    def num(i):
        try:
            return float(parts[i])
        except (IndexError, ValueError):
            return 0.0
    done, total, est, speed, eta = (num(0), num(1), num(2), num(3), num(4))
    if not total:
        total = est
    pct = (done / total * 100) if total else 0
    return {"type": "progress", "phase": "downloading",
            "percent": max(0.0, min(100.0, pct)), "downloaded": done,
            "total": total, "speed": speed, "eta": eta}
_FFMPEG_TIME_RE = re.compile(r"\btime=(\d+):([0-5]\d):([0-5]\d(?:\.\d+)?)\b")
_FFMPEG_SPEED_RE = re.compile(r"\bspeed=\s*([\d.]+)x")
_TIME_RANGE_RE = re.compile(r"time ranges?:\s*([\d.]+)\s*-\s*([\d.]+)")
def _is_ffmpeg_status(line: str) -> bool:
    """True for ffmpeg's own "frame=... time=... speed=..." progress line."""
    return "time=" in line and ("frame=" in line or "speed=" in line)
def _ffmpeg_progress_event(line: str, section: float, seen: float):
    """Download progress for a clip, read off ffmpeg's status line.

    --download-sections hands the whole clip to ffmpeg, so yt-dlp's own
    progress hook fires exactly once, at the very end: without this the bar sat
    at 0% for the entire download.  ffmpeg's periodic status line rides along
    on the same output and counts up through the section, which is what this
    turns back into a percentage.  Returns None when there is nothing new to
    report (no length known yet, a repeated position, or "time=N/A").
    """
    if section <= 0:
        return None
    match = _FFMPEG_TIME_RE.search(line)
    if not match:
        return None
    seconds = (int(match.group(1)) * 3600 + int(match.group(2)) * 60
               + float(match.group(3)))
    if seconds <= seen:
        return None
    speed = _FFMPEG_SPEED_RE.search(line)
    speed = float(speed.group(1)) if speed else 0.0
    return {"type": "progress", "phase": "downloading",
            "percent": min(100.0, seconds / section * 100.0),
            "downloaded": 0, "total": 0, "seconds": seconds, "speed": 0,
            "eta": ((section - seconds) / speed if speed > 0 else 0.0)}
def _section_length(line: str) -> float:
    """The span yt-dlp reports for the section it is about to fetch.

    A clip with no end ("*1:30-inf") stays unknown, but an end past the end of
    the video comes back clamped, which is worth picking up.
    """
    match = _TIME_RANGE_RE.search(line)
    if not match:
        return 0.0
    start, end = float(match.group(1)), float(match.group(2))
    return max(0.0, end - start) if end > start else 0.0
def _last_error(tail) -> str:
    """Pick the most useful line out of yt-dlp's trailing output."""
    for line in reversed(tail):
        if "ERROR" in line.upper():
            text = re.sub(r"^\[[^\]]+\]\s*", "", line)
            text = text.split("ERROR:", 1)[-1].strip()
            return text or line
    return tail[-1] if tail else ""
