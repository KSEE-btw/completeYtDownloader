"""Reading and writing ~/.config/ytgrab/config.json."""

from __future__ import annotations

import json
import os
import threading

from ytgrab.infrastructure.files import expand
from ytgrab.log import log
from ytgrab import APP, NAME
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
    # a picked format that has no audio of its own (video-only) also pulls the
    # best audio and muxes them into one file, so nothing lands silent
    "ensure_audio": True,
    # cookies to borrow for YouTube's "confirm you're not a bot" wall.
    # "auto" = Firefox when it has a profile (the default); "firefox"/"chrome"/
    # "edge"/"brave"/... = that browser; "none" = never.  See
    # resolve_cookies_from_browser().
    "cookies_from_browser": "auto",
    # refuse a playlist URL instead of grabbing the whole thing
    "force_no_playlist": True,
    # extra raw yt-dlp args
    "extra_ytdlp_args": [],

    # ---- queue window look ----
    # auto | light | dark.  "auto" follows the Windows app theme; the toggle in
    # the queue window writes "light"/"dark" here so the choice sticks.
    "ui_theme": "auto",

    # ---- speed (all quality-neutral by default) ----
    # parallel DASH fragment downloads; the single biggest download speedup
    "concurrent_fragments": 8,
    # split HTTP ranges for throttled hosts ("" = leave yt-dlp's default)
    "http_chunk_size": "",
    # auto | true | false  -- use aria2c when it is installed for multi-conn
    "use_aria2c": "auto",
    # how many queue items download at the same time (1 = strictly one at a
    # time, up to 8).  Fragments *within* one file are already parallel; this
    # is about whole videos, so two or three is usually plenty.
    "max_concurrent": 2,
    # x264 speed/size trade: preset does not change quality at a fixed CRF
    "x264_preset": "faster",
    "x264_crf": 18,
    # decode with the GPU when possible (no effect on output)
    "hwaccel": True,
    # libx264 | h264_nvenc | h264_qsv | h264_amf  (hardware is faster but
    # slightly less efficient per bitrate -- opt in explicitly)
    "video_encoder": "libx264",

    # ---- editor pack ----
    # grab only part of a video: "90", "1:30" or "0:01:30" (blank = the lot).
    # yt-dlp fetches just the fragments that cover the range, and the cut lands
    # on a keyframe, which is what a timeline wants.
    "clip_start": "",
    "clip_end": "",
    # pin the frame rate to a constant one so VFR sources (most phone and
    # screen-capture material) do not drift against the audio on a timeline
    "force_cfr": False,
    # re-encode audio to 48 kHz, the rate editing tools mix at
    "audio_48k": False,

    # binary overrides ("" = look up on PATH)
    "ytdlp": "",
    "ffmpeg": "",
    "ffprobe": "",
    "aria2c": "",
}


_config_lock = threading.Lock()
_config_cache = None
_config_mtime = None
def _config_file_mtime():
    """mtime of config.json, or None when it does not exist yet."""
    try:
        return os.path.getmtime(CONFIG_PATH)
    except OSError:
        return None
def load_config() -> dict:
    global _config_cache, _config_mtime
    with _config_lock:
        # The queue window, the native host and the CLI are separate processes
        # that all touch config.json.  Re-read whenever the file's mtime moved
        # so one process never serves a folder another process just changed.
        mtime = _config_file_mtime()
        if _config_cache is not None and mtime == _config_mtime:
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
        _config_mtime = mtime
        return cfg
def save_config(patch: dict) -> dict:
    global _config_cache, _config_mtime
    cfg = dict(load_config())
    for key, value in (patch or {}).items():
        if key in DEFAULT_CONFIG:
            cfg[key] = value
    with _config_lock:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
        _config_cache = cfg
        _config_mtime = _config_file_mtime()
    return cfg
def reset_config_cache() -> None:
    global _config_cache, _config_mtime
    with _config_lock:
        _config_cache = None
        _config_mtime = None
def choose_folder(start: str = "", parent=None) -> str:
    """Open the OS folder picker; return the chosen path, or "" if cancelled.

    The queue window passes itself as `parent` -- Tk only allows one root per
    process -- while the native host, which has no window, gets a transient
    hidden one.  tkinter is imported here rather than at module scope so the
    CLI and the download worker never pull in a GUI.
    """
    import tkinter as tk
    from tkinter import filedialog

    own = parent is None
    root = parent
    if own:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass

    options = {"parent": root,
               "title": f"Choose where {NAME} saves downloads",
               "mustexist": True}
    if start:
        options["initialdir"] = expand(start)
    try:
        chosen = filedialog.askdirectory(**options)
    except tk.TclError:
        chosen = ""
    finally:
        if own:
            try:
                root.destroy()
            except tk.TclError:
                pass
    return os.path.normpath(chosen) if chosen else ""
