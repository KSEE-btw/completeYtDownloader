#!/usr/bin/env python3
"""ytgrab queue window.

A small Tkinter app that owns the download queue.  The Firefox extension
talks to the native-messaging host, the host forwards each new download here
over loopback IPC, and this process does the actual yt-dlp/ffmpeg work and
shows the progress -- so nothing is lost when the browser popup closes.

Modes
-----
    ytgrab-host.exe --ui           open (or focus) the queue window
    ytgrab-host.exe --ui --show    focus an existing window, else open one
    ytgrab-host.exe --headless     run the queue with no window (testing/CI)
    ytgrab-host.exe --self-test    end-to-end check of queue + IPC (testing)
    ytgrab-host.exe --smoke        build the window and close it (testing)

Closing the window keeps the process alive; downloads continue.  Use Quit to
stop, and unfinished items resume next time the queue starts.
"""

from __future__ import annotations

import json
import os
import queue as _queue
import subprocess
import sys
import threading
import time
import uuid

import ytgrab_core as core
import ytgrab_ipc as ipc

QUEUE_PATH = os.environ.get("YTGRAB_QUEUE_PATH") or os.path.join(
    core.CONFIG_DIR, "queue.json")
HISTORY_LIMIT = 200

try:
    import tkinter as tk
    from tkinter import ttk
    from tkinter import messagebox
    TK_OK = True
except Exception:            # pragma: no cover - no display / no Tk
    tk = ttk = messagebox = None
    TK_OK = False

# dark palette that roughly matches the extension
BG = "#16181d"
CARD = "#1e2127"
CARD_HI = "#242832"
FG = "#e8eaed"
MUTED = "#9aa0a6"
ACCENT = "#4c8dff"
OK = "#3fb950"
WARN = "#d29922"
BAD = "#f85149"


# --------------------------------------------------------------------------- #
# formatting helpers (shared by the window and the status text)
# --------------------------------------------------------------------------- #

def fmt_bytes(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    return f"{n:.1f} {units[i]}" if i and n < 10 else f"{n:.0f} {units[i]}"


def fmt_speed(n) -> str:
    b = fmt_bytes(n)
    return f"{b}/s" if b else ""


def fmt_eta(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def detail_text(item) -> str:
    status = item["status"]
    if status == "queued":
        return "Waiting in the queue"
    if status == "running":
        if item["stage"] == "downloading":
            bits = [f"Downloading {item['percent']:.0f}%"]
            if item["total"]:
                bits.append(f"of {fmt_bytes(item['total'])}")
            if item["speed"]:
                bits.append(fmt_speed(item["speed"]))
            if item["eta"]:
                bits.append(f"ETA {fmt_eta(item['eta'])}")
            return "   ".join(bits)
        return item["stage_label"] or "Working..."
    if status == "done":
        name = os.path.basename(item["path"]) if item["path"] else ""
        note = item["note"] or "saved"
        head = "Already Premiere-compatible" if item["mode"] == "none" else "Done"
        out = f"{head} \u00b7 {note}"
        if name:
            out += f" \u00b7 {name}"
        return out
    if status == "error":
        return "Failed: " + (item["error"] or "unknown error")
    if status == "cancelled":
        return "Cancelled"
    return status


# --------------------------------------------------------------------------- #
# queue model
# --------------------------------------------------------------------------- #

class Item:
    def __init__(self, item_id: str, spec: dict):
        self.id = item_id
        self.spec = dict(spec or {})
        self.url = self.spec.get("url", "") or ""
        self.title = self.spec.get("title", "") or ""
        self.status = "queued"          # queued running done error cancelled
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


class QueueManager:
    """Owns the items and a single worker thread that drains the queue."""

    def __init__(self, path: str = QUEUE_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self.items: dict = {}          # id -> Item, in queue order
        self._jobs: dict = {}
        self._stop = False
        self.load()
        self._worker = threading.Thread(target=self._loop, name="ytgrab-queue",
                                        daemon=True)
        self._worker.start()

    # --- persistence ---

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, ValueError, OSError):
            return
        for raw in (data.get("items") if isinstance(data, dict) else None) or []:
            try:
                item = Item.from_dict(raw)
            except Exception:
                continue
            self.items[item.id] = item

    def save(self) -> None:
        with self._lock:
            self._prune()
            payload = {"version": 1,
                       "items": [it.to_dict() for it in self.items.values()]}
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _prune(self) -> None:
        if len(self.items) <= HISTORY_LIMIT:
            return
        finished = ("done", "error", "cancelled")
        for item_id in list(self.items):
            if len(self.items) <= HISTORY_LIMIT:
                break
            if self.items[item_id].status in finished:
                del self.items[item_id]

    # --- mutations ---

    def add(self, spec: dict) -> str:
        with self._lock:
            item = Item(uuid.uuid4().hex, spec)
            self.items[item.id] = item
            self._cond.notify_all()
        self.save()
        return item.id

    def cancel(self, item_id: str) -> bool:
        with self._lock:
            item = self.items.get(item_id)
            if item is None or item.status not in ("queued", "running"):
                return False
            item.status = "cancelled"
            item.finished = time.time()
            job = self._jobs.get(item_id)
        self.save()
        if job is not None:
            job.cancel()
        return True

    def retry(self, item_id: str) -> bool:
        with self._lock:
            item = self.items.get(item_id)
            if item is None or item.status not in ("error", "cancelled"):
                return False
            item.status = "queued"
            item.stage = item.stage_label = item.error = ""
            item.percent = item.speed = item.eta = 0.0
            item.downloaded = item.total = 0.0
            item.finished = None
            self._cond.notify_all()
        self.save()
        return True

    def remove(self, item_id: str) -> bool:
        with self._lock:
            item = self.items.get(item_id)
            if item is None:
                return False
            job = self._jobs.get(item_id)
            if item.status == "running" and job is not None:
                item.status = "cancelled"
            del self.items[item_id]
        if job is not None:
            job.cancel()
        self.save()
        return True

    def clear_finished(self) -> int:
        with self._lock:
            done = [i for i, it in self.items.items()
                    if it.status in ("done", "error", "cancelled")]
            for item_id in done:
                del self.items[item_id]
        if done:
            self.save()
        return len(done)

    def request_stop(self) -> None:
        with self._lock:
            self._stop = True
            self._cond.notify_all()

    def stopped(self) -> bool:
        with self._lock:
            return self._stop

    def shutdown(self) -> None:
        with self._lock:
            self._stop = True
            self._cond.notify_all()
            for item in self.items.values():
                if item.status == "running":
                    item.status = "queued"     # resume next launch
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel()
        self.save()
        self._worker.join(timeout=3.0)

    # --- reads ---

    def snapshot(self) -> list:
        with self._lock:
            return [it.to_dict() for it in self.items.values()]

    def counts(self) -> dict:
        with self._lock:
            c = {"queued": 0, "running": 0, "done": 0, "error": 0,
                 "cancelled": 0}
            for item in self.items.values():
                c[item.status] = c.get(item.status, 0) + 1
            c["total"] = len(self.items)
            return c

    # --- worker ---

    def _next_queued(self):
        for item in self.items.values():
            if item.status == "queued":
                return item
        return None

    def _loop(self) -> None:
        while True:
            with self._lock:
                if self._stop:
                    return
                item = self._next_queued()
                if item is None:
                    self._cond.wait(timeout=0.5)
                    continue
                item.status = "running"
                item.stage = "downloading"
                item.stage_label = "Starting..."
                item.percent = 0.0
                job = core.Job(item.id)
                self._jobs[item.id] = job
            self.save()
            self._run(item, job)

    def _run(self, item: "Item", job: core.Job) -> None:
        def emit(event: dict) -> None:
            with self._lock:
                if event.get("type") == "stage":
                    item.stage = event.get("stage", "")
                    item.stage_label = event.get("stage_label", "")
                    item.percent = 0.0
                    item.speed = 0.0
                    item.eta = 0.0
                elif event.get("type") == "progress":
                    item.stage = event.get("phase", item.stage)
                    if not item.stage_label or item.stage_label == "Starting...":
                        item.stage_label = "Downloading from the web"
                    item.percent = float(event.get("percent") or 0.0)
                    item.speed = float(event.get("speed") or 0.0)
                    item.eta = float(event.get("eta") or 0.0)
                    item.downloaded = float(event.get("downloaded") or 0.0)
                    if event.get("total"):
                        item.total = float(event.get("total") or 0.0)

        try:
            result = core.run_download(job, item.spec, emit)
            with self._lock:
                item.status = "done"
                item.percent = 100.0
                item.path = result.get("path", "")
                item.mode = result.get("mode", "")
                item.codecs = result.get("codecs", "")
                item.note = result.get("note", "")
                item.speed = item.eta = 0.0
        except core.Cancelled:
            # Either the user cancelled (status is already "cancelled") or
            # shutdown() re-queued it to resume next launch -- keep both.
            with self._lock:
                if item.status == "running":
                    item.status = "cancelled"
        except Exception as exc:  # noqa: BLE001 - surfaced in the window
            with self._lock:
                if item.status == "running":
                    item.status = "error"
                    item.error = str(exc) or exc.__class__.__name__
        finally:
            with self._lock:
                self._jobs.pop(item.id, None)
                if (item.status in ("done", "error", "cancelled")
                        and item.finished is None):
                    item.finished = time.time()
                item.stage = ""
                item.stage_label = ""
            self.save()
        if core.DEBUG:
            core.log("finished", item.id, item.status)


# --------------------------------------------------------------------------- #
# IPC command surface
# --------------------------------------------------------------------------- #

ENQUEUE_FIELDS = ("url", "format", "mode", "convert", "target", "outdir",
                  "keep_original", "title")


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
        if cmd == "clear_finished":
            return {"ok": True, "cleared": qm.clear_finished()}
        if cmd == "list":
            return {"ok": True, "items": qm.snapshot()}
        if cmd == "status":
            counts = qm.counts()
            return {"ok": True, "counts": counts,
                    "queued": counts.get("queued", 0),
                    "running": counts.get("running", 0)}
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


# --------------------------------------------------------------------------- #
# the window
# --------------------------------------------------------------------------- #

if TK_OK:

    class QueueWindow(tk.Tk):
        def __init__(self, qm: QueueManager, show: bool = True):
            super().__init__()
            self.qm = qm
            self.rows: dict = {}
            self._commands = _queue.Queue()
            self._closing = False

            self.title("ytgrab \u2014 download queue")
            self.geometry("780x540")
            self.minsize(600, 360)
            self.configure(bg=BG)
            self._init_style()

            self._build()
            self.protocol("WM_DELETE_WINDOW", self._on_close)
            self.bind("<Control-q>", lambda _e: self._quit())
            if not show:
                self.iconify()
            self.after(250, self._tick)

        # --- styling ---

        def _init_style(self) -> None:
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            style.configure("Yt.TButton", background=CARD_HI, foreground=FG,
                            borderwidth=0, focusthickness=0, padding=(10, 4))
            style.map("Yt.TButton",
                      background=[("active", "#333a48"), ("pressed", ACCENT),
                                  ("disabled", CARD)],
                      foreground=[("disabled", MUTED)])
            style.configure("Yt.Horizontal.TProgressbar", troughcolor="#2a2f3a",
                            background=ACCENT, bordercolor="#2a2f3a",
                            lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)

        # --- layout ---

        def _build(self) -> None:
            header = tk.Frame(self, bg=CARD, padx=14, pady=10)
            header.pack(fill="x")
            tk.Label(header, text="ytgrab queue", bg=CARD, fg=ACCENT,
                     font=("Segoe UI", 12, "bold")).pack(side="left")
            self.summary = tk.Label(header, text="", bg=CARD, fg=MUTED,
                                    font=("Segoe UI", 9))
            self.summary.pack(side="left", padx=(12, 0))

            for label, cmd in (("Quit", self._quit), ("Hide", self._on_close),
                               ("Clear finished", self._clear_finished),
                               ("Open folder", self._open_folder)):
                ttk.Button(header, text=label, style="Yt.TButton",
                           command=cmd).pack(side="right", padx=(6, 0))

            self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
            scroll = ttk.Scrollbar(self, orient="vertical",
                                   command=self.canvas.yview)
            self.canvas.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            self.canvas.pack(side="left", fill="both", expand=True)

            self.list = tk.Frame(self.canvas, bg=BG)
            self.canvas.create_window((0, 0), window=self.list, anchor="nw",
                                      tags="inner")
            self.list.bind(
                "<Configure>",
                lambda _e: self.canvas.configure(
                    scrollregion=self.canvas.bbox("all")))
            self.canvas.bind(
                "<Configure>",
                lambda e: self.canvas.itemconfigure("inner", width=e.width))
            self.canvas.bind_all("<MouseWheel>", self._on_wheel)

            self.empty = tk.Label(
                self.list, bg=BG, fg=MUTED, justify="center",
                font=("Segoe UI", 10),
                text=("No downloads yet.\n\n"
                      "Click the blue ytgrab arrow on a page to add one."))

            self.footer = tk.Label(
                self, bg=BG, fg=MUTED, anchor="w", padx=14, pady=6,
                font=("Segoe UI", 8),
                text="Closing this window keeps downloads running in the "
                     "background. Use Quit to stop ytgrab.")
            self.footer.pack(fill="x", side="bottom")

        def _on_wheel(self, event) -> None:
            try:
                self.canvas.yview_scroll(int(-event.delta / 120), "units")
            except Exception:
                pass

        # --- thread-safe entry point for the IPC server ---

        def post_command(self, cmd: str) -> None:
            self._commands.put(cmd)

        # --- row widgets ---

        def _create_row(self, item: dict) -> dict:
            frame = tk.Frame(self.list, bg=CARD, padx=12, pady=9)
            top = tk.Frame(frame, bg=CARD)
            top.pack(fill="x")
            title = tk.Label(top, bg=CARD, fg=FG, anchor="w",
                             font=("Segoe UI", 10, "bold"))
            title.pack(side="left", fill="x", expand=True)
            btns = tk.Frame(top, bg=CARD)
            btns.pack(side="right")
            detail = tk.Label(frame, bg=CARD, fg=MUTED, anchor="w",
                              font=("Segoe UI", 9))
            detail.pack(fill="x", pady=(3, 6))
            bar = ttk.Progressbar(frame, style="Yt.Horizontal.TProgressbar",
                                  maximum=100.0)
            bar.pack(fill="x")
            return {"frame": frame, "title": title, "detail": detail,
                    "bar": bar, "btns": btns, "buttons": {}, "bar_mode": None}

        def _set_buttons(self, row: dict, item: dict) -> None:
            status = item["status"]
            if status == "queued":
                wanted = [("Cancel", "cancel"), ("Remove", "remove")]
            elif status == "running":
                wanted = [("Cancel", "cancel")]
            elif status == "done":
                wanted = [("Show file", "reveal"), ("Remove", "remove")]
            else:
                wanted = [("Retry", "retry"), ("Remove", "remove")]
            names = [name for name, _ in wanted]
            if list(row["buttons"]) == names:
                return
            for widget in row["buttons"].values():
                widget.destroy()
            row["buttons"] = {}
            for name, action in wanted:
                btn = ttk.Button(row["btns"], text=name, style="Yt.TButton",
                                 command=lambda i=item["id"], a=action:
                                 self._action(a, i))
                btn.pack(side="right", padx=(6, 0))
                row["buttons"][name] = btn

        def _action(self, action: str, item_id: str) -> None:
            if action == "cancel":
                self.qm.cancel(item_id)
            elif action == "retry":
                self.qm.retry(item_id)
            elif action == "remove":
                self.qm.remove(item_id)
            elif action == "reveal":
                self._reveal_item(item_id)
            self._tick(force=True)

        def _update_row(self, row: dict, item: dict) -> None:
            text = item["title"] or item["url"]
            if len(text) > 110:
                text = text[:107] + "..."
            row["title"].configure(text=text)
            row["detail"].configure(text=detail_text(item))
            running = item["status"] == "running"
            determinate = running and item["stage"] in (
                "downloading", "remuxing", "converting")
            if determinate:
                if row["bar_mode"] != "determinate":
                    row["bar"].stop()
                    row["bar"].configure(mode="determinate")
                    row["bar_mode"] = "determinate"
                row["bar"]["value"] = max(0.0, min(100.0, item["percent"]))
            elif running:
                if row["bar_mode"] != "indeterminate":
                    row["bar"].configure(mode="indeterminate")
                    row["bar"].start(60)
                    row["bar_mode"] = "indeterminate"
            else:
                if row["bar_mode"] != "done":
                    row["bar"].stop()
                    row["bar"].configure(mode="determinate")
                    row["bar_mode"] = "done"
                if item["status"] == "done":
                    row["bar"]["value"] = 100.0
                elif item["status"] == "error":
                    row["bar"]["value"] = 100.0
                else:
                    row["bar"]["value"] = 0.0
            self._set_buttons(row, item)

        # --- periodic refresh ---

        def _tick(self, force: bool = False) -> None:
            if self._closing:
                return
            self._drain_commands()
            snapshot = self.qm.snapshot()
            seen = set()
            for item in snapshot:
                seen.add(item["id"])
                row = self.rows.get(item["id"])
                if row is None:
                    row = self._create_row(item)
                    self.rows[item["id"]] = row
                    row["frame"].pack(fill="x", pady=(0, 8), padx=12)
                self._update_row(row, item)
            for item_id in list(self.rows):
                if item_id not in seen:
                    self.rows.pop(item_id)["frame"].destroy()

            if snapshot:
                self.empty.pack_forget()
            else:
                self.empty.pack(pady=60)

            counts = self.qm.counts()
            parts = []
            if counts["running"]:
                parts.append(f"{counts['running']} downloading")
            if counts["queued"]:
                parts.append(f"{counts['queued']} waiting")
            done = counts["done"] + counts["error"] + counts["cancelled"]
            if done:
                parts.append(f"{done} finished")
            self.summary.configure(text="   ".join(parts) or "idle")
            self.update_idletasks()
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            if not force:
                self.after(400, self._tick)

        def _drain_commands(self) -> None:
            while True:
                try:
                    cmd = self._commands.get_nowait()
                except _queue.Empty:
                    return
                if cmd == "show":
                    self.deiconify()
                    self.lift()
                elif cmd == "quit":
                    self._quit()
                elif cmd == "shutdown":
                    self._force_quit()

        # --- window actions ---

        def _on_close(self) -> None:
            self.iconify()
            self.footer.configure(
                text="Still running in the background. Reopen from the "
                     "ytgrab button, or use Quit to stop ytgrab.")

        def _quit(self) -> None:
            if self.qm.counts().get("running"):
                if not messagebox.askyesno(
                        "ytgrab",
                        "A download is still running. Quit anyway?\n\n"
                        "Unfinished items will resume next time you open "
                        "the queue."):
                    return
            self._closing = True
            try:
                self.destroy()
            except tk.TclError:
                pass

        def _force_quit(self) -> None:
            # like _quit but never asks: the updater is waiting on us
            self._closing = True
            try:
                self.destroy()
            except tk.TclError:
                pass

        def _clear_finished(self) -> None:
            self.qm.clear_finished()
            self._tick(force=True)

        def _open_folder(self) -> None:
            cfg = core.load_config()
            target = core.expand(cfg.get("outdir")
                                 or core.DEFAULT_CONFIG["outdir"])
            self._reveal(target)

        def _reveal_item(self, item_id: str) -> None:
            for item in self.qm.snapshot():
                if item["id"] == item_id:
                    self._reveal(item["path"] or item["url"])
                    return

        def _reveal(self, path: str) -> None:
            try:
                if path and os.path.isfile(path):
                    subprocess.Popen(["explorer", "/select,", path],
                                     **core.child_kwargs())
                elif path and os.path.isdir(path):
                    subprocess.Popen(["explorer", path],
                                     **core.child_kwargs())
                else:
                    raise OSError("path does not exist")
            except Exception as exc:  # noqa: BLE001
                messagebox.showwarning("ytgrab", f"Could not open:\n{exc}")


# --------------------------------------------------------------------------- #
# modes
# --------------------------------------------------------------------------- #

def _run_headless(qm: QueueManager) -> int:
    def on_command(cmd: str) -> None:
        if cmd in ("quit", "shutdown"):
            qm.request_stop()
    server = ipc.Server(make_handler(qm, on_command), version=core.VERSION)
    server.start()
    core.log("headless queue on port", server.port)
    try:
        while not qm.stopped():
            time.sleep(0.4)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        qm.shutdown()
    return 0


def _self_test(url: str, fmt: str = "", mode: str = "premiere_safe") -> int:
    """End-to-end check: queue + worker + IPC, with no window."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="ytgrab-selftest-")
    qm = QueueManager(os.path.join(tmp, "queue.json"))
    server = ipc.Server(make_handler(qm, lambda _c: None),
                        version=core.VERSION)
    server.start()
    print(f"server on 127.0.0.1:{server.port}, queue in {tmp}")

    job = ipc.request({"cmd": "enqueue", "url": url, "format": fmt,
                       "mode": mode, "convert": True,
                       "target": "h264", "outdir": tmp, "keep_original": False,
                       "title": "self-test"})
    print("enqueue ->", job)
    print("status  ->", ipc.request({"cmd": "status"}))

    deadline = time.monotonic() + 600
    last = None
    while time.monotonic() < deadline:
        items = ipc.request({"cmd": "list"}).get("items", [])
        if items:
            item = items[0]
            line = f"  {item['status']:<10} {item['percent']:5.1f}%  {detail_text(item)}"
            if line != last:
                print(line, flush=True)
                last = line
            if item["status"] in ("done", "error", "cancelled"):
                break
        time.sleep(0.5)

    final = ipc.request({"cmd": "list"}).get("items", [])
    print("\nfinal:", json.dumps(final, indent=2)[:2000])
    server.stop()
    qm.shutdown()
    ok = bool(final) and final[0]["status"] == "done"
    print("\nSELF-TEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _smoke() -> int:
    if not TK_OK:
        print("tkinter unavailable; cannot smoke-test the window")
        return 1
    import tempfile
    tmp = tempfile.mkdtemp(prefix="ytgrab-smoke-")
    qm = QueueManager(os.path.join(tmp, "queue.json"))
    root = QueueWindow(qm, show=False)
    root.withdraw()
    for _ in range(6):
        root.update()
        time.sleep(0.06)
    root.destroy()
    qm.shutdown()
    print("SMOKE PASS")
    return 0


def main(args=None) -> int:
    args = list(args if args is not None else sys.argv[1:])
    headless = "--headless" in args
    show = "--show" in args

    # testing hooks, no window
    if "--self-test-url" in args:
        def value(flag, default=""):
            if flag in args:
                i = args.index(flag)
                if i + 1 < len(args):
                    return args[i + 1]
            return default
        return _self_test(value("--self-test-url"),
                          fmt=value("--self-test-format"),
                          mode=value("--self-test-mode", "premiere_safe"))
    if "--smoke" in args:
        return _smoke()

    # single instance: if one is already running, just show it and leave
    if ipc.is_running():
        if show:
            try:
                ipc.request({"cmd": "show"})
            except ipc.IpcError:
                pass
        return 0

    qm = QueueManager(QUEUE_PATH)

    if headless or not TK_OK:
        if not TK_OK and not headless:
            core.log("tkinter unavailable; running headless")
        return _run_headless(qm)

    root = QueueWindow(qm, show=show)
    server = ipc.Server(make_handler(qm, root.post_command),
                        version=core.VERSION)
    server.start()
    try:
        root.mainloop()
    finally:
        server.stop()
        qm.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
