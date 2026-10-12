"""The wire contract between the extension, the host and the queue."""

from __future__ import annotations
ENQUEUE_FIELDS = ("url", "format", "mode", "convert", "target", "outdir",
                  "keep_original", "title",
                  "clip_start", "clip_end", "force_cfr", "audio_48k")
