#!/usr/bin/env python3
"""Render-health gate for the cross-platform smoke tests.

Replaces the old exact-hash-vs-baseline clipboard check, which failed on every
harmless re-encoding of the copied image and forced constant re-baselining.

This gate is baseline-free. It fails only on signals that an OS is actually
broken, and passes on cosmetic pixel differences between OSes:

  * a bug surfaced during the run          (driver recorded failed / stderr)
  * a screenshot rendered blank / solid    (black screen, nothing drawn)
  * a copied clipboard image is blank/black/transparent
  * the whole run copied nothing at all    (total copy-path breakage)

Per-OS cross comparison of the actual images is a separate, non-gating tool:
see ``tests/os_compare.py``.

Usage:
    python tests/health_check.py --all --run-dir tests/test_run
    python tests/health_check.py --all --skip test_16_story --json

Exit code: 0 healthy, 1 if any test is broken.
"""
import argparse
import json
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TESTS_DIR))

from run_common import force_utf8_stdio, image_health, read_status  # noqa: E402

_DEFAULT_RUN = _TESTS_DIR / "test_run"


_COPY_LOG = "copied-images.log"


def _screenshots(run: Path):
    return [p for p in sorted(run.glob("*.png"))
            if not p.name.endswith("_clipboard.png") and p.name != "clipboard.png"]


def _clipboard_images(run: Path):
    # Linux captures a per-step <step>_clipboard.png; mac/Windows dump a single
    # final clipboard.png. Check whichever the OS produced.
    return sorted(set(run.glob("*_clipboard.png")) | set(run.glob("clipboard.png")))


def _copy_events(run: Path) -> int:
    """Number of copies the app logged (cross-OS: written on every copy)."""
    log = run / _COPY_LOG
    if not log.exists():
        return 0
    return sum(1 for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
               if line.split("\t", 1)[0].strip())


def check_test(test_name: str, run_dir: Path) -> dict:
    run = run_dir / test_name
    problems = []

    if not run.exists():
        return {"test": test_name, "passed": False, "clipboard_images": 0,
                "copy_events": 0,
                "problems": ["run folder missing - test produced no output"]}

    status = read_status(run)
    if status and status.get("failed"):
        detail = status.get("stderr", "").strip().splitlines()
        tail = detail[-1] if detail else "no stderr captured"
        problems.append(f"driver reported failure: {tail}")

    for shot in _screenshots(run):
        ok, reason = image_health(shot, "screenshot")
        if not ok:
            problems.append(f"screenshot {shot.name}: {reason}")

    clips = _clipboard_images(run)
    for clip in clips:
        ok, reason = image_health(clip, "clipboard")
        if not ok:
            problems.append(f"clipboard {clip.name}: {reason}")

    return {"test": test_name, "passed": not problems,
            "clipboard_images": len(clips), "copy_events": _copy_events(run),
            "problems": problems}


def _collect_tests(run_dir: Path, explicit, skip):
    if explicit:
        return [explicit]
    return [d.name for d in sorted(run_dir.iterdir())
            if d.is_dir() and d.name.startswith("test_") and d.name not in skip]


def main():
    force_utf8_stdio()

    p = argparse.ArgumentParser()
    p.add_argument("test_name", nargs="?", help="e.g. test_02_keyword_search")
    p.add_argument("--all", action="store_true", help="check every test folder in --run-dir")
    p.add_argument("--run-dir", default=str(_DEFAULT_RUN))
    p.add_argument("--skip", action="append", default=[], metavar="TEST",
                   help="test to exclude (repeatable)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if not args.all and not args.test_name:
        p.error("provide a test name or --all")

    run_dir = Path(args.run_dir)
    tests = _collect_tests(run_dir, args.test_name if not args.all else None, set(args.skip))
    results = [check_test(t, run_dir) for t in tests]

    total_copies = sum(r["copy_events"] for r in results)
    suite_problem = None
    if results and total_copies == 0:
        suite_problem = "no image copy was logged by any test - copy path likely broken"

    if args.json:
        print(json.dumps({"results": results, "suite_problem": suite_problem}, indent=2))
    else:
        print("\n  Render-health check (baseline-free)")
        print("  " + "-" * 56)
        for r in results:
            verdict = "PASS" if r["passed"] else "FAIL"
            print(f"  [{r['test']}] {verdict}  "
                  f"({r['clipboard_images']} clipboard image(s), {r['copy_events']} copy event(s))")
            for prob in r["problems"]:
                print(f"      X {prob}")
        if suite_problem:
            print(f"  X SUITE: {suite_problem}")
        print("  " + "-" * 56)
        passed = sum(1 for r in results if r["passed"])
        print(f"  {passed}/{len(results)} test(s) healthy")
        if args.skip:
            print(f"  skipped: {', '.join(args.skip)}")
        print()

    broken = any(not r["passed"] for r in results) or suite_problem is not None
    sys.exit(1 if broken else 0)


if __name__ == "__main__":
    main()
