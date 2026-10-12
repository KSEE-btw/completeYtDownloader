"""Turning yt-dlp format data into what the UI shows."""

from __future__ import annotations

import re

from ytgrab.domain.codecs import audio_family, compat_state, video_family
# format selectors the UI offers as the two "Auto" entries
SELECTOR_PREMIERE = ("bv*[vcodec^=avc1]+ba[acodec^=mp4a]"
                     "/b[ext=mp4][vcodec^=avc1]/bv*+ba/b")
SELECTOR_BEST = "bv*+ba/b"


def _size_of(fmt: dict):
    return (fmt.get("filesize") or fmt.get("filesize_approx")
            or (fmt.get("filesize") if fmt.get("filesize") else None))
def simplify_formats(info: dict) -> dict:
    """Turn a yt-dlp info dict into something small enough to send."""
    raw = info.get("formats") or []
    best = {}
    for fmt in raw:
        vfam = video_family(fmt.get("vcodec"))
        afam = audio_family(fmt.get("acodec"))
        if vfam is None and afam is None:
            continue                       # yt-dlp placeholder, skip
        ext = (fmt.get("ext") or "").lower()
        if vfam is not None and not fmt.get("height"):
            continue
        # group key: same quality tier / codecs / container / hdr
        key = (fmt.get("height"), vfam, afam, ext,
               fmt.get("dynamic_range"))
        rank = (1 if _size_of(fmt) else 0,
                (fmt.get("tbr") or 0) + (fmt.get("abr") or 0))
        prev = best.get(key)
        if prev is None or rank > prev[0]:
            best[key] = (rank, fmt)

    out = []
    for _, fmt in best.values():
        vfam = video_family(fmt.get("vcodec"))
        afam = audio_family(fmt.get("acodec"))
        ext = (fmt.get("ext") or "").lower()
        height = fmt.get("height")
        state = compat_state(vfam, afam, ext)
        kind = ("audio" if vfam is None
                else ("video" if afam is None else "av"))
        out.append({
            "id": fmt.get("format_id"),
            "kind": kind,
            "ext": ext,
            "height": height,
            "width": fmt.get("width"),
            "fps": fmt.get("fps"),
            "vcodec": vfam,
            "acodec": afam,
            "vcodec_raw": fmt.get("vcodec"),
            "acodec_raw": fmt.get("acodec"),
            "hdr": fmt.get("dynamic_range"),
            "note": fmt.get("format_note"),
            "size": _size_of(fmt),
            "tbr": fmt.get("tbr"),
            "abr": fmt.get("abr"),
            "compat": state,        # ok | remux | transcode
            "label": _fmt_label(height, vfam, afam, ext, fmt),
        })

    def sort_key(item):
        order = {"av": 0, "video": 1, "audio": 2}[item["kind"]]
        return (order, -(item["height"] or 0), -(item["tbr"] or 0))

    out.sort(key=sort_key)
    return {
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "video_id": info.get("id"),
        "thumbnail": info.get("thumbnail"),
        "is_live": bool(info.get("is_live")),
        "formats": out,
    }
def _fmt_label(height, vfam, afam, ext, fmt) -> str:
    if height:
        head = f"{height}p"
        if fmt.get("fps") and float(fmt.get("fps") or 0) >= 50:
            head += str(int(float(fmt["fps"])))
    else:
        head = "audio"
    bits = [head, (vfam or "?").upper()]
    if afam:
        bits.append(afam.upper())
    head = " ".join(bits)
    if fmt.get("dynamic_range") and fmt["dynamic_range"] != "SDR":
        head += f" [{fmt['dynamic_range']}]"
    return f"{head}  .{ext}"


_BARE_FORMAT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
def ensure_audio_selector(selector: str, enabled: bool = True) -> str:
    """Make an explicit video-only pick carry the best audio track.

    The UI's explicit choices are bare format ids, and a video-only one has no
    sound of its own, so "160" downloads silently.  yt-dlp resolves
    "160+ba/160" to video + best audio when 160 is video-only, and to 160 alone
    when it already carries audio (or when the video is genuinely silent), so
    this is a no-op for muxed and audio-only picks.  Auto selectors already
    contain "+ba" and are left untouched.
    """
    sel = (selector or "").strip()
    if not enabled or sel == "auto" or not _BARE_FORMAT_RE.match(sel):
        return selector
    return f"{sel}+ba/{sel}"
