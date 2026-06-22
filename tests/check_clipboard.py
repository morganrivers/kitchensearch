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

The baseline is recorded on Linux, so it doubles as the cross-OS reference:
"matches the baseline" means "byte-identical to what Linux produced". Combo
images gate the build; generated images (the emoji story) are *reported* for
consistency against that Linux baseline but never fail it — see
``_REPORT_ONLY_STEPS``.

Exit code:
    0  every gating image was copied (or the test copies no gating images)
    1  a gating image was never copied, or no copy log was produced
       (a report-only image diverging across OSes does NOT set this)
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
# Total copy-event count per test from the Linux reference run (Linux is the
# gold standard). Screenshot + distinct-image counts come from the baseline
# artifacts; only the raw copy-event total isn't recoverable, so it's recorded.
_GOLD_CAPTURES = _BASELINE / "gold-capture-counts.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _count_screenshots(d: Path) -> int:
    """Per-step screenshot frames in a run/baseline dir (the GIF frames):
    files named like ``07_Down.png`` but not the ``*_clipboard.png`` captures."""
    if not d.exists():
        return 0
    return sum(1 for p in d.glob("[0-9]*.png") if not p.name.endswith("_clipboard.png"))


def _gold_capture_counts() -> dict:
    try:
        data = json.loads(_GOLD_CAPTURES.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return {}


# Steps whose copied image is *reported* for cross-OS consistency but is NOT
# part of the pass/fail gate. The clipboard baseline here is recorded on Linux,
# so "matches the baseline" == "byte-identical to what Linux produced". The
# downloaded combo images must match (they gate the build); the emoji-story
# image is generated locally (a PIL composite) and is reported instead — we
# surface whether each OS reproduced the Linux bytes and flag the ones that
# didn't, but a story divergence (e.g. a font / FreeType difference) does not
# fail the build. (test_16_story is skipped wholesale at the call site because
# that whole test is just the story image.)
_REPORT_ONLY_STEPS: dict[str, frozenset[str]] = {
    "test_30_manyresults": frozenset({"43_Return", "44_step", "45_click_223_250"}),
}


def _expected_images(base: Path) -> dict[str, list[str]]:
    """{image_hash: [baseline step names...]} for every image in the baseline."""
    exp: dict[str, list[str]] = {}
    for clip in sorted(base.glob("*_clipboard.png")):
        step = clip.name[: -len("_clipboard.png")]
        exp.setdefault(_sha256(clip.read_bytes()), []).append(step)
    return exp


def _is_report_only(test_name: str, steps: list[str]) -> bool:
    """True if every baseline step for this image is report-only (e.g. story)."""
    ro = _REPORT_ONLY_STEPS.get(test_name, frozenset())
    return bool(ro) and all(s in ro for s in steps)


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
        images.append({"hash": h, "steps": steps,
                       "status": "ok" if h in copied_set else "missing",
                       "report_only": _is_report_only(test_name, steps)})

    # Images the app copied that the baseline never recorded — informational.
    # (For a report-only image that diverged, this is the OS's own render.)
    extra = sorted(copied_set - set(expected))

    gating  = [i for i in images if not i["report_only"]]
    gate_ok = sum(1 for i in gating if i["status"] == "ok")
    passed  = all(i["status"] == "ok" for i in gating)
    # If we expected gating copies but got no log at all, that's a hard failure.
    if gating and not log_present:
        passed = False

    # ── capture-count gate ────────────────────────────────────────────────
    # Every OS must reach the end of the test capturing the SAME number of
    # screenshots and copy events as the gold standard (Linux), with the same
    # number of duplicate copies. A shortfall means the OS couldn't complete
    # the run the same way (the readiness/callback sync failed, or it got stuck
    # repeating one image) — so it fails the build.
    gold_shots   = _count_screenshots(base)
    run_shots    = _count_screenshots(run)
    gold         = _gold_capture_counts().get(test_name) or {}
    gold_copies  = gold.get("copy_events")
    gold_distinct = gold.get("distinct_copies")    # distinct copies in the gold run
    run_distinct  = len(copied_set)
    # duplicates = copies that repeated an already-copied image (computed from
    # the gold run's own counts, not the baseline's distinct-image set, since a
    # run may legitimately copy images that aren't in the baseline).
    gold_dups = (gold_copies - gold_distinct) if (
        gold_copies is not None and gold_distinct is not None) else None
    cap = {
        "gold_screenshots": gold_shots,
        "run_screenshots":  run_shots,
        "screenshots_ok":   (gold_shots == 0) or (run_shots == gold_shots),
        "gold_copies":      gold_copies,
        "run_copies":       len(copied),
        "copies_ok":        (gold_copies is None) or (len(copied) == gold_copies),
        "gold_duplicates":  gold_dups,
        "run_duplicates":   len(copied) - run_distinct,
        "duplicates_ok":    (gold_dups is None)
                             or ((len(copied) - run_distinct) == gold_dups),
    }
    if not (cap["screenshots_ok"] and cap["copies_ok"] and cap["duplicates_ok"]):
        passed = False

    return {"test": test_name,
            "expected": len(gating),               # # of gating (pass-condition) images
            "gate_ok": gate_ok,
            "report_only": [i for i in images if i["report_only"]],
            "copied_total": len(copied),
            "extra": extra,
            "captures": cap,
            "log_present": log_present,
            "run_exists": run.exists(),
            "passed": passed,
            "images": images}


def _print_report(result: dict) -> None:
    name, n = result["test"], result["expected"]
    ro = result["report_only"]
    if n == 0 and not ro:
        print(f"  [{name}] no image-copy steps - nothing to verify")
        return
    verdict = "PASS" if result["passed"] else "FAIL"
    print(f"  [{name}] {verdict}  ({result['gate_ok']}/{n} gating image(s) copied; "
          f"{result['copied_total']} copy event(s) logged)")
    if not result["log_present"] and n:
        print(f"      ! no {_COPY_LOG} produced - app didn't log any copies")
    for i in result["images"]:
        if not i["report_only"] and i["status"] != "ok":
            print(f"      X expected image (baseline step(s) {', '.join(i['steps'])}) "
                  f"was never copied  [{i['hash'][:12]}...]")
    # Capture-count gate: same # screenshots + copy events + duplicates as the
    # gold standard, or this OS didn't reach the end the same way.
    cap = result.get("captures", {})
    if not cap.get("screenshots_ok", True):
        print(f"      X screenshots: captured {cap['run_screenshots']}, "
              f"gold standard {cap['gold_screenshots']} (run did not reach the end)")
    if not cap.get("copies_ok", True):
        print(f"      X copy events: {cap['run_copies']} copied, "
              f"gold standard {cap['gold_copies']} (an image capture was missed)")
    if not cap.get("duplicates_ok", True):
        print(f"      X duplicate copies: {cap['run_duplicates']}, "
              f"gold standard {cap['gold_duplicates']} (got stuck on / skipped a repeat)")
    # Cross-OS consistency (reported, not gating): does THIS OS reproduce the
    # Linux-recorded baseline bytes for the generated images?
    for i in ro:
        steps = ', '.join(i["steps"])
        if i["status"] == "ok":
            print(f"      consistency OK    {steps}: matches Linux baseline [{i['hash'][:12]}...]")
        else:
            mine = ', '.join(h[:12] for h in result["extra"]) or "nothing"
            print(f"      consistency DIFFERS {steps}: Linux baseline [{i['hash'][:12]}...] "
                  f"not reproduced here (this OS copied: {mine})")
    if result["extra"] and not ro:
        print(f"      (note: {len(result['extra'])} other image(s) copied not in baseline: "
              f"{', '.join(h[:12] for h in result['extra'])})")


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
        ok    = sum(r["gate_ok"] for r in results)
        ro_all = [i for r in results for i in r["report_only"]]
        ro_ok  = sum(1 for i in ro_all if i["status"] == "ok")
        cap_fail = [r for r in results
                    if not (r["captures"]["screenshots_ok"]
                            and r["captures"]["copies_ok"]
                            and r["captures"]["duplicates_ok"])]
        print("  " + "-" * 56)
        print(f"  {ok}/{total} gating image(s) matched the (Linux) baseline across {len(results)} test(s)")
        cap_checked = len(results) - len(cap_fail)
        print(f"  capture counts (screenshots + copies + duplicates vs gold standard): "
              f"{cap_checked}/{len(results)} test(s) matched")
        if ro_all:
            print(f"  cross-OS consistency (reported, not gating): "
                  f"{ro_ok}/{len(ro_all)} generated image(s) reproduced the Linux baseline here")
        if skipped:
            print(f"  skipped (not reproducible in CI): {', '.join(skipped)}")
        print()

    failed = [r for r in results if r["expected"] and not r["passed"]]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
