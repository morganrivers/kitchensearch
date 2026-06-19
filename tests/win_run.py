#!/usr/bin/env python3
"""Replay a recorded test script on Windows and save the GIF + clipboard.

This is the Windows counterpart to ``mac_run.py``: it drives the app through
the Win32 ``WinTestHarness`` (SendInput / EnumWindows / PIL.ImageGrab) and
writes a per-test ``recording.gif`` plus a clipboard dump. It does *no*
baseline comparison — its only job is to produce the artifacts the CI workflow
uploads.

Usage:
    python tests/win_run.py test_01_main_menu
    python tests/win_run.py test_01_main_menu --output-dir tests/test_run
    python tests/win_run.py test_01_main_menu --require-clipboard

Exit codes:
    0  test ran end-to-end (and clipboard non-empty if --require-clipboard)
    1  test raised, or clipboard required but empty
"""
import argparse
import importlib.util
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TESTS_DIR))

from win_harness import WinTestHarness, dump_clipboard

_SCRIPTS_DIR = _TESTS_DIR / "scripts"
_DEFAULT_OUT = _TESTS_DIR / "test_run"


def _load_script(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    p = argparse.ArgumentParser()
    p.add_argument("test_name", help="e.g. test_01_main_menu")
    p.add_argument("--output-dir", default=str(_DEFAULT_OUT),
                   help="parent directory for the per-test run folder")
    p.add_argument("--require-clipboard", action="store_true",
                   help="fail with rc=1 if the clipboard is empty after the test")
    args = p.parse_args()

    script = _SCRIPTS_DIR / f"{args.test_name}.py"
    assert script.exists(), f"no such test script: {script}"

    companion = script.with_suffix(".json")
    out_dir   = Path(args.output_dir) / args.test_name
    out_dir.mkdir(parents=True, exist_ok=True)

    mod = _load_script(script)
    harness = WinTestHarness(
        run_dir=out_dir,
        settings_path=companion if companion.exists() else None,
    )

    print(f"\n  Running {args.test_name} on Windows …")
    failed = False
    try:
        with harness as h:
            mod.run(h)
            gif = out_dir / "recording.gif"
            h.make_gif(gif)
            print(f"  GIF → {gif}")
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        failed = True

    clip = dump_clipboard(out_dir)
    if clip:
        print(f"  Clipboard → {clip}")
    else:
        print("  Clipboard was empty")
        if args.require_clipboard:
            failed = True

    stderr = harness.meaningful_stderr
    if stderr:
        print(f"  STDERR:\n{stderr}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
