"""Console output helpers for the CLI."""

from __future__ import annotations

import sys
def _init_console() -> None:
    """Make non-ASCII release notes survive a legacy Windows console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
def say(text: str = "") -> None:
    print(text, flush=True)
def head(text: str) -> None:
    say()
    say(f"== {text}")
