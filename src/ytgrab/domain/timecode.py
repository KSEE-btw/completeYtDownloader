"""Parsing and formatting timecodes."""

from __future__ import annotations
def _int(value) -> int:
    try:
        return int(str(value if value is not None else "").strip() or 0)
    except (TypeError, ValueError):
        return 0
def _ratio(value) -> float:
    """ffprobe's "30000/1001" (or a plain number) as a float."""
    try:
        num, sep, den = str(value or "").partition("/")
        if sep:
            den = float(den)
            return float(num) / den if den else 0.0
        return float(num)
    except (TypeError, ValueError):
        return 0.0
def parse_timecode(value):
    """Seconds from "90", "1:30" or "0:01:30.5"; None when it is not one.

    Typed by hand, so a trailing "s", stray spaces and a comma decimal mark
    all pass.  Blank or nonsense returns None, meaning "no bound".
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    text = str(value).strip().rstrip("sS").replace(",", ".").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        return None
    total = 0.0
    for part in parts:
        try:
            piece = float(part.strip())
        except ValueError:
            return None
        if piece < 0:
            return None
        total = total * 60.0 + piece
    return total
def format_timecode(seconds: float) -> str:
    """90 -> "1:30", 3661.25 -> "1:01:01.250" (the inverse of the above)."""
    whole = int(seconds)
    millis = int(round((seconds - whole) * 1000))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    head = (f"{hours}:{minutes:02d}:{secs:02d}" if hours
            else f"{minutes}:{secs:02d}")
    return f"{head}.{millis:03d}" if millis else head
