"""Clip bounds, CFR decisions and the editor-friendly flags."""

from __future__ import annotations

from ytgrab.domain.timecode import format_timecode, parse_timecode
def clip_bounds(spec: dict, cfg: dict):
    """(start, end) seconds for the requested clip -- each may be None."""
    def pick(name):
        if spec.get(name) is not None and str(spec.get(name)).strip():
            return spec.get(name)
        return cfg.get(name)
    start = parse_timecode(pick("clip_start"))
    end = parse_timecode(pick("clip_end"))
    if start is not None and end is not None and end <= start:
        return start, None          # a backwards range just runs to the end
    return start, end
def section_args(start, end):
    """yt-dlp's --download-sections arguments for a clip, or [] for all of it.

    This makes yt-dlp fetch only the fragments covering the range, so a
    30-second clip of a two-hour VOD does not download two hours.  Keyframes
    are forced at the cut because without a re-encode the cut would land on
    whatever keyframe came before it.
    """
    if start is None and end is None:
        return []
    if start is None:
        span = f"*0-{format_timecode(end)}"
    elif end is None:
        span = f"*{format_timecode(start)}-inf"
    else:
        span = f"*{format_timecode(start)}-{format_timecode(end)}"
    return ["--download-sections", span, "--force-keyframes-at-cuts"]
def clip_length(editor: dict) -> float:
    """Seconds covered by the requested clip, or 0 when the end is open.

    Only used to turn ffmpeg's position into a percentage, so "unknown" is a
    perfectly good answer -- the caller falls back to an indeterminate bar.
    """
    end = editor.get("clip_end")
    if end is None:
        return 0.0
    return max(0.0, end - (editor.get("clip_start") or 0.0))
def frame_rates(info: dict):
    """(nominal, average) frames per second for the video -- 0 when unknown."""
    video = info.get("video") or {}
    return video.get("fps") or 0.0, video.get("fps_avg") or 0.0
def is_vfr(info: dict) -> bool:
    """True when the stream's frame rate wanders -- the NLE drift case."""
    nominal, average = frame_rates(info)
    if nominal <= 0 or average <= 0:
        return False
    return abs(nominal - average) > max(0.05, average * 0.002)
def cfr_args(info: dict):
    """ffmpeg arguments that pin the output to a constant frame rate.

    The source's own rate is kept (as the exact rational ffprobe reported, so
    29.97 stays 30000/1001); only the *variance* is removed.
    """
    raw = ((info.get("video") or {}).get("fps_raw") or "").strip()
    if "/" in raw:
        return ["-fps_mode", "cfr", "-r", raw]
    nominal, _ = frame_rates(info)
    if nominal <= 0:
        return []
    return ["-fps_mode", "cfr", "-r", f"{nominal:.6f}"]
def editor_flags(spec: dict, cfg: dict) -> dict:
    """What the editor asked for, resolved against the config defaults."""
    def flag(name, fallback=False):
        value = spec.get(name)
        if value is None or value == "":
            value = cfg.get(name, fallback)
        if isinstance(value, str):
            return value.strip().lower() not in ("", "false", "0", "no", "off")
        return bool(value)
    start, end = clip_bounds(spec, cfg)
    return {"clip_start": start, "clip_end": end,
            "force_cfr": flag("force_cfr"), "audio_48k": flag("audio_48k")}
