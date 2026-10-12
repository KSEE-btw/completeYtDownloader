"""ytgrab: a Firefox downloader that feeds Premiere Pro.

The package is layered: ``domain`` holds pure logic, ``services`` orchestrates
it, ``infrastructure`` talks to the outside world, and ``presentation`` shows
the result.  Dependencies only ever point inwards.
"""
from __future__ import annotations

import os

APP = "ytgrab"
VERSION = "0.3.14"
__version__ = VERSION

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_ROOT = os.path.dirname(PACKAGE_DIR)

__all__ = ["APP", "VERSION", "__version__", "PACKAGE_DIR", "SRC_ROOT"]
