"""The single place that touches tkinter.

Importing this module never fails: when there is no display (or no tkinter at
all, as in the CLI bundle) ``TK_OK`` is False and the names are None.  The queue
window is only defined when ``TK_OK`` is true, so headless runs still work.
"""
from __future__ import annotations

try:
    import tkinter as tk
    from tkinter import messagebox
    from tkinter import ttk

    TK_OK = True
except Exception:                     # pragma: no cover - no display / no Tk
    tk = ttk = messagebox = None      # type: ignore[assignment]
    TK_OK = False

__all__ = ["TK_OK", "messagebox", "tk", "ttk"]
