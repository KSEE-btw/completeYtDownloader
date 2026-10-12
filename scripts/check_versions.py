#!/usr/bin/env python3
"""Keep the version in one place.

The version lives in extension/manifest.json.  src/ytgrab/__init__.py repeats it
(so the executables know their own version), and docs/releases/v<version>.md
must exist because the release workflow uses it as the release body.  If any of
the three drifts, `ytgrab update` and the release build start disagreeing about
what "the latest version" is -- so this fails the build early.

Run locally with:  python scripts/check_versions.py
"""

from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    problems = []

    manifest_path = os.path.join(ROOT, "extension", "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest_version = json.load(fh)["version"]

    core_path = os.path.join(ROOT, "src", "ytgrab", "__init__.py")
    with open(core_path, "r", encoding="utf-8") as fh:
        core_text = fh.read()
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', core_text, re.M)
    core_version = match.group(1) if match else None

    print(f"extension/manifest.json : {manifest_version}")
    print(f"src/ytgrab/__init__.py  : {core_version or '(VERSION not found)'}")

    if core_version is None:
        problems.append('src/ytgrab/__init__.py has no VERSION = "..." line')
    elif core_version != manifest_version:
        problems.append(
            f"version mismatch: manifest.json is {manifest_version} "
            f"but src/ytgrab/__init__.py is {core_version}")

    notes_rel = os.path.join("docs", "releases", f"v{manifest_version}.md")
    notes_path = os.path.join(ROOT, notes_rel)
    if os.path.isfile(notes_path):
        print(f"release notes           : {notes_rel}")
    else:
        problems.append(f"missing release notes for {manifest_version}: {notes_rel}")

    print()
    if problems:
        for problem in problems:
            print(f"::error::{problem}")
            print(f"FAIL: {problem}")
        return 1
    print("OK: versions agree and the release notes exist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
