"""Turning bytes, speeds and specs into row text."""

from __future__ import annotations

import os

from ytgrab.domain.editor import clip_bounds
from ytgrab.domain.timecode import format_timecode
def fmt_bytes(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    return f"{n:.1f} {units[i]}" if i and n < 10 else f"{n:.0f} {units[i]}"
def fmt_speed(n) -> str:
    b = fmt_bytes(n)
    return f"{b}/s" if b else ""
def fmt_eta(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"
def clip_note(spec) -> str:
    """ "clip 1:30\u20132:15" when the item only wants part of the video.

    The popup pre-fills the clip row from the stored settings and sends it with
    every download, so the spec is enough to describe the range.
    """
    start, end = clip_bounds(spec or {}, {})
    if start is None and end is None:
        return ""
    left = format_timecode(start) if start is not None else "0:00"
    right = format_timecode(end) if end is not None else "end"
    return f"clip {left}\u2013{right}"
def detail_text(item) -> str:
    status = item["status"]
    if status == "queued":
        note = clip_note(item.get("spec"))
        return "Waiting in the queue" + (f" \u00b7 {note}" if note else "")
    if status == "running":
        note = clip_note(item.get("spec"))
        tail = f" \u00b7 {note}" if note else ""
        if item["stage"] == "downloading":
            if note and item["percent"] <= 0:
                # A clip is cut by ffmpeg, which reports no position until its
                # first status line, so a determined "0%" here reads as a stall.
                return "Downloading the clip" + tail
            bits = [f"Downloading {item['percent']:.0f}%"]
            if item["total"]:
                bits.append(f"of {fmt_bytes(item['total'])}")
            if item["speed"]:
                bits.append(fmt_speed(item["speed"]))
            if item["eta"]:
                bits.append(f"ETA {fmt_eta(item['eta'])}")
            return "   ".join(bits) + tail
        return (item["stage_label"] or "Working...") + tail
    if status == "done":
        name = os.path.basename(item["path"]) if item["path"] else ""
        note = item["note"] or "saved"
        head = "Already Premiere-compatible" if item["mode"] == "none" else "Done"
        out = f"{head} \u00b7 {note}"
        if name:
            out += f" \u00b7 {name}"
        return out
    if status == "error":
        return "Failed: " + (item["error"] or "unknown error")
    if status == "cancelled":
        return "Cancelled"
    if status == "paused":
        return "Paused \u2014 press Resume to carry on"
    return status
