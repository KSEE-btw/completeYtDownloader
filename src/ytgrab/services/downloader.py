"""The download pipeline: yt-dlp + ffmpeg for one item."""

from __future__ import annotations

import collections
import os
import shutil
import subprocess

from ytgrab.domain.codecs import choose_action, compat_state
from ytgrab.domain.editor import cfr_args, clip_length, editor_flags, frame_rates, is_vfr, section_args
from ytgrab.domain.encode import TARGETS, audio_args_for, video_args_for
from ytgrab.domain.formats import SELECTOR_BEST, SELECTOR_PREMIERE, ensure_audio_selector
from ytgrab.domain.progress import _ffmpeg_progress_event, _is_ffmpeg_status, _last_error, _section_length, _ytdlp_progress_event
from ytgrab.errors import Cancelled
from ytgrab.infrastructure.config import DEFAULT_CONFIG, load_config
from ytgrab.infrastructure.files import _pick_output, expand, unique_path
from ytgrab.infrastructure.probe import probe_file
from ytgrab.infrastructure.process import _ffmpeg_run
from ytgrab.infrastructure.tools import child_kwargs, tools
from ytgrab.log import log
from ytgrab.services.catalog import download_speed_args, ytdlp_base_args
from ytgrab.services.job import Job
def run_download(job: Job, spec: dict, emit) -> dict:
    """Download (and convert) one item.

    emit(event) receives progress/stage dicts.  Returns a result dict on
    success, or raises Cancelled / RuntimeError.
    """
    cfg = load_config()
    url = (spec.get("url") or "").strip()
    if not url:
        raise RuntimeError("no URL given")

    exe = tools()["ytdlp"]
    if not exe:
        raise RuntimeError("yt-dlp was not found on PATH")

    selector = (spec.get("format") or "").strip()
    if not selector or selector == "auto":
        mode = spec.get("mode") or cfg.get("default_mode", "premiere_safe")
        selector = (SELECTOR_BEST if mode == "best" else SELECTOR_PREMIERE)
    # An explicit pick like "137" is often video-only (no sound of its own).
    # Appending "+ba" downloads the best audio alongside it and lets yt-dlp
    # mux the two in the same task; muxed/audio picks are unaffected.
    selector = ensure_audio_selector(selector, cfg.get("ensure_audio", True))

    editor = editor_flags(spec, cfg)

    outdir = expand(spec.get("outdir") or cfg.get("outdir")
                    or DEFAULT_CONFIG["outdir"])
    os.makedirs(outdir, exist_ok=True)

    # Keep the scratch file inside outdir so the final move is a rename on the
    # same volume instead of a second full copy.
    tmpdir = os.path.join(outdir, f".ytgrab-{job.id[:8]}")
    shutil.rmtree(tmpdir, ignore_errors=True)
    os.makedirs(tmpdir, exist_ok=True)

    try:
        clip = section_args(editor["clip_start"], editor["clip_end"])
        # ffmpeg cuts a clip, so its own status line is the only progress
        # source we get -- `section` is the length to measure it against.
        section = clip_length(editor)
        seen = 0.0
        samples = 0
        emit({"type": "stage", "stage": "downloading",
              "stage_label": ("Downloading the clip" if clip
                              else "Downloading from the web")})

        args = ([exe] + ytdlp_base_args(cfg, progress=True)
                + download_speed_args(cfg)
                + clip
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
        tail = collections.deque(maxlen=25)
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if line.startswith("@@P@@"):
                emit(_ytdlp_progress_event(line[5:].split()))
            elif clip and "time range" in line:
                # yt-dlp announces the span it actually resolved, which beats
                # what was typed: an end past the video comes back clamped, so
                # measuring against the request would never reach 100%.
                section = _section_length(line) or section
            elif clip and _is_ffmpeg_status(line):
                event = _ffmpeg_progress_event(line, section, seen)
                if event:
                    seen = event["seconds"]
                    samples += 1
                    if samples <= 2:
                        # The first lines arrive while ffmpeg is still filling
                        # its buffers, and its speed reads far too low then
                        # (minutes of ETA for a 30-second clip).  The position
                        # is real straight away; the ETA needs a moment.
                        event["eta"] = 0.0
                    emit(event)
            elif line.startswith("[Merger]") or "Merging formats" in line:
                emit({"type": "stage", "stage": "merging",
                      "stage_label": "Muxing video + audio"})
            elif line.strip():
                tail.append(line.strip())
        proc.wait()
        if job.cancelled:
            raise Cancelled()
        if proc.returncode != 0:
            raise RuntimeError(_last_error(tail)
                               or "yt-dlp exited with an error")

        source = _pick_output(tmpdir)
        if not source:
            raise RuntimeError("the download finished but no file was found")

        emit({"type": "stage", "stage": "checking",
              "stage_label": "Checking codecs"})

        info = probe_file(source)
        vfam = (info["video"] or {}).get("family")
        afam = (info["audio"] or {}).get("family")
        ext = os.path.splitext(source)[1].lstrip(".").lower()
        state = compat_state(vfam, afam, ext)

        want_convert = bool(spec.get("convert", True))
        target = (spec.get("target") or cfg.get("default_target") or "h264")
        target = target if target in TARGETS else "h264"

        codec_txt = "/".join(filter(None, [
            (info["video"] or {}).get("name"),
            (info["audio"] or {}).get("name")])) or "unknown"

        # --- the editor pack ---
        # A frame rate can only be pinned by re-encoding the video, and 48 kHz
        # by rewriting the audio, so each counts as "needed" only when the file
        # would not come out that way on its own.
        need_cfr = editor["force_cfr"] and is_vfr(info)
        src_rate = (info["audio"] or {}).get("sample_rate") or 0
        need_48k = (editor["audio_48k"] and info["audio"] is not None
                    and src_rate != 48000)
        extras = []
        if need_cfr:
            nominal, _ = frame_rates(info)
            extras.append(f"constant {nominal:.3f} fps")
        if need_48k:
            extras.append("48 kHz audio")

        action = choose_action(state, want_convert, need_cfr, need_48k)

        if action == "move":
            dest = unique_path(os.path.join(outdir, os.path.basename(source)))
            shutil.move(source, dest)
            if not want_convert:
                # Turning conversion off used to mean "already compatible", but
                # a video-only pick now merges in Opus audio, which is not.
                note = ("already Premiere-compatible" if state == "ok"
                        else "conversion off \u2014 saved as downloaded")
            else:
                note = "no conversion needed"
            return {"path": dest, "mode": "none", "codecs": codec_txt,
                    "note": note}

        plan = TARGETS[target]
        base = os.path.splitext(os.path.basename(source))[0]
        final = unique_path(os.path.join(outdir, f"{base}.{plan['ext']}"))
        duration = info["duration"] or 0.0

        # Decoding on the GPU is free speed and cannot change the output.
        pre = ["-hwaccel", "auto"] if cfg.get("hwaccel", True) else []
        vargs = video_args_for(target, cfg)
        aargs = audio_args_for(target, cfg, afam, force_48k=need_48k)

        if action == "remux":
            emit({"type": "stage", "stage": "remuxing",
                  "stage_label": "Repackaging into a Premiere container (fast)"})
            # A copy handles almost all of it; only a 48 kHz ask has to touch
            # the audio, and even then the video stays untouched.
            audio = (audio_args_for(target, cfg, afam, force_48k=True)
                     if need_48k else ["-c:a", "copy"])
            _ffmpeg_run(job, ["-y", "-i", source,
                              "-map", "0:v:0", "-map", "0:a:0?",
                              "-c:v", "copy"] + audio
                        + plan["extra"] + [final],
                        duration, "remuxing", emit)
        else:
            emit({"type": "stage", "stage": "converting",
                  "stage_label": f"Converting to {plan['label']}"})
            convert_args = (["-y", "-i", source,
                             "-map", "0:v:0", "-map", "0:a:0?"]
                            + vargs + (cfr_args(info) if need_cfr else [])
                            + aargs
                            + ["-map_metadata", "0", "-map_chapters", "0"]
                            + plan["extra"] + [final])
            try:
                _ffmpeg_run(job, pre + convert_args, duration, "converting",
                            emit)
            except Exception as exc:
                # Not every GPU can decode every codec; retry in software.
                if not pre or job.cancelled:
                    raise
                log("hwaccel failed, retrying on CPU:", exc)
                try:
                    os.remove(final)
                except OSError:
                    pass
                emit({"type": "stage", "stage": "converting",
                      "stage_label":
                          "Converting on CPU (GPU decode unavailable)"})
                _ffmpeg_run(job, convert_args, duration, "converting", emit)

        if job.cancelled:
            raise Cancelled()

        keep = spec.get("keep_original")
        if keep is None:
            keep = cfg.get("keep_original", False)
        if keep:
            shutil.move(source, unique_path(
                os.path.join(outdir, os.path.basename(source))))
        detail = f"converted from {codec_txt}"
        if aargs == ["-c:a", "copy"]:
            detail += " (audio copied)"
        if extras:
            detail += ", " + " and ".join(extras)
        return {"path": final, "mode": state, "codecs": codec_txt,
                "note": detail}
    except Cancelled:
        raise
    except Exception:
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
