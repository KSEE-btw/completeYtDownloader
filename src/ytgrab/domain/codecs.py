"""Codec families, Premiere compatibility, and what to do about it."""

from __future__ import annotations
PREMIERE_VIDEO = {"h264", "hevc"}
PREMIERE_AUDIO = {"aac", "mp3", "pcm"}
PREMIERE_CONTAINERS = {"mp4", "mov", "m4v", "mxf"}
PREMIERE_AUDIO_CONTAINERS = {"m4a", "mp4", "mov", "aac", "mp3", "mxf"}


def video_family(vcodec: str):
    v = (vcodec or "").lower()
    if v in ("", "none"):
        return None
    if v.startswith(("avc1", "h264", "x264")):
        return "h264"
    if v.startswith(("hev1", "hvc1")) or "hevc" in v:
        return "hevc"
    if v.startswith(("vp09", "vp9", "vp8")):
        return "vp9"
    if v.startswith("av01") or "av1" in v:
        return "av1"
    if v.startswith("dnx"):
        return "dnxhd"
    if v.startswith(("prores", "ap4", "apc", "apc")):
        return "prores"
    return v.split(".")[0]
def audio_family(acodec: str):
    a = (acodec or "").lower()
    if a in ("", "none"):
        return None
    if a.startswith("mp4a.69"):
        return "mp3"
    if a.startswith(("mp4a", "aac")):
        return "aac"
    if a.startswith("opus"):
        return "opus"
    if a.startswith("vorbis"):
        return "vorbis"
    if a.startswith("mp3"):
        return "mp3"
    if a.startswith(("pcm", "lpcm")):
        return "pcm"
    if a.startswith("flac"):
        return "flac"
    return a.split(".")[0]
def compat_state(vfam, afam, ext: str) -> str:
    """ok | remux | transcode  -- what Premiere needs for this file."""
    ext = (ext or "").lower()
    if vfam is None:                      # audio-only stream
        if afam is None:
            return "transcode"
        if afam in PREMIERE_AUDIO:
            return "ok" if ext in PREMIERE_AUDIO_CONTAINERS else "remux"
        return "transcode"
    codecs_ok = (vfam in PREMIERE_VIDEO
                 and (afam is None or afam in PREMIERE_AUDIO))
    if codecs_ok and ext in PREMIERE_CONTAINERS:
        return "ok"
    if codecs_ok:
        return "remux"       # right codecs, repackage only (fast)
    return "transcode"       # needs a real re-encode


def choose_action(state: str, want_convert: bool, need_cfr: bool,
                  need_48k: bool) -> str:
    """move | remux | convert -- what has to happen to the file that landed.

    `state` is what compat_state says about the source.  The editor's asks win
    over the "it is already fine" shortcut: a constant frame rate can only be
    rebuilt, and a 48 kHz audio track has to be rewritten.
    """
    if need_cfr:
        return "convert"
    if state == "remux" and want_convert:
        return "remux"
    if state in ("ok", "remux") and need_48k:
        return "remux"              # keep the video, rewrite the audio
    if state == "ok":
        return "move"
    if want_convert or need_48k:
        return "convert"
    return "move"
