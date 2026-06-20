#!/usr/bin/env python3
"""Verify that the test steps which should copy an image actually copied the
*correct* image.

The approved baseline records a per-step clipboard image
(``<step>_clipboard.png``) at every point where the app copies a combo image
to the clipboard. This script treats those baseline images as the source of
truth and checks that a freshly-run test reproduced them:

  1. at each step the baseline copied an image, the run also put an image on
     the clipboard (not empty, not plain text), and
  2. the copied image matches the baseline image.

This is Linux-oriented: the baseline clipboard images were captured by the
X11 harness (``xclip`` image/png), so compare a Linux run against them. The
Windows/macOS clipboard formats differ, so this check is not meaningful there.

Usage:
    python tests/check_clipboard.py test_02_keyword_search
    python tests/check_clipboard.py test_02_keyword_search --run-dir tests/test_run
    python tests/check_clipboard.py --all
    python tests/check_clipboard.py --all --json

Exit code:
    0  every expected image-copy step reproduced a matching image
       (or the test has no image-copy steps)
    1  a copy was missing, copied text/empty instead, or the image differed
"""
import argparse
import json
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TESTS_DIR))

from compare import compare_images

_BASELINE = _TESTS_DIR / "baseline_approved"
_DEFAULT_RUN = _TESTS_DIR / "test_run"

# A copied image is the same source PNG each run, so it should be near-identical;
# allow a small margin for PNG re-encoding / clipboard round-tripping.
_MATCH_THRESHOLD_PCT = 2.0


def check_test(test_name: str, run_dir: Path, baseline_dir: Path) -> dict:
    """Check one test. Returns a result dict with per-step outcomes."""
    base = baseline_dir / test_name
    run  = run_dir / test_name

    expected = sorted(base.glob("*_clipboard.png"))
    steps: list[dict] = []

    for bclip in expected:
        step = bclip.name[: -len("_clipboard.png")]
        rclip_png = run / f"{step}_clipboard.png"
        rclip_txt = run / f"{step}_clipboard.txt"

        if rclip_png.exists():
            try:
                _, pct = compare_images(bclip, rclip_png)
            except Exception as exc:
                steps.append({"step": step, "status": "error",
                              "detail": f"compare failed: {exc}"})
                continue
            if pct <= _MATCH_THRESHOLD_PCT:
                steps.append({"step": step, "status": "ok",
                              "detail": f"image matches ({pct:.2f}% diff)"})
            else:
                steps.append({"step": step, "status": "mismatch",
                              "detail": f"copied image differs from baseline ({pct:.2f}%)"})
        elif rclip_txt.exists():
            txt = rclip_txt.read_text(errors="replace").strip()
            steps.append({"step": step, "status": "wrong_type",
                          "detail": f"expected an image, but text was copied: {txt!r}"})
        else:
            steps.append({"step": step, "status": "missing",
                          "detail": "expected an image copy, but clipboard had nothing"})

    passed = all(s["status"] == "ok" for s in steps)
    return {"test": test_name, "expected": len(expected),
            "passed": passed, "steps": steps,
            "run_exists": run.exists()}


def _print_report(result: dict) -> None:
    name = result["test"]
    n = result["expected"]
    if n == 0:
        print(f"  [{name}] no image-copy steps — nothing to verify")
        return
    icon = "PASS" if result["passed"] else "FAIL"
    print(f"  [{name}] {icon}  ({n} image-copy step(s))")
    for s in result["steps"]:
        mark = {"ok": "✓", "mismatch": "≠", "missing": "∅",
                "wrong_type": "T", "error": "!"}.get(s["status"], "?")
        if s["status"] != "ok":
            print(f"      {mark} {s['step']}: {s['detail']}")
    if result["passed"]:
        print(f"      ✓ all {n} copied image(s) match the baseline")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("test_name", nargs="?", help="e.g. test_02_keyword_search")
    p.add_argument("--all", action="store_true",
                   help="check every test that has baseline clipboard images")
    p.add_argument("--run-dir", default=str(_DEFAULT_RUN),
                   help="parent dir holding per-test run folders")
    p.add_argument("--baseline-dir", default=str(_BASELINE),
                   help="parent dir holding approved baselines")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = p.parse_args()

    run_dir  = Path(args.run_dir)
    base_dir = Path(args.baseline_dir)

    if args.all:
        tests = sorted(d.name for d in base_dir.iterdir()
                       if d.is_dir() and any(d.glob("*_clipboard.png")))
    elif args.test_name:
        tests = [args.test_name]
    else:
        p.error("provide a test name or --all")

    results = [check_test(t, run_dir, base_dir) for t in tests]

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("\n  Clipboard image-copy verification")
        print("  " + "─" * 50)
        for r in results:
            _print_report(r)

    # A test with no expected copies is not a failure; a missing run dir for a
    # test that *should* have copies is.
    failed = [r for r in results if r["expected"] and not r["passed"]]
    no_run = [r for r in results if r["expected"] and not r["run_exists"]]
    if not args.json:
        total_expected = sum(r["expected"] for r in results)
        ok_steps = sum(1 for r in results for s in r["steps"] if s["status"] == "ok")
        print("  " + "─" * 50)
        print(f"  {ok_steps}/{total_expected} image-copy step(s) verified across "
              f"{len(results)} test(s)")
        if no_run:
            print(f"  note: no run output found for {[r['test'] for r in no_run]}")
        print()

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
