"""Debug logging shared by the host, queue window and CLI."""

from __future__ import annotations

import os
import sys
DEBUG = bool(os.environ.get("YTGRAB_DEBUG"))


def log(*parts: object) -> None:
    if DEBUG:
        print("[ytgrab]", *parts, file=sys.stderr, flush=True)
