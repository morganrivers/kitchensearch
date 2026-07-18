#!/usr/bin/env python3
"""Replay a recorded test script on Linux/X11 and save the GIF + clipboard.

This is the Linux counterpart to ``mac_run.py``: it drives the app through the
X11 ``TestHarness`` (xdotool / xclip / ImageMagick) under a virtual framebuffer
and writes a per-test ``recording.gif`` plus a clipboard dump. Unlike
``run_tests.py`` it does *no* baseline comparison — its only job is to produce
the artifacts the CI workflow uploads.

The script re-execs itself under ``xvfb-run`` when there is no display, so it
can be invoked directly:

    python tests/linux_run.py test_01_main_menu
    python tests/linux_run.py test_01_main_menu --output-dir tests/test_run
    python tests/linux_run.py test_01_main_menu --require-clipboard

Exit codes:
    0  test ran end-to-end (and clipboard non-empty if --require-clipboard)
    1  test raised, or clipboard required but empty
"""
import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TESTS_DIR))

_SCRIPTS_DIR = _TESTS_DIR / "scripts"
_DEFAULT_OUT = _TESTS_DIR / "test_run"


def _load_script(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def dump_clipboard(out_dir: Path):
    """Save the current X11 clipboard as PNG (preferred) or text.

    Returns the written path, or None if the clipboard is empty.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    r = subprocess.run(
        ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
        capture_output=True,
    )
    # xclip may echo a text target back when no real image/png is present, so
    # only treat it as an image when the PNG signature is actually there.
    if r.returncode == 0 and r.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        png = out_dir / "clipboard.png"
        png.write_bytes(r.stdout)
        return png

    r = subprocess.run(
        ["xclip", "-selection", "clipboard", "-o"],
        capture_output=True,
    )
    if r.returncode == 0 and r.stdout:
        txt = out_dir / "clipboard.txt"
        txt.write_bytes(r.stdout)
        return txt
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("test_name", help="e.g. test_01_main_menu")
    p.add_argument("--output-dir", default=str(_DEFAULT_OUT),
                   help="parent directory for the per-test run folder")
    p.add_argument("--require-clipboard", action="store_true",
                   help="fail with rc=1 if the clipboard is empty after the test")
    p.add_argument("--check-clipboard", action="store_true",
                   help="after the run, verify every step that should copy an image "
                        "copied the same image as the approved baseline (fail rc=1 if not)")
    args = p.parse_args()

    # Auto-wrap under xvfb-run unless we already have a display (real or virtual).
    if not os.environ.get("DISPLAY") and not os.environ.get("_XVFB_WRAPPED"):
        env = os.environ.copy()
        env["_XVFB_WRAPPED"] = "1"
        result = subprocess.run(
            ["xvfb-run", "-a", sys.executable] + sys.argv,
            env=env,
        )
        sys.exit(result.returncode)

    from harness import TestHarness

    script = _SCRIPTS_DIR / f"{args.test_name}.py"
    assert script.exists(), f"no such test script: {script}"

    companion = script.with_suffix(".json")
    out_dir   = Path(args.output_dir) / args.test_name
    out_dir.mkdir(parents=True, exist_ok=True)

    mod = _load_script(script)
    harness = TestHarness(
        run_dir=out_dir,
        settings_path=companion if companion.exists() else None,
    )

    print(f"\n  Running {args.test_name} on Linux/X11 …")
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

    if args.check_clipboard:
        from check_clipboard import check_test, _print_report
        _BASELINE = _TESTS_DIR / "baseline_approved"
        result = check_test(args.test_name, Path(args.output_dir), _BASELINE)
        print()
        _print_report(result)
        if result["expected"] and not result["passed"]:
            driver_errors.append("clipboard does not match approved baseline")
            failed = True

    # Record the driver-level error (raised exception / clipboard mismatch)
    # alongside the app's own stderr so the render-health gate reports the real
    # cause instead of "no stderr captured".
    status_detail = "\n".join(driver_errors + ([app_stderr] if app_stderr else []))
    from run_common import write_status
    write_status(out_dir, failed, status_detail)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
