"""The queue window's command surface (IPC -> queue)."""

from __future__ import annotations

from ytgrab.protocol import ENQUEUE_FIELDS
from ytgrab.services.queue import QueueManager
def make_handler(qm: QueueManager, on_ui_command):
    def handler(req: dict) -> dict:
        cmd = req.get("cmd")
        if cmd == "enqueue":
            spec = {key: req.get(key) for key in ENQUEUE_FIELDS}
            if not (spec.get("url") or "").strip():
                return {"ok": False, "error": "no URL given"}
            job = qm.add(spec)
            return {"ok": True, "job": job}
        if cmd == "cancel":
            return {"ok": qm.cancel(req.get("job") or "")}
        if cmd == "retry":
            return {"ok": qm.retry(req.get("job") or "")}
        if cmd == "remove":
            return {"ok": qm.remove(req.get("job") or "")}
        if cmd == "pause":
            return {"ok": qm.pause(req.get("job") or "")}
        if cmd == "resume":
            return {"ok": qm.resume(req.get("job") or "")}
        if cmd == "pause_all":
            return {"ok": True, "paused": qm.pause_all()}
        if cmd == "resume_all":
            return {"ok": True, "resumed": qm.resume_all()}
        if cmd == "move":
            try:
                delta = int(req.get("delta") or 0)
            except (TypeError, ValueError):
                delta = 0
            return {"ok": qm.move(req.get("job") or "", delta)}
        if cmd == "cancel_many":
            return {"ok": True,
                    "cancelled": qm.cancel_many(req.get("jobs") or [])}
        if cmd == "remove_many":
            return {"ok": True,
                    "removed": qm.remove_many(req.get("jobs") or [])}
        if cmd == "set_max_concurrent":
            return {"ok": True,
                    "max_concurrent": qm.set_max_concurrent(req.get("value"))}
        if cmd == "clear_finished":
            return {"ok": True, "cleared": qm.clear_finished()}
        if cmd == "list":
            return {"ok": True, "items": qm.snapshot()}
        if cmd == "status":
            counts = qm.counts()
            return {"ok": True, "counts": counts,
                    "queued": counts.get("queued", 0),
                    "running": counts.get("running", 0),
                    "paused": counts.get("paused", 0)}
        if cmd == "show":
            on_ui_command("show")
            return {"ok": True}
        if cmd == "quit":
            on_ui_command("quit")
            return {"ok": True}
        if cmd == "shutdown":
            # stop without prompting; the updater uses this before swapping exes
            on_ui_command("shutdown")
            return {"ok": True}
        return {"ok": False, "error": f"unknown command: {cmd}"}
    return handler
