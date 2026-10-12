"""``python -m ytgrab`` runs the console tool."""
from __future__ import annotations

import sys

from ytgrab.presentation.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
