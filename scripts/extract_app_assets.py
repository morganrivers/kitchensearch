#!/usr/bin/env python3
"""Extract data/app_assets.tar.gz into data/ for the build.

Cross-platform replacement for `tar -xzf` so the build works on Windows
without needing tar on PATH.
"""
from __future__ import annotations

import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
ARCHIVE = REPO / "data" / "app_assets.tar.gz"
DEST = REPO


def main() -> int:
    if not ARCHIVE.is_file():
        print(f"ERROR: archive not found: {ARCHIVE}", file=sys.stderr)
        return 1

    print(f"Extracting {ARCHIVE.name} -> {DEST}")
    with tarfile.open(ARCHIVE, "r:gz") as tf:
        # Python 3.12+: 'data' filter rejects unsafe members (absolute paths,
        # symlinks escaping the dest, etc). Falls back gracefully on older Python.
        try:
            tf.extractall(DEST, filter="data")
        except TypeError:
            tf.extractall(DEST)

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
