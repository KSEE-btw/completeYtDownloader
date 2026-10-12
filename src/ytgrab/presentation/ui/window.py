"""The Tkinter queue window."""

from __future__ import annotations

import queue as _queue
import os
import subprocess

from ytgrab import NAME
from ytgrab.infrastructure.config import DEFAULT_CONFIG, choose_folder, load_config, reset_config_cache, save_config
from ytgrab.infrastructure.files import expand
from ytgrab.infrastructure.tools import child_kwargs
from ytgrab.log import log
from ytgrab.presentation.ui.formatting import clip_note, detail_text
from ytgrab.presentation.ui.theme import THEMES, config_theme, pick_ui_font
from ytgrab.presentation.ui.toolkit import TK_OK, messagebox, tk, ttk
from ytgrab.services.queue import MAX_PARALLEL, QueueManager
if TK_OK:

    class QueueWindow(tk.Tk):
        def __init__(self, qm: QueueManager, show: bool = True):
            super().__init__()
            self.qm = qm
            self.rows: dict = {}
            self.selected: set = set()
            self._picked = 0               # how many rows are ticked
            self._commands = _queue.Queue()
            self._closing = False
            self._tick_after = None

            self.title(f"{NAME} \u2014 download queue")
            self.geometry("820x600")
            self.minsize(720, 400)
            # light/dark, from config if the user chose one, else the OS
            self.mode = config_theme()
            self.p = THEMES[self.mode]
            self.configure(bg=self.p["bg"])
            self.font = pick_ui_font(self)
            self._init_style()

            self._build()
            self.bind_all("<MouseWheel>", self._on_wheel)
            self.protocol("WM_DELETE_WINDOW", self._on_close)
            self.bind("<Control-q>", lambda _e: self._quit())
            if not show:
                self.iconify()
            self._tick_after = self.after(250, self._tick)

        # --- styling ---

        def _init_style(self) -> None:
            p = self.p
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            # flat Breeze buttons: surface fill, hairline border, blue on press
            style.configure("Yt.TButton", background=p["card"], foreground=p["fg"],
                            borderwidth=1, relief="solid", focusthickness=0,
                            padding=(12, 5), bordercolor=p["border"],
                            lightcolor=p["card"], darkcolor=p["card"])
            style.map("Yt.TButton",
                      background=[("pressed", p["accent"]),
                                  ("active", p["card_hi"]),
                                  ("disabled", p["bg"])],
                      foreground=[("pressed", p["press_fg"]),
                                  ("disabled", p["muted"])],
                      bordercolor=[("active", p["accent"]),
                                   ("pressed", p["accent_dk"]),
                                   ("disabled", p["border"])],
                      lightcolor=[("active", p["card_hi"]),
                                  ("pressed", p["accent"])],
                      darkcolor=[("active", p["card_hi"]),
                                 ("pressed", p["accent_dk"])])
            style.configure("Yt.Horizontal.TProgressbar", troughcolor=p["trough"],
                            background=p["accent"], bordercolor=p["trough"],
                            lightcolor=p["accent"], darkcolor=p["accent"],
                            thickness=8)
            style.configure("Yt.Ok.Horizontal.TProgressbar", troughcolor=p["trough"],
                            background=p["ok"], bordercolor=p["trough"],
                            lightcolor=p["ok"], darkcolor=p["ok"], thickness=8)
            style.configure("Yt.Bad.Horizontal.TProgressbar",
                            troughcolor=p["trough"], background=p["bad"],
                            bordercolor=p["trough"], lightcolor=p["bad"],
                            darkcolor=p["bad"], thickness=8)
            style.configure("Yt.Vertical.TScrollbar", background=p["border"],
                            troughcolor=p["bg"], bordercolor=p["bg"],
                            arrowcolor=p["muted"], lightcolor=p["bg"],
                            darkcolor=p["bg"], gripcount=0)
            style.map("Yt.Vertical.TScrollbar",
                      background=[("active", p["muted"]),
                                  ("pressed", p["accent"])])
            # compact buttons for the per-row queue moves
            style.configure("Yt.Mini.TButton", background=p["card"],
                            foreground=p["fg"], borderwidth=1, relief="solid",
                            focusthickness=0, padding=(7, 3),
                            bordercolor=p["border"], lightcolor=p["card"],
                            darkcolor=p["card"])
            style.map("Yt.Mini.TButton",
                      background=[("pressed", p["accent"]),
                                  ("active", p["card_hi"]),
                                  ("disabled", p["bg"])],
                      foreground=[("pressed", p["press_fg"]),
                                  ("disabled", p["muted"])],
                      bordercolor=[("active", p["accent"]),
                                   ("pressed", p["accent_dk"]),
                                   ("disabled", p["border"])],
                      lightcolor=[("active", p["card_hi"]),
                                  ("pressed", p["accent"])],
                      darkcolor=[("active", p["card_hi"]),
                                 ("pressed", p["accent_dk"])])
            # the "at once" picker
            self.option_add("*TCombobox*Listbox.background", p["card"])
            self.option_add("*TCombobox*Listbox.foreground", p["fg"])
            self.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
            self.option_add("*TCombobox*Listbox.selectForeground", p["press_fg"])
            style.configure("Yt.TCombobox", fieldbackground=p["card"],
                            background=p["card"], foreground=p["fg"],
                            bordercolor=p["border"], arrowcolor=p["fg"],
                            lightcolor=p["card"], darkcolor=p["card"],
                            selectbackground=p["card"],
                            selectforeground=p["fg"], padding=(4, 3))
            style.map("Yt.TCombobox",
                      fieldbackground=[("readonly", p["card"]),
                                       ("disabled", p["bg"])],
                      foreground=[("readonly", p["fg"]),
                                  ("disabled", p["muted"])],
                      bordercolor=[("focus", p["accent"])],
                      arrowcolor=[("disabled", p["muted"])])

        # --- layout ---

        def _build(self) -> None:
            p = self.p
            # --- top toolbar (Breeze-style) ---
            header = tk.Frame(self, bg=p["header"], padx=14, pady=9)
            header.pack(fill="x")
            tk.Label(header, text="\u25b6", bg=p["header"], fg=p["accent"],
                     font=(self.font, 12, "bold")).pack(side="left",
                                                        padx=(0, 8))
            tk.Label(header, text=f"{NAME} queue", bg=p["header"], fg=p["fg"],
                     font=(self.font, 12, "bold")).pack(side="left")
            self.summary = tk.Label(header, text="", bg=p["header"],
                                    fg=p["muted"], font=(self.font, 9))
            self.summary.pack(side="left", padx=(12, 0))

            for label, cmd in (("Quit", self._quit), ("Hide", self._on_close),
                               ("Clear finished", self._clear_finished),
                               ("Open folder", self._open_folder)):
                ttk.Button(header, text=label, style="Yt.TButton",
                           command=cmd).pack(side="right", padx=(6, 0))
            # the toggle is labelled with the mode you would switch *to*
            self.themeBtn = ttk.Button(
                header, text=self._theme_button_text(), style="Yt.TButton",
                command=self._toggle_theme)
            self.themeBtn.pack(side="right", padx=(6, 0))
            tk.Frame(self, bg=p["border"], height=1).pack(fill="x")

            # --- queue controls (parallel slots, pause, selection) ---
            bar = tk.Frame(self, bg=p["bg"], padx=14, pady=6)
            bar.pack(fill="x")
            tk.Label(bar, text="Downloading", bg=p["bg"], fg=p["muted"],
                     font=(self.font, 9)).pack(side="left")
            self.slots = ttk.Combobox(
                bar, style="Yt.TCombobox", width=2, state="readonly",
                values=[str(n) for n in range(1, MAX_PARALLEL + 1)])
            self.slots.set(str(self.qm.max_concurrent()))
            self.slots.pack(side="left", padx=(6, 4))
            tk.Label(bar, text="at once", bg=p["bg"], fg=p["muted"],
                     font=(self.font, 9)).pack(side="left", padx=(0, 14))
            self.slots.bind("<<ComboboxSelected>>", self._on_slots)

            self.pauseBtn = ttk.Button(bar, text="Pause all",
                                       style="Yt.TButton",
                                       command=self._toggle_pause_all)
            self.pauseBtn.pack(side="left")

            self.selRemove = ttk.Button(bar, text="Remove selected",
                                        style="Yt.TButton",
                                        command=self._remove_selected)
            self.selRemove.pack(side="right", padx=(6, 0))
            self.selCancel = ttk.Button(bar, text="Cancel selected",
                                        style="Yt.TButton",
                                        command=self._cancel_selected)
            self.selCancel.pack(side="right", padx=(6, 0))
            tk.Frame(self, bg=p["border"], height=1).pack(fill="x")

            # --- where downloads go ---
            save = tk.Frame(self, bg=p["bg"], padx=14, pady=7)
            save.pack(fill="x")
            tk.Label(save, text="Save to", bg=p["bg"], fg=p["muted"],
                     font=(self.font, 9)).pack(side="left")
            self.savedir = tk.Label(save, text="", bg=p["bg"], fg=p["fg"],
                                    anchor="w", font=(self.font, 9))
            self.savedir.pack(side="left", fill="x", expand=True, padx=(10, 10))
            ttk.Button(save, text="Change\u2026", style="Yt.TButton",
                       command=self._choose_folder).pack(side="right")
            self._refresh_savedir()

            # --- scrolling list of cards ---
            self.canvas = tk.Canvas(self, bg=p["bg"], highlightthickness=0, bd=0)
            scroll = ttk.Scrollbar(self, orient="vertical",
                                   style="Yt.Vertical.TScrollbar",
                                   command=self.canvas.yview)
            self.canvas.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            self.canvas.pack(side="left", fill="both", expand=True)

            self.list = tk.Frame(self.canvas, bg=p["bg"], pady=6)
            self.canvas.create_window((0, 0), window=self.list, anchor="nw",
                                      tags="inner")
            self.list.bind(
                "<Configure>",
                lambda _e: self.canvas.configure(
                    scrollregion=self.canvas.bbox("all")))
            self.canvas.bind(
                "<Configure>",
                lambda e: self.canvas.itemconfigure("inner", width=e.width))

            self.empty = tk.Label(
                self.list, bg=p["bg"], fg=p["muted"], justify="center",
                font=(self.font, 10),
                text=("No downloads yet.\n\n"
                      f"Click the blue {NAME} arrow on a page to add one."))

            # --- footer ---
            self.footer = tk.Label(
                self, bg=p["header"], fg=p["muted"], anchor="w", padx=14,
                pady=6, font=(self.font, 8),
                text="Closing this window keeps downloads running in the "
                     f"background. Use Quit to stop {NAME}.")
            self.footer.pack(fill="x", side="bottom")
            tk.Frame(self, bg=p["border"], height=1).pack(fill="x",
                                                          side="bottom")

        # --- light / dark toggle ---

        def _theme_button_text(self) -> str:
            return "\u263e Dark" if self.mode == "light" else "\u2600 Light"

        def _toggle_theme(self) -> None:
            mode = "dark" if self.mode == "light" else "light"
            try:
                save_config({"ui_theme": mode})
            except Exception as exc:  # noqa: BLE001
                log("could not save ui_theme:", exc)
            self._apply_theme(mode)
            self.footer.configure(
                text=f"Queue window: {mode} mode \u2014 saved to your config.")

        def _apply_theme(self, mode: str) -> None:
            """Swap palettes and rebuild the chrome and rows in place."""
            self.mode = mode if mode in THEMES else "light"
            self.p = THEMES[self.mode]
            self.configure(bg=self.p["bg"])
            self._init_style()
            for child in list(self.winfo_children()):
                child.destroy()
            self.rows.clear()
            self._build()
            self._tick(force=True)

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
            # a Breeze card: hairline border, coloured status stripe, and a
            # tick box on the left for picking several items at once
            p = self.p
            frame = tk.Frame(self.list, bg=p["card"],
                             highlightbackground=p["border"],
                             highlightthickness=1)
            stripe = tk.Frame(frame, width=4,
                              bg=p["stripe"].get(item["status"], p["muted"]))
            stripe.pack(side="left", fill="y")
            body = tk.Frame(frame, bg=p["card"], padx=13, pady=9)
            body.pack(side="left", fill="both", expand=True)
            top = tk.Frame(body, bg=p["card"])
            top.pack(fill="x")
            check = tk.Label(top, bg=p["card"], fg=p["muted"], cursor="hand2",
                             font=(self.font, 11))
            check.pack(side="left", padx=(0, 8))
            title = tk.Label(top, bg=p["card"], fg=p["fg"], anchor="w",
                             font=(self.font, 10, "bold"))
            title.pack(side="left", fill="x", expand=True)
            btns = tk.Frame(top, bg=p["card"])
            btns.pack(side="right")
            detail = tk.Label(body, bg=p["card"], fg=p["muted"], anchor="w",
                              font=(self.font, 9))
            detail.pack(fill="x", pady=(3, 7))
            bar = ttk.Progressbar(body, style="Yt.Horizontal.TProgressbar",
                                  maximum=100.0)
            bar.pack(fill="x")
            for widget in (frame, body, top, check, title, detail):
                widget.bind("<Button-1>",
                            lambda _e, i=item["id"]: self._toggle_select(i))
            return {"frame": frame, "stripe": stripe, "body": body,
                    "title": title, "detail": detail, "check": check,
                    "bar": bar, "btns": btns, "buttons": {}, "bar_mode": None,
                    "sel_mode": None, "stripe_mode": item["status"],
                    "border_mode": p["border"]}

        def _set_buttons(self, row: dict, item: dict) -> None:
            status = item["status"]
            if status == "queued":
                wanted = [("Cancel", "cancel"), ("Remove", "remove"),
                          ("Pause", "pause"), ("\u25bc", "down"),
                          ("\u25b2", "up")]
            elif status == "running":
                wanted = [("Pause", "pause"), ("Cancel", "cancel")]
            elif status == "paused":
                wanted = [("Resume", "resume"), ("Remove", "remove"),
                          ("\u25bc", "down"), ("\u25b2", "up")]
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
                # clam's default button width is a fixed 11 characters; the
                # per-row buttons fit their label instead so a full row of
                # them still leaves the title room to breathe
                if action in ("up", "down"):
                    style, width = "Yt.Mini.TButton", 2
                else:
                    style, width = "Yt.TButton", 0
                btn = ttk.Button(row["btns"], text=name, style=style,
                                 width=width,
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
                self.selected.discard(item_id)
            elif action == "reveal":
                self._reveal_item(item_id)
            elif action == "pause":
                self.qm.pause(item_id)
            elif action == "resume":
                self.qm.resume(item_id)
            elif action == "up":
                self.qm.move(item_id, -1)
            elif action == "down":
                self.qm.move(item_id, +1)
            self._tick(force=True)

        def _update_row(self, row: dict, item: dict) -> None:
            status = item["status"]
            p = self.p
            # left stripe carries the status colour; the border lights up blue
            # while the item is actually running
            if row["stripe_mode"] != status:
                row["stripe"].configure(bg=p["stripe"].get(status, p["muted"]))
                row["stripe_mode"] = status
            selected = item["id"] in self.selected
            border = (p["accent"] if (status == "running" or selected)
                      else p["border"])
            if row["border_mode"] != border:
                row["frame"].configure(highlightbackground=border)
                row["border_mode"] = border
            if row["sel_mode"] != selected:
                row["check"].configure(
                    text="\u2611" if selected else "\u2610",
                    fg=p["accent"] if selected else p["muted"])
                row["sel_mode"] = selected
            text = item["title"] or item["url"]
            if len(text) > 110:
                text = text[:107] + "..."
            row["title"].configure(text=text)
            row["detail"].configure(text=detail_text(item))
            running = status == "running"
            determinate = running and item["stage"] in (
                "downloading", "remuxing", "converting")
            if (determinate and item["stage"] == "downloading"
                    and item["percent"] <= 0 and clip_note(item.get("spec"))):
                # A clip's opening seconds (and a very short clip all the way
                # through) have no measurable position yet, so spin rather
                # than park a determined bar at 0%.
                determinate = False
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
                if status == "done":
                    row["bar"].configure(style="Yt.Ok.Horizontal.TProgressbar")
                    row["bar"]["value"] = 100.0
                elif status == "error":
                    row["bar"].configure(style="Yt.Bad.Horizontal.TProgressbar")
                    row["bar"]["value"] = 100.0
                else:
                    row["bar"].configure(style="Yt.Horizontal.TProgressbar")
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
                    row["frame"].pack(fill="x", pady=(0, 6), padx=14)
                self._update_row(row, item)
            for item_id in list(self.rows):
                if item_id not in seen:
                    self.rows.pop(item_id)["frame"].destroy()

            if snapshot:
                self.empty.pack_forget()
            else:
                self.empty.pack(pady=60)

            counts = self.qm.counts()
            self._update_controls(counts)
            parts = []
            if counts["running"]:
                parts.append(f"{counts['running']} downloading")
            if counts["queued"]:
                parts.append(f"{counts['queued']} waiting")
            if counts["paused"]:
                parts.append(f"{counts['paused']} paused")
            done = counts["done"] + counts["error"] + counts["cancelled"]
            if done:
                parts.append(f"{done} finished")
            if self._picked:
                parts.append(f"{self._picked} selected")
            self.summary.configure(text="   ".join(parts) or "idle")
            self.update_idletasks()
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            if not force:
                self._tick_after = self.after(400, self._tick)

        def _drain_commands(self) -> None:
            while True:
                try:
                    cmd = self._commands.get_nowait()
                except _queue.Empty:
                    return
                if cmd == "show":
                    self.deiconify()
                    self.lift()
                    self._refresh_savedir(fresh=True)
                elif cmd == "quit":
                    self._quit()
                elif cmd == "shutdown":
                    self._force_quit()

        # --- window actions ---

        def _on_close(self) -> None:
            self.iconify()
            self.footer.configure(
                text="Still running in the background. Reopen from the "
                     f"{NAME} button, or use Quit to stop {NAME}.")

        def _cancel_tick(self) -> None:
            """Drop the pending refresh so it cannot fire after destroy()."""
            if self._tick_after is not None:
                try:
                    self.after_cancel(self._tick_after)
                except Exception:
                    pass
                self._tick_after = None

        def _quit(self) -> None:
            if self.qm.counts().get("running"):
                if not messagebox.askyesno(
                        NAME,
                        "A download is still running. Quit anyway?\n\n"
                        "Unfinished items will resume next time you open "
                        "the queue."):
                    return
            self._closing = True
            self._cancel_tick()
            try:
                self.destroy()
            except tk.TclError:
                pass

        def _force_quit(self) -> None:
            # like _quit but never asks: the updater is waiting on us
            self._closing = True
            self._cancel_tick()
            try:
                self.destroy()
            except tk.TclError:
                pass

        def _clear_finished(self) -> None:
            self.qm.clear_finished()
            self._tick(force=True)

        # --- queue controls ---

        def _update_controls(self, counts: dict) -> None:
            """Keep the toolbar in step with the queue."""
            self.selected &= set(self.rows)   # forget rows that went away
            picked = len(self.selected)
            busy = counts.get("queued", 0) + counts.get("running", 0)
            self.pauseBtn.configure(
                text="Pause all" if busy or not counts.get("paused")
                else "Resume all")
            self.selCancel.configure(state="normal" if picked else "disabled")
            self.selRemove.configure(state="normal" if picked else "disabled")
            self._picked = picked      # the summary line spells this out

        def _toggle_select(self, item_id: str) -> None:
            if item_id in self.selected:
                self.selected.discard(item_id)
            else:
                self.selected.add(item_id)
            self._tick(force=True)

        def _on_slots(self, _event=None) -> None:
            n = self.qm.set_max_concurrent(self.slots.get())
            self.slots.set(str(n))
            self.footer.configure(
                text=f"Downloading {n} at a time \u2014 saved to your config.")

        def _toggle_pause_all(self) -> None:
            counts = self.qm.counts()
            if counts.get("queued") or counts.get("running"):
                n = self.qm.pause_all()
                self.footer.configure(
                    text=f"Paused {n} item(s). Use Resume all to continue.")
            else:
                n = self.qm.resume_all()
                self.footer.configure(text=f"Resumed {n} item(s).")
            self._tick(force=True)

        def _cancel_selected(self) -> None:
            n = self.qm.cancel_many(sorted(self.selected))
            self.selected.clear()
            self.footer.configure(text=f"Cancelled {n} item(s).")
            self._tick(force=True)

        def _remove_selected(self) -> None:
            n = self.qm.remove_many(sorted(self.selected))
            self.selected.clear()
            self.footer.configure(text=f"Removed {n} item(s).")
            self._tick(force=True)

        def _open_folder(self) -> None:
            reset_config_cache()
            cfg = load_config()
            target = expand(cfg.get("outdir")
                                 or DEFAULT_CONFIG["outdir"])
            self._reveal(target)

        def _refresh_savedir(self, fresh: bool = False) -> None:
            if fresh:                       # another process may have changed it
                reset_config_cache()
            cfg = load_config()
            self.savedir.configure(
                text=expand(cfg.get("outdir")
                                 or DEFAULT_CONFIG["outdir"]))

        def _choose_folder(self) -> None:
            cfg = load_config()
            start = expand(cfg.get("outdir")
                                or DEFAULT_CONFIG["outdir"])
            path = choose_folder(start, parent=self)
            if not path:
                return
            save_config({"outdir": path})
            self._refresh_savedir()
            self.footer.configure(text=f"New downloads save to {path}")

        def _reveal_item(self, item_id: str) -> None:
            for item in self.qm.snapshot():
                if item["id"] == item_id:
                    self._reveal(item["path"] or item["url"])
                    return

        def _reveal(self, path: str) -> None:
            try:
                if path and os.path.isfile(path):
                    subprocess.Popen(["explorer", "/select,", path],
                                     **child_kwargs())
                elif path and os.path.isdir(path):
                    subprocess.Popen(["explorer", path],
                                     **child_kwargs())
                else:
                    raise OSError("path does not exist")
            except Exception as exc:  # noqa: BLE001
                messagebox.showwarning(NAME, f"Could not open:\n{exc}")
