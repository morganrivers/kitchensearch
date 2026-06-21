#!/usr/bin/env python3
"""Verify that the test steps which should copy an image actually copied the
*correct* image — cross-platform, by content hash.

How it works
------------
The app copies a combo image read from a source PNG file. That source PNG is
byte-identical on every OS (it's the same downloaded asset), even though what
lands on the clipboard differs per platform (CF_DIB on Windows, raw PNG on
macOS/Linux). So instead of comparing clipboards, the app hashes the *source*
image it copies (when ``KITCHENSEARCH_COPY_LOG`` is set) and appends the hash
to a per-test log. This script compares those hashes to the expected set.

The expected hashes are derived from the approved baseline's per-step
``<step>_clipboard.png`` files: on Linux the clipboard holds the exact source
bytes, so ``sha256(baseline_clipboard.png) == sha256(source) ==`` the hash the
app logs on *any* platform. That makes this check valid on Windows, macOS and
Linux against a single baseline.

(The harness re-captures the same clipboard image at every screenshot, so the
baseline can hold the same image many times; we compare the set of *distinct*
expected images, each of which must have been copied at least once.)

Usage:
    python tests/check_clipboard.py test_02_keyword_search
    python tests/check_clipboard.py --all
    python tests/check_clipboard.py --all --run-dir tests/test_run --json

Exit code:
    0  every distinct expected image was copied (or the test copies no images)
    1  an expected image was never copied, or no copy log was produced
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

_TESTS_DIR   = Path(__file__).parent
_BASELINE    = _TESTS_DIR / "baseline_approved"
_DEFAULT_RUN = _TESTS_DIR / "test_run"
_COPY_LOG    = "copied-images.log"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Steps whose copied image is produced by the emoji-story generator. That image
# is model-rendered, so its bytes are NOT identical across operating systems
# (unlike the downloaded emoji-kitchen combo assets, which are). Requiring it to
# match a single baseline can never pass cross-platform — the same reason
# test_16_story is skipped wholesale via --skip. Here test_30 mixes deterministic
# combo copies with one story copy at the very end, so we drop just the story
# steps and still verify every reproducible copy in the test.
_NONREPRODUCIBLE_STEPS: dict[str, frozenset[str]] = {
    "test_30_manyresults": frozenset(
        {"43_Return", "44_step", "45_click_223_250"}
    ),
}


def _expected_images(base: Path) -> dict[str, list[str]]:
    """{image_hash: [baseline step names...]} for every reproducible copy."""
    skip_steps = _NONREPRODUCIBLE_STEPS.get(base.name, frozenset())
    exp: dict[str, list[str]] = {}
    for clip in sorted(base.glob("*_clipboard.png")):
        step = clip.name[: -len("_clipboard.png")]
        if step in skip_steps:
            continue
        exp.setdefault(_sha256(clip.read_bytes()), []).append(step)
    return exp


def _copied_hashes(run: Path) -> tuple[list[str], bool]:
    """(hashes the app logged copying, log_present)."""
    log = run / _COPY_LOG
    if not log.exists():
        return [], False
    hashes = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        h = line.split("\t", 1)[0].strip()
        if h:
            hashes.append(h)
    return hashes, True


def check_test(test_name: str, run_dir: Path, baseline_dir: Path) -> dict:
    base = baseline_dir / test_name
    run  = run_dir / test_name

    expected = _expected_images(base)              # distinct image -> steps
    copied, log_present = _copied_hashes(run)
    copied_set = set(copied)

    images = []
    for h, steps in expected.items():
        if h in copied_set:
            images.append({"hash": h, "steps": steps, "status": "ok",
                           "detail": f"copied (baseline step(s): {', '.join(steps)})"})
        else:
            images.append({"hash": h, "steps": steps, "status": "missing",
                           "detail": f"expected image (baseline step(s) {', '.join(steps)}) "
                                     f"was never copied"})

    # Images the app copied that the baseline never recorded — informational.
    extra = sorted(copied_set - set(expected))

    passed = all(i["status"] == "ok" for i in images)
    # If we expected copies but got no log at all, that's a hard failure.
    if expected and not log_present:
        passed = False

    return {"test": test_name,
            "expected": len(expected),
            "copied_total": len(copied),
            "extra": extra,
            "log_present": log_present,
            "run_exists": run.exists(),
            "passed": passed,
            "images": images}


def _print_report(result: dict) -> None:
    name, n = result["test"], result["expected"]
    if n == 0:
        print(f"  [{name}] no image-copy steps - nothing to verify")
        return
    ok = sum(1 for i in result["images"] if i["status"] == "ok")
    verdict = "PASS" if result["passed"] else "FAIL"
    print(f"  [{name}] {verdict}  ({ok}/{n} distinct image(s) copied; "
          f"{result['copied_total']} copy event(s) logged)")
    if not result["log_present"] and n:
        print(f"      ! no {_COPY_LOG} produced - app didn't log any copies")
    for i in result["images"]:
        if i["status"] != "ok":
            print(f"      X {i['detail']}  [{i['hash'][:12]}...]")
    if result["extra"]:
        print(f"      (note: {len(result['extra'])} other image(s) copied not in baseline)")


def main():
    # Windows consoles default to cp1252; keep output encodable regardless.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser()
    p.add_argument("test_name", nargs="?", help="e.g. test_02_keyword_search")
    p.add_argument("--all", action="store_true",
                   help="check every test that has baseline clipboard images")
    p.add_argument("--run-dir", default=str(_DEFAULT_RUN),
                   help="parent dir holding per-test run folders")
    p.add_argument("--baseline-dir", default=str(_BASELINE),
                   help="parent dir holding approved baselines")
    p.add_argument("--skip", action="append", default=[], metavar="TEST",
                   help="test to exclude from --all (repeatable). Use for copies "
                        "that can't be reproduced in CI, e.g. the model-generated "
                        "emoji-story image.")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = p.parse_args()

    run_dir  = Path(args.run_dir)
    base_dir = Path(args.baseline_dir)

    skipped = []
    if args.all:
        tests = []
        for d in sorted(base_dir.iterdir()):
            if not (d.is_dir() and any(d.glob("*_clipboard.png"))):
                continue
            (skipped if d.name in args.skip else tests).append(d.name)
    elif args.test_name:
        tests = [args.test_name]
    else:
        p.error("provide a test name or --all")

    results = [check_test(t, run_dir, base_dir) for t in tests]

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("\n  Clipboard image-copy verification (by content hash)")
        print("  " + "-" * 56)
        for r in results:
            _print_report(r)
        total = sum(r["expected"] for r in results)
        ok    = sum(1 for r in results for i in r["images"] if i["status"] == "ok")
        print("  " + "-" * 56)
        print(f"  {ok}/{total} distinct expected image(s) copied across {len(results)} test(s)")
        if skipped:
            print(f"  skipped (not reproducible in CI): {', '.join(skipped)}")
        print()

    failed = [r for r in results if r["expected"] and not r["passed"]]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
