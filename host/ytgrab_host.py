#!/usr/bin/env python3
"""ytgrab native-messaging host.

A tiny stdlib-only companion that drives yt-dlp + ffmpeg on behalf of the
Firefox extension.  Talks the native messaging protocol on stdin/stdout:

    <uint32 little-endian length><utf-8 json>

Commands from the extension
---------------------------
    {"cmd": "ping",         "id": "..."}
    {"cmd": "list_formats", "id": "...", "url": "..."}
    {"cmd": "download",     "id": "...", "url": "...", "format": "...",
     "convert": true, "target": "h264|dnxhr|prores",
     "outdir": "...", "filename": "...", "keep_original": false}
    {"cmd": "cancel",       "id": "...", "job": "..."}
    {"cmd": "get_config",   "id": "..."}
    {"cmd": "set_config",   "id": "...", "config": {...}}

Everything the host sends back carries the id it is answering.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

# --------------------------------------------------------------------------- #
# constants / targets
# --------------------------------------------------------------------------- #

APP = "ytgrab"
VERSION = "0.2.0"

# native messaging messages are capped at 1 MiB
MAX_MSG = 1024 * 1024 - 16

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", APP)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    # where finished files land ("" => ~/Downloads)
    "outdir": os.path.join(os.path.expanduser("~"), "Downloads"),
    # auto | premiere_safe | best  (what the "Auto" entry in the UI downloads)
    "default_mode": "premiere_safe",
    # default conversion target when the user ticks the box
    "default_target": "h264",
    # delete the non-Premiere source after a successful conversion
    "keep_original": False,
    # cookies ("" = off) e.g. "firefox" for age-restricted / member videos
    "cookies_from_browser": "",
    # refuse a playlist URL instead of grabbing the whole thing
    "force_no_playlist": True,
    # extra raw yt-dlp args
    "extra_ytdlp_args": [],

    # ---- speed (all quality-neutral by default) ----
    # parallel DASH fragment downloads; the single biggest download speedup
    "concurrent_fragments": 8,
    # split HTTP ranges for throttled hosts ("" = leave yt-dlp's default)
    "http_chunk_size": "",
    # auto | true | false  -- use aria2c when it is installed for multi-conn
    "use_aria2c": "auto",
    # x264 speed/size trade: preset does not change quality at a fixed CRF
    "x264_preset": "faster",
    "x264_crf": 18,
    # decode with the GPU when possible (no effect on output)
    "hwaccel": True,
    # libx264 | h264_nvenc | h264_qsv | h264_amf  (hardware is faster but
    # slightly less efficient per bitrate -- opt in explicitly)
    "video_encoder": "libx264",

    # binary overrides ("" = look up on PATH)
    "ytdlp": "",
    "ffmpeg": "",
    "ffprobe": "",
    "aria2c": "",
}

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

# format selectors the UI offers as the two "Auto" entries
SELECTOR_PREMIERE = ("bv*[vcodec^=avc1]+ba[acodec^=mp4a]"
                     "/b[ext=mp4][vcodec^=avc1]/bv*+ba/b")
SELECTOR_BEST = "bv*+ba/b"

PREMIERE_VIDEO = {"h264", "hevc"}
PREMIERE_AUDIO = {"aac", "mp3", "pcm"}
PREMIERE_CONTAINERS = {"mp4", "mov", "m4v", "mxf"}
PREMIERE_AUDIO_CONTAINERS = {"m4a", "mp4", "mov", "aac", "mp3", "mxf"}

DEBUG = bool(os.environ.get("YTGRAB_DEBUG"))

# --------------------------------------------------------------------------- #
# logging / stdio plumbing
# --------------------------------------------------------------------------- #

if os.name == "nt":
    import msvcrt
    for _stream in (sys.stdin, sys.stdout):
        try:
            msvcrt.setmode(_stream.fileno(), os.O_BINARY)
        except (OSError, ValueError, AttributeError):
            pass

_write_lock = threading.Lock()


def log(*parts: object) -> None:
    if DEBUG:
        print("[ytgrab]", *parts, file=sys.stderr, flush=True)


def read_message():
    """Read one native-messaging message, or None at end of stream."""
    header = sys.stdin.buffer.read(4)
    if len(header) < 4:
        return None
    size = int.from_bytes(header, "little")
    if size <= 0:
        return None
    buf = bytearray()
    while len(buf) < size:
        chunk = sys.stdin.buffer.read(size - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    try:
        return json.loads(buf.decode("utf-8"))
    except Exception as exc:  # pragma: no cover - malformed input
        log("bad json:", exc)
        return {}


def send(obj: dict) -> None:
    """Write one native-messaging message."""
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_MSG:
        payload = json.dumps(
            {"type": "error", "message": "response too large"})
        payload = payload.encode("utf-8")
    with _write_lock:
        try:
            sys.stdout.buffer.write(len(payload).to_bytes(4, "little"))
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()
        except (BrokenPipeError, OSError):
            log("stdout closed")
            os._exit(0)


def reply(msg_id, **fields) -> None:
    out = dict(fields)
    out["id"] = msg_id          # the request id always wins
    send(out)


def fail(msg_id, message: str, **fields) -> None:
    out = {"type": "error", "id": msg_id, "message": message}
    out.update(fields)
    send(out)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #

_config_lock = threading.Lock()
_config_cache = None


def load_config() -> dict:
    global _config_cache
    with _config_lock:
        if _config_cache is not None:
            return _config_cache
        cfg = dict(DEFAULT_CONFIG)
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                cfg.update(json.load(fh))
        except FileNotFoundError:
            pass
        except Exception as exc:
            log("config read failed:", exc)
        _config_cache = cfg
        return cfg


def save_config(patch: dict) -> dict:
    global _config_cache
    cfg = dict(load_config())
    for key, value in (patch or {}).items():
        if key in DEFAULT_CONFIG:
            cfg[key] = value
    with _config_lock:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
        _config_cache = cfg
    return cfg


# --------------------------------------------------------------------------- #
# tool discovery
# --------------------------------------------------------------------------- #

def _which(name: str, override: str = "") -> str:
    if override:
        candidate = os.path.expandvars(os.path.expanduser(override))
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        exe = name if name.lower().endswith(".exe") else name + ".exe"
        for base in (
            os.path.join(os.environ.get("LOCALAPPDATA", ""),
                         r"Microsoft\WinGet\Links"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""),
                         r"Microsoft\WindowsApps"),
            r"C:\ffmpeg\bin",
        ):
            candidate = os.path.join(base, exe)
            if os.path.isfile(candidate):
                return candidate
    return ""


def tools() -> dict:
    cfg = load_config()
    return {
        "ytdlp": _which("yt-dlp", cfg.get("ytdlp", "")),
        "ffmpeg": _which("ffmpeg", cfg.get("ffmpeg", "")),
        "ffprobe": _which("ffprobe", cfg.get("ffprobe", "")),
        "aria2c": _which("aria2c", cfg.get("aria2c", "")),
    }


def tool_versions() -> dict:
    found = tools()
    info = {}
    for key, args in (("ytdlp", ["--version"]),
                      ("ffmpeg", ["-version"]),
                      ("ffprobe", ["-version"]),
                      ("aria2c", ["--version"])):
        path = found.get(key, "")
        if not path:
            info[key] = {"path": "", "version": ""}
            continue
        try:
            out = subprocess.run([path] + args, capture_output=True, text=True,
                                 timeout=20, errors="replace",
                                 **child_kwargs()).stdout
            info[key] = {"path": path,
                         "version": out.strip().splitlines()[0] if out else ""}
        except Exception as exc:
            info[key] = {"path": path, "version": "", "error": str(exc)}
    return info


# --------------------------------------------------------------------------- #
# codec helpers
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# encoder recipes
# --------------------------------------------------------------------------- #

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


def audio_args_for(target: str, cfg: dict, afam):
    """Re-encode audio only when we must; a stream copy is instant and lossless."""
    plan = TARGETS[target]
    if afam is None:
        return []
    if target == "h264":
        if afam == "aac":
            return ["-c:a", "copy"]
        if afam == "mp3":
            return ["-c:a", "copy"]          # mp3 in mp4 is legal
    elif afam == "pcm":
        return ["-c:a", "copy"]
    return plan["aargs"]


def download_speed_args(cfg: dict):
    args = []
    try:
        frags = int(cfg.get("concurrent_fragments") or 1)
    except (TypeError, ValueError):
        frags = 1
    if frags > 1:
        args += ["--concurrent-fragments", str(frags)]
    chunk = (cfg.get("http_chunk_size") or "").strip()
    if chunk:
        args += ["--http-chunk-size", chunk]
    want_aria = str(cfg.get("use_aria2c", "auto")).lower()
    if want_aria not in ("false", "0", "off", "no"):
        if tools().get("aria2c"):
            args += ["--downloader", "aria2c",
                     "--downloader-args",
                     "aria2c:-x16 -s16 -k1M --file-allocation=none"]
    return args


# --------------------------------------------------------------------------- #
# probing / format listing
# --------------------------------------------------------------------------- #

def ytdlp_base_args(cfg: dict, progress: bool = False):
    args = ["--no-warnings", "--color", "never"]
    if not progress:
        args.append("--no-progress")
    if cfg.get("force_no_playlist"):
        args.append("--no-playlist")
    cookies = (cfg.get("cookies_from_browser") or "").strip()
    if cookies:
        args += ["--cookies-from-browser", cookies]
    args += list(cfg.get("extra_ytdlp_args") or [])
    return args


def run_ytdlp_json(url: str, timeout: int = 150) -> dict:
    cfg = load_config()
    exe = tools()["ytdlp"]
    if not exe:
        raise RuntimeError("yt-dlp was not found on PATH")
    args = ([exe] + ytdlp_base_args(cfg)
            + ["--dump-single-json", "--skip-download", url])
    log("probe:", " ".join(args))
    proc = subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          **child_kwargs())
    if proc.returncode != 0 or not proc.stdout.strip():
        tail = (proc.stderr or "").strip().splitlines()
        raise RuntimeError(tail[-1] if tail else "yt-dlp could not read this URL")
    return json.loads(proc.stdout)


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


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #

class Job:
    def __init__(self, job_id: str):
        self.id = job_id
        self.proc = None
        self.cancelled = False
        self.lock = threading.Lock()

    def attach(self, proc):
        with self.lock:
            self.proc = proc

    def cancel(self):
        self.cancelled = True
        with self.lock:
            proc = self.proc
        if proc and proc.poll() is None:
            kill_tree(proc)


JOBS: dict = {}
JOBS_LOCK = threading.Lock()


def register(job: Job) -> None:
    with JOBS_LOCK:
        JOBS[job.id] = job


def unregister(job: Job) -> None:
    with JOBS_LOCK:
        JOBS.pop(job.id, None)


def kill_tree(proc) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, **child_kwargs())
        else:
            proc.terminate()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# filename helpers
# --------------------------------------------------------------------------- #

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, fallback: str = "video") -> str:
    name = _ILLEGAL.sub("_", (name or "").strip())
    name = name.rstrip(" .")
    if not name:
        name = fallback
    return name[:120]


def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


def expand(path: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def child_kwargs() -> dict:
    """Popen options that keep child processes detached from our stdio.

    yt-dlp/ffmpeg must never read the native-messaging stdin, and on Windows
    they should not pop a console window.
    """
    kw = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kw


# --------------------------------------------------------------------------- #
# ffprobe / ffmpeg
# --------------------------------------------------------------------------- #

def probe_file(path: str) -> dict:
    exe = tools()["ffprobe"]
    if not exe:
        raise RuntimeError("ffprobe was not found on PATH")
    args = [exe, "-v", "error", "-show_entries",
            "stream=index,codec_type,codec_name,profile,width,height",
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
            }
        elif stream.get("codec_type") == "audio" and result["audio"] is None:
            result["audio"] = {
                "family": audio_family(stream.get("codec_name")),
                "name": stream.get("codec_name"),
            }
    return result


def _ffmpeg_run(job: Job, args, total_seconds: float, phase: str,
                msg_id) -> None:
    exe = tools()["ffmpeg"]
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-nostats",
           "-progress", "pipe:1"] + args
    log("ffmpeg:", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1,
                            encoding="utf-8", errors="replace",
                            **child_kwargs())
    job.attach(proc)
    for line in proc.stdout:
        line = line.strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key in ("out_time_us", "out_time_ms"):
            try:
                seconds = int(value) / 1_000_000
            except ValueError:
                continue
            pct = (seconds / total_seconds * 100) if total_seconds else 0
            reply(msg_id, type="progress", job=job.id, phase=phase,
                  percent=max(0.0, min(100.0, pct)), seconds=seconds,
                  total=total_seconds)
        elif key == "progress" and value == "end":
            reply(msg_id, type="progress", job=job.id, phase=phase,
                  percent=100.0, seconds=total_seconds, total=total_seconds)
    proc.wait()
    err = (proc.stderr.read() or "").strip().splitlines()
    if proc.returncode != 0:
        raise RuntimeError(err[-1] if err else "ffmpeg failed")


# --------------------------------------------------------------------------- #
# the download pipeline
# --------------------------------------------------------------------------- #

def download_worker(msg: dict) -> None:
    msg_id = msg.get("id")
    job = Job(msg_id or uuid.uuid4().hex)
    register(job)
    tmpdir = None
    try:
        cfg = load_config()
        url = (msg.get("url") or "").strip()
        if not url:
            raise RuntimeError("no URL given")

        exe = tools()["ytdlp"]
        if not exe:
            raise RuntimeError("yt-dlp was not found on PATH")

        selector = (msg.get("format") or "").strip()
        if not selector or selector == "auto":
            mode = msg.get("mode") or cfg.get("default_mode", "premiere_safe")
            selector = (SELECTOR_BEST if mode == "best" else SELECTOR_PREMIERE)

        outdir = expand(msg.get("outdir") or cfg.get("outdir")
                        or DEFAULT_CONFIG["outdir"])
        os.makedirs(outdir, exist_ok=True)

        # Keep the scratch file inside outdir so the final move is a rename on
        # the same volume instead of a second full copy.
        tmpdir = os.path.join(outdir, f".ytgrab-{job.id[:8]}")
        shutil.rmtree(tmpdir, ignore_errors=True)
        os.makedirs(tmpdir, exist_ok=True)

        reply(msg_id, type="stage", job=job.id, stage="downloading",
              stage_label="Downloading from the web")

        args = ([exe] + ytdlp_base_args(cfg, progress=True)
                + download_speed_args(cfg)
                + ["-f", selector,
                   "--newline",
                   "-o", os.path.join(tmpdir, "%(title)s.%(ext)s"),
                   "--windows-filenames", "--trim-filenames", "120",
                   "--progress-template",
                   ("download:@@P@@%(progress.downloaded_bytes|0)s "
                    "%(progress.total_bytes|0)s "
                    "%(progress.total_bytes_estimate|0)s "
                    "%(progress.speed|0)s "
                    "%(progress.eta|0)s"),
                   url])
        log("download:", " ".join(args))
        proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1,
                                encoding="utf-8", errors="replace",
                                **child_kwargs())
        job.attach(proc)
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if line.startswith("@@P@@"):
                _emit_ytdlp_progress(msg_id, job.id, line[5:].split())
            elif line.startswith("[Merger]") or "Merging formats" in line:
                reply(msg_id, type="stage", job=job.id, stage="merging",
                      stage_label="Muxing video + audio")
        proc.wait()
        if job.cancelled:
            raise _Cancelled()
        if proc.returncode != 0:
            raise RuntimeError("yt-dlp exited with an error (see the log)")

        source = _pick_output(tmpdir)
        if not source:
            raise RuntimeError("the download finished but no file was found")

        reply(msg_id, type="stage", job=job.id, stage="checking",
              stage_label="Checking codecs")

        info = probe_file(source)
        vfam = (info["video"] or {}).get("family")
        afam = (info["audio"] or {}).get("family")
        ext = os.path.splitext(source)[1].lstrip(".").lower()
        state = compat_state(vfam, afam, ext)

        want_convert = bool(msg.get("convert", True))
        target = (msg.get("target") or cfg.get("default_target") or "h264")
        target = target if target in TARGETS else "h264"

        codec_txt = "/".join(filter(None, [
            (info["video"] or {}).get("name"),
            (info["audio"] or {}).get("name")])) or "unknown"

        if not want_convert or state == "ok":
            dest = unique_path(os.path.join(outdir, os.path.basename(source)))
            shutil.move(source, dest)
            reply(msg_id, type="done", job=job.id, path=dest,
                  mode="none", codecs=codec_txt,
                  note=("already Premiere-compatible"
                        if not want_convert else "no conversion needed"))
            return

        plan = TARGETS[target]
        base = os.path.splitext(os.path.basename(source))[0]
        final = unique_path(os.path.join(outdir, f"{base}.{plan['ext']}"))
        duration = info["duration"] or 0.0

        # Decoding on the GPU is free speed and cannot change the output.
        pre = ["-hwaccel", "auto"] if cfg.get("hwaccel", True) else []
        vargs = video_args_for(target, cfg)
        aargs = audio_args_for(target, cfg, afam)

        if state == "remux":
            reply(msg_id, type="stage", job=job.id, stage="remuxing",
                  stage_label="Repackaging into a Premiere container (fast)")
            _ffmpeg_run(job, ["-y", "-i", source,
                              "-map", "0:v:0", "-map", "0:a:0?",
                              "-c", "copy"] + plan["extra"] + [final],
                        duration, "remuxing", msg_id)
        else:
            reply(msg_id, type="stage", job=job.id, stage="converting",
                  stage_label=f"Converting to {plan['label']}")
            convert_args = (["-y", "-i", source,
                             "-map", "0:v:0", "-map", "0:a:0?"]
                            + vargs + aargs
                            + ["-map_metadata", "0", "-map_chapters", "0"]
                            + plan["extra"] + [final])
            try:
                _ffmpeg_run(job, pre + convert_args, duration, "converting",
                            msg_id)
            except Exception as exc:
                # Not every GPU can decode every codec; retry in software.
                if not pre or job.cancelled:
                    raise
                log("hwaccel failed, retrying on CPU:", exc)
                try:
                    os.remove(final)
                except OSError:
                    pass
                reply(msg_id, type="stage", job=job.id, stage="converting",
                      stage_label="Converting on CPU (GPU decode unavailable)")
                _ffmpeg_run(job, convert_args, duration, "converting", msg_id)

        if job.cancelled:
            raise _Cancelled()

        keep = msg.get("keep_original")
        if keep is None:
            keep = cfg.get("keep_original", False)
        if keep:
            shutil.move(source, unique_path(
                os.path.join(outdir, os.path.basename(source))))
        detail = f"converted from {codec_txt}"
        if aargs == ["-c:a", "copy"]:
            detail += " (audio copied)"
        reply(msg_id, type="done", job=job.id, path=final,
              mode=state, codecs=codec_txt, note=detail)
    except _Cancelled:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        fail(msg_id, "cancelled", job=job.id, cancelled=True)
    except Exception as exc:  # noqa: BLE001 - report everything to the UI
        log("job failed:", repr(exc))
        if DEBUG:
            import traceback
            traceback.print_exc(file=sys.stderr)
        fail(msg_id, str(exc) or exc.__class__.__name__, job=job.id)
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        unregister(job)


class _Cancelled(Exception):
    pass


def _emit_ytdlp_progress(msg_id, job_id: str, parts) -> None:
    def num(i):
        try:
            return float(parts[i])
        except (IndexError, ValueError):
            return 0.0
    done, total, est, speed, eta = (num(0), num(1), num(2), num(3), num(4))
    if not total:
        total = est
    pct = (done / total * 100) if total else 0
    reply(msg_id, type="progress", job=job_id, phase="downloading",
          percent=max(0.0, min(100.0, pct)), downloaded=done, total=total,
          speed=speed, eta=eta)


def _pick_output(tmpdir: str):
    exts = {".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".opus", ".flac"}
    best = None
    for name in os.listdir(tmpdir):
        path = os.path.join(tmpdir, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in exts:
            continue
        if best is None or os.path.getmtime(path) > os.path.getmtime(best):
            best = path
    return best


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

def handle(msg: dict) -> None:
    cmd = msg.get("cmd")
    msg_id = msg.get("id")

    if cmd == "ping":
        info = tool_versions()
        reply(msg_id, type="pong", app=APP, version=VERSION,
              tools=info, config=load_config(),
              targets={k: v["label"] for k, v in TARGETS.items()},
              ok=bool(info["ytdlp"]["path"] and info["ffmpeg"]["path"]))
        return

    if cmd == "get_config":
        reply(msg_id, type="config", config=load_config())
        return

    if cmd == "set_config":
        reply(msg_id, type="config",
              config=save_config(msg.get("config") or {}))
        return

    if cmd == "list_formats":
        try:
            info = run_ytdlp_json(msg.get("url", ""))
            reply(msg_id, type="formats", **simplify_formats(info))
        except Exception as exc:  # noqa: BLE001
            fail(msg_id, str(exc) or exc.__class__.__name__)
        return

    if cmd == "reveal":
        path = expand(msg.get("path") or "")
        try:
            if os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", path],
                                 **child_kwargs())
            elif os.path.isdir(path):
                subprocess.Popen(["explorer", path], **child_kwargs())
            else:
                raise RuntimeError("path does not exist")
            reply(msg_id, type="ok")
        except Exception as exc:  # noqa: BLE001
            fail(msg_id, str(exc))
        return

    if cmd == "download":
        threading.Thread(target=download_worker, args=(msg,),
                         daemon=True).start()
        return

    if cmd == "cancel":
        job_id = msg.get("job") or msg_id
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if job:
            job.cancel()
            reply(msg_id, type="cancelled", job=job.id)
        else:
            reply(msg_id, type="cancelled", job=job_id, note="no such job")
        return

    fail(msg_id, f"unknown command: {cmd}")


def main() -> None:
    log(f"{APP} host {VERSION} starting")
    while True:
        try:
            msg = read_message()
        except Exception as exc:  # pragma: no cover
            log("read failed:", exc)
            break
        if msg is None:
            break
        if not isinstance(msg, dict):
            continue
        try:
            handle(msg)
        except Exception as exc:  # noqa: BLE001
            log("handler crashed:", repr(exc))
            fail(msg.get("id"), str(exc))
    log("stdin closed, exiting")


# --------------------------------------------------------------------------- #
# small CLI for testing without Firefox
# --------------------------------------------------------------------------- #

def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="ytgrab-host", description="ytgrab native messaging host")
    parser.add_argument("--ping", action="store_true",
                        help="print tool/config status and exit")
    parser.add_argument("--list", metavar="URL",
                        help="list formats for URL as JSON")
    parser.add_argument("--config-path", action="store_true")
    args = parser.parse_args()
    if args.config_path:
        print(CONFIG_PATH)
        return 0
    if args.ping:
        print(json.dumps({"tools": tool_versions(), "config": load_config()},
                         indent=2, ensure_ascii=False))
        return 0
    if args.list:
        print(json.dumps(simplify_formats(run_ytdlp_json(args.list)),
                         indent=2, ensure_ascii=False))
        return 0
    parser.print_help()
    return 0


CLI_FLAGS = ("--ping", "--list", "--config-path", "-h", "--help")

if __name__ == "__main__":
    # Firefox launches the host with its own arguments (on Windows the parent
    # window handle), so "any argv present means CLI" is wrong: the process
    # would run the test CLI, argparse would reject the argument, and the host
    # would exit instantly -- which the extension sees as "host disconnected".
    # Only use the CLI when one of our own flags is actually present.
    if any(arg in CLI_FLAGS for arg in sys.argv[1:]):
        sys.exit(_cli())
    main()
