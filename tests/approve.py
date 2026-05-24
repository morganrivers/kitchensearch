#!/usr/bin/env python3
"""
Approve unapproved baselines, moving them into baseline_approved.

Usage:
    python tests/approve.py                     # approve all unapproved tests
    python tests/approve.py test_01 test_02     # approve by name prefix
"""

import shutil
import sys
from pathlib import Path

_TESTS_DIR           = Path(__file__).parent
_BASELINE_UNAPPROVED = _TESTS_DIR / "baseline_unapproved"
_BASELINE_APPROVED   = _TESTS_DIR / "baseline_approved"


def main():
    if not _BASELINE_UNAPPROVED.exists():
        print("No unapproved baselines found.")
        sys.exit(0)

    candidates = sorted(_BASELINE_UNAPPROVED.glob("test_*/"))
    if not candidates:
        print("No unapproved baselines found.")
        sys.exit(0)

    filters = sys.argv[1:]
    if filters:
        candidates = [p for p in candidates if any(p.name.startswith(f) for f in filters)]
        if not candidates:
            print(f"No unapproved baselines matching: {filters}")
            sys.exit(1)

    _BASELINE_APPROVED.mkdir(parents=True, exist_ok=True)

    for src in candidates:
        dst = _BASELINE_APPROVED / src.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        shutil.rmtree(src)
        print(f"  Approved: {src.name}")

    print(f"\n  {len(candidates)} test(s) approved → {_BASELINE_APPROVED}")


if __name__ == "__main__":
    main()
