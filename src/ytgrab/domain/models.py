"""Plain data: the queue item and the media constants."""

from __future__ import annotations

import time
import uuid
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".opus",
              ".flac"}


class Item:
    def __init__(self, item_id: str, spec: dict):
        self.id = item_id
        self.spec = dict(spec or {})
        self.url = self.spec.get("url", "") or ""
        self.title = self.spec.get("title", "") or ""
        self.status = "queued"      # queued running paused done error cancelled
        self.stage = ""
        self.stage_label = ""
        self.percent = 0.0
        self.speed = 0.0
        self.eta = 0.0
        self.downloaded = 0.0
        self.total = 0.0
        self.path = ""
        self.mode = ""
        self.codecs = ""
        self.note = ""
        self.error = ""
        self.added = time.time()
        self.finished = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "url": self.url, "title": self.title,
            "status": self.status, "stage": self.stage,
            "stage_label": self.stage_label, "percent": self.percent,
            "speed": self.speed, "eta": self.eta,
            "downloaded": self.downloaded, "total": self.total,
            "path": self.path, "mode": self.mode, "codecs": self.codecs,
            "note": self.note, "error": self.error,
            "added": self.added, "finished": self.finished,
            "spec": self.spec,      # kept so unfinished items can resume
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Item":
        item = cls(data.get("id") or uuid.uuid4().hex, {})
        for key, value in data.items():
            if key in ("id", "spec"):
                continue
            if hasattr(item, key):
                setattr(item, key, value)
        item.spec = dict(data.get("spec") or {})
        if not item.url:
            item.url = item.spec.get("url", "") or ""
        if not item.title:
            item.title = item.spec.get("title", "") or ""
        if item.status in ("running", "downloading", "converting"):
            item.status = "queued"      # resume unfinished work on restart
        item.finished = None if item.status == "queued" else item.finished
        return item
