"""ytgrab: a Firefox downloader that feeds Premiere Pro.

The package is layered: ``domain`` holds pure logic, ``services`` orchestrates
it, ``infrastructure`` talks to the outside world, and ``presentation`` shows
the result.  Dependencies only ever point inwards.
"""
from __future__ import annotations

import os

APP = "ytgrab"                   # frozen id: config dir, native host, exe names
NAME = "completeYtDownloader"    # the display name users see
VERSION = "0.3.15"
__version__ = VERSION

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_ROOT = os.path.dirname(PACKAGE_DIR)

__all__ = ["APP", "NAME", "VERSION", "__version__", "PACKAGE_DIR", "SRC_ROOT"]
