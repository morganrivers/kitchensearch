#!/usr/bin/env python3
"""Replay a recorded test script on macOS and save the GIF + clipboard.

Usage:
    python tests/mac_run.py test_34_macsimple
    python tests/mac_run.py test_34_macsimple --output-dir tests/test_run
    python tests/mac_run.py test_34_macsimple --require-clipboard

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

from mac_harness import MacTestHarness, dump_clipboard

_SCRIPTS_DIR = _TESTS_DIR / "scripts"
_DEFAULT_OUT = _TESTS_DIR / "test_run"


def _load_script(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    p = argparse.ArgumentParser()
    p.add_argument("test_name", help="e.g. test_34_macsimple")
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
    harness = MacTestHarness(
        run_dir=out_dir,
        settings_path=companion if companion.exists() else None,
    )

    print(f"\n  Running {args.test_name} on macOS …")
    failed = False
    driver_errors: list[str] = []
    try:
        with harness as h:
            mod.run(h)
            gif = out_dir / "recording.gif"
            h.make_gif(gif)
            print(f"  GIF → {gif}")
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        print(f"  ERROR: {msg}", file=sys.stderr)
        driver_errors.append(msg)
        failed = True

    clip = dump_clipboard(out_dir)
    if clip:
        print(f"  Clipboard → {clip}")
    else:
        print("  Clipboard was empty")
        if args.require_clipboard:
            driver_errors.append("clipboard required but empty after test")
            failed = True

    app_stderr = harness.meaningful_stderr
    if app_stderr:
        print(f"  STDERR:\n{app_stderr}")

    # Record the driver-level error (the raised exception / missing clipboard)
    # alongside the app's own stderr so the render-health gate reports the real
    # cause instead of "no stderr captured".
    status_detail = "\n".join(driver_errors + ([app_stderr] if app_stderr else []))
    from run_common import write_status
    write_status(out_dir, failed, status_detail)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
