#!/usr/bin/env python3
"""Run the ytgrab regression suites.

    python tests/run_all.py          # the offline suites (no network)
    python tests/run_all.py --net    # also the suites that hit YouTube

Each ``check_*.py`` file is a standalone script: it imports the package from
``src/``, prints its own PASS/FAIL report and exits non-zero when something
breaks.  This runner just drives them in order, streams their output, and
prints one summary so CI and local runs behave the same way.

The network suites download real videos (a 19 s clip and a 10 min one), so they
are opt-in: they need yt-dlp, ffmpeg and working cookies for age/region gates.
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

OFFLINE = [
    "check_editor.py",
    "check_clip_progress.py",
    "check_queue.py",
    "check_editor_e2e.py",
    "check_controls.py",
    "check_theme.py",
]
NETWORK = [
    "check_editor_net.py",
    "check_clip_progress_net.py",
]


def run(suite: str) -> int:
    print(f"\n{'=' * 72}\n== {suite}\n{'=' * 72}", flush=True)
    # The Tk suites print the queue's arrows (▲ ▼) and the theme toggle's moon
    # (☾). A cp1252 console cannot encode those and the suite dies on a
    # UnicodeEncodeError, so pin the child's stdio to UTF-8 rather than relying
    # on whoever is running this to have set PYTHONIOENCODING.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, "-u", os.path.join(HERE, suite)],
                          cwd=os.path.dirname(HERE), env=env)
    return proc.returncode


def main() -> int:
    suites = list(OFFLINE)
    if "--net" in sys.argv:
        suites += NETWORK

    failed = []
    for suite in suites:
        if run(suite) != 0:
            failed.append(suite)

    print(f"\n{'=' * 72}")
    if failed:
        print(f"FAILED: {len(failed)}/{len(suites)} suites -> {', '.join(failed)}")
        return 1
    print(f"ALL PASS: {len(suites)} suites")
    return 0


if __name__ == "__main__":
    sys.exit(main())
