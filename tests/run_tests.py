#!/usr/bin/env python3
"""
Test runner for the emoji kitchen visual regression suite.

Usage:
    python tests/run_tests.py                   # run all tests, compare against baseline
    python tests/run_tests.py --update-baseline # capture new baseline, clear test_run
    python tests/run_tests.py --test test_01    # run a single test by name prefix
    python tests/run_tests.py --show-ui         # run on real display instead of xvfb
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Make sure tests/ is on the path
_TESTS_DIR        = Path(__file__).parent
sys.path.insert(0, str(_TESTS_DIR))

from harness import TestHarness
from compare import compare_runs, print_summary

_REPO             = _TESTS_DIR.parent
_SCRIPTS_DIR      = _TESTS_DIR / "scripts"
_BASELINE_DIR     = _TESTS_DIR / "baseline"
_BASELINE_RUNS_DIR = _TESTS_DIR / "baseline_runs"
_TEST_RUN_DIR     = _TESTS_DIR / "test_run"


def _load_script(path: Path):
    spec   = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_test(script_path: Path, run_dir: Path):
    """Launch the app, execute the test script, close cleanly."""
    print(f"\n  Running {script_path.stem} …")
    module    = _load_script(script_path)
    test_dir  = run_dir / script_path.stem
    test_dir.mkdir(parents=True, exist_ok=True)

    companion = script_path.with_suffix(".json")
    try:
        with TestHarness(run_dir=test_dir, settings_path=companion if companion.exists() else None) as h:
            module.run(h)
            gif_path = test_dir / "recording.gif"
            h.make_gif(gif_path)
            print(f"  GIF → {gif_path}")
    except Exception as exc:
        print(f"  ERROR in {script_path.stem}: {exc}")
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-baseline", action="store_true",
                        help="capture new baseline images, clear test_run")
    parser.add_argument("--test", default=None,
                        help="only run scripts whose name starts with this prefix")
    parser.add_argument("--show-ui", action="store_true",
                        help="run on the real display instead of a virtual framebuffer")
    args = parser.parse_args()

    # Auto-wrap under xvfb-run unless --show-ui or already wrapped
    if not args.show_ui and not os.environ.get("_XVFB_WRAPPED"):
        env = os.environ.copy()
        env["_XVFB_WRAPPED"] = "1"
        result = subprocess.run(
            ["xvfb-run", "-a", sys.executable] + sys.argv,
            env=env,
        )
        sys.exit(result.returncode)

    # ── discover test scripts ─────────────────────────────────────────────────
    scripts = sorted(_SCRIPTS_DIR.glob("test_*.py"))
    if args.test:
        scripts = [s for s in scripts if s.stem.startswith(args.test)]
    if not scripts:
        print("No test scripts found.")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.update_baseline:
        # Save images to a timestamped baseline_runs folder
        run_dir = _BASELINE_RUNS_DIR / timestamp
    else:
        # Always overwrite the fixed test_run folder
        if _TEST_RUN_DIR.exists():
            shutil.rmtree(_TEST_RUN_DIR)
        run_dir = _TEST_RUN_DIR

    print(f"\n{'═'*60}")
    print(f"  {'Baseline capture' if args.update_baseline else 'Test run'}: {timestamp}")
    print(f"  Scripts: {[s.stem for s in scripts]}")
    print(f"{'═'*60}")

    for script in scripts:
        run_test(script, run_dir)

    # ── update baseline mode ──────────────────────────────────────────────────
    if args.update_baseline:
        for script in scripts:
            test_name     = script.stem
            test_run_dir  = run_dir / test_name
            test_base_dir = _BASELINE_DIR / test_name
            test_base_dir.mkdir(parents=True, exist_ok=True)
            for src in test_run_dir.glob("*.png"):
                if "_clipboard" not in src.stem:
                    shutil.copy2(src, test_base_dir / src.name)
            for src in test_run_dir.glob("*.json"):
                if src.stem != "results":
                    shutil.copy2(src, test_base_dir / src.name)
        # Clear test_run so viewer can't open a stale diff
        if _TEST_RUN_DIR.exists():
            shutil.rmtree(_TEST_RUN_DIR)
        print("\n  Baseline updated.")
        sys.exit(0)

    # ── compare against baseline ──────────────────────────────────────────────
    all_results: dict[str, dict] = {}
    diff_dir = _TEST_RUN_DIR / "_diffs"
    for script in scripts:
        test_name     = script.stem
        test_run_dir  = _TEST_RUN_DIR / test_name
        test_base_dir = _BASELINE_DIR / test_name
        test_diff_dir = diff_dir / test_name

        results = compare_runs(test_base_dir, test_run_dir, test_diff_dir)
        all_results[test_name] = results

        (test_run_dir / "results.json").write_text(json.dumps(results, indent=2))

    # ── print summary ─────────────────────────────────────────────────────────
    total_changed = 0
    for test_name, results in all_results.items():
        print(f"\n  [{test_name}]")
        print_summary(results)
        total_changed += sum(1 for r in results.values() if r["status"] != "ok")

    pct_tests_changed = (
        100 * total_changed / max(1, sum(len(r) for r in all_results.values()))
    )
    print(f"  Total changed: {total_changed}  ({pct_tests_changed:.1f}% of all screenshots)\n")

    if total_changed:
        print(f"  python tests/viewer.py {_TEST_RUN_DIR}")
    else:
        print("  All screenshots match baseline.")
    sys.exit(1 if total_changed else 0)


if __name__ == "__main__":
    main()
