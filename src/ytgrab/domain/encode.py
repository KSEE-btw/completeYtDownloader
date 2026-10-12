"""ffmpeg recipes for each Premiere-friendly target."""

from __future__ import annotations
# ffmpeg recipes for each Premiere-friendly target.  The h264 video side is
# built from config (see h264_video_args) so it can use a faster preset or a
# hardware encoder.
TARGETS = {
    "h264": {
        "ext": "mp4",
        "label": "H.264 / AAC  (MP4)",
        "aargs": ["-c:a", "aac", "-b:a", "192k"],
        "extra": ["-movflags", "+faststart"],
    },
    "dnxhr": {
        "ext": "mov",
        "label": "DNxHR HQ / PCM  (MOV)",
        "vargs": ["-c:v", "dnxhd", "-profile:v", "dnxhr_hq",
                  "-pix_fmt", "yuv422p"],
        "aargs": ["-c:a", "pcm_s16le"],
        "extra": [],
    },
    "prores": {
        "ext": "mov",
        "label": "ProRes 422 HQ / PCM  (MOV)",
        "vargs": ["-c:v", "prores_ks", "-profile:v", "3",
                  "-pix_fmt", "yuv422p10le"],
        "aargs": ["-c:a", "pcm_s16le"],
        "extra": [],
    },
}


def h264_video_args(cfg: dict):
    """Video-encode args for the H.264 target, honouring speed settings."""
    encoder = (cfg.get("video_encoder") or "libx264").lower()
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr",
                "-cq", "19", "-b:v", "0", "-pix_fmt", "yuv420p"]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-global_quality", "19",
                "-look_ahead", "1", "-pix_fmt", "nv12"]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp",
                "-qp_i", "19", "-qp_p", "19", "-pix_fmt", "yuv420p"]
    preset = str(cfg.get("x264_preset") or "faster")
    crf = str(cfg.get("x264_crf", 18))
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf,
            "-pix_fmt", "yuv420p"]
def video_args_for(target: str, cfg: dict):
    if target == "h264":
        return h264_video_args(cfg)
    return TARGETS[target]["vargs"]
def audio_args_for(target: str, cfg: dict, afam, force_48k: bool = False):
    """Re-encode audio only when we must; a copy is instant and lossless.

    force_48k rewrites the samples, so it rules a copy out -- an editor asked
    for 48 kHz would get 44.1 back otherwise.
    """
    plan = TARGETS[target]
    if afam is None:
        return []
    if not force_48k:
        if target == "h264":
            if afam in ("aac", "mp3"):
                return ["-c:a", "copy"]
        elif afam == "pcm":
            return ["-c:a", "copy"]
    return list(plan["aargs"]) + (["-ar", "48000"] if force_48k else [])
