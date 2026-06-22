#!/usr/bin/env python3
"""Cross-platform *consistency* gate.

The real question this answers is **not** "does this run pixel-match the
committed PNGs?" (it won't — fonts/anti-aliasing/DPI differ per OS, and the
baseline may be stale). It is:

    "Did this OS navigate the app through the *same screens* as the
     Linux gold standard, and copy the *same* images?"

Linux is the reference: it's the environment the app is developed and
eyeballed in (the GIFs are reviewed by hand), so its captured run is the
source of truth. Windows and macOS must reproduce it.

What is compared (per test), reference vs candidate:

  1. Screen count           — exact. Same number of screenshots taken.
  2. Duplicate structure    — exact. Wherever the reference has a screen that
                              is identical to its predecessor (a step that
                              changed nothing on screen), the candidate must
                              repeat that same pattern. Catches navigation
                              drift even when counts happen to match.
  3. Transition magnitudes  — within +/- X% (relative) of the reference. For
                              each step, how much the screen changed from the
                              previous one. This is OS-tolerant: it never
                              compares a Windows pixel to a Linux pixel, only
                              Windows' own step-to-step delta against Linux'
                              own step-to-step delta. A step that should move
                              ~3% of pixels must not suddenly move 80%.
  4. Copied images          — exact. The set of images the app copied to the
                              clipboard (by content hash) must be identical;
                              these *are* byte-identical across OSes by design.

Exit code:
    0  candidate is consistent with the reference for every test
    1  any test diverged (or a test folder is missing on one side)

Because the committed baseline is expected to be stale right now, this gate
is expected to stay RED until a good Linux set is approved. That is fine: the
signal we care about is that Windows, macOS and Linux diverge *identically*.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops
import numpy as np

_TESTS_DIR = Path(__file__).parent
_NON_SHOT_SUFFIXES = ("_clipboard.png",)
_SCRATCH_PREFIXES = ("_stable_",)

# Matches compare.py: a per-channel delta this large counts as a changed pixel,
# and a frame with less than _MIN_PCT changed is treated as unchanged noise.
_THRESHOLD = 8
_MIN_PCT = 0.05


def _shots(test_dir: Path) -> list[Path]:
    """Ordered list of screen PNGs (excludes clipboard dumps + scratch frames)."""
    out = []
    for p in sorted(test_dir.glob("*.png")):
        if any(p.name.endswith(s) for s in _NON_SHOT_SUFFIXES):
            continue
        if any(p.name.startswith(s) for s in _SCRATCH_PREFIXES):
            continue
        out.append(p)
    return out


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _copied_hashes(test_dir: Path) -> set[str]:
    """Content hashes of every image the app copied to the clipboard."""
    return {_sha256(p.read_bytes()) for p in test_dir.glob("*_clipboard.png")}


def _pct(a: Path, b: Path) -> float:
    """Percentage of pixels that differ between two screenshots (0-100).

    Mirrors compare.compare_images' magnitude, but tkinter-free so the gate
    can run as a pure image check.
    """
    base = Image.open(a).convert("RGB")
    curr = Image.open(b).convert("RGB")
    if base.size != curr.size:
        curr = curr.resize(base.size, Image.LANCZOS)
    diff = np.asarray(ImageChops.difference(base, curr), dtype=np.uint8)
    mask = np.any(diff > _THRESHOLD, axis=2)
    return float(mask.mean() * 100)


def _dup_pattern(shots: list[Path], threshold: float) -> list[bool]:
    """For each shot after the first: is it identical (< threshold%) to the
    previous one?  This is the test's 'duplicate structure'."""
    pattern = []
    for i in range(1, len(shots)):
        pattern.append(_pct(shots[i - 1], shots[i]) < threshold)
    return pattern


def _transitions(shots: list[Path]) -> list[float]:
    """Per-step change magnitude (% pixels changed from the previous shot)."""
    return [_pct(shots[i - 1], shots[i]) for i in range(1, len(shots))]


def _within(cand: float, ref: float, tol: float, abs_floor: float) -> bool:
    """cand within +/- tol (relative) of ref, with an absolute floor so that
    near-zero reference transitions don't make the band degenerate."""
    band = max(abs_floor, abs(ref) * tol)
    return abs(cand - ref) <= band


def check_test(name: str, ref_root: Path, cand_root: Path,
               tol: float, abs_floor: float, dup_threshold: float) -> dict:
    ref_dir = ref_root / name
    cand_dir = cand_root / name
    issues: list[str] = []

    ref = _shots(ref_dir)
    cand = _shots(cand_dir)

    if not ref:
        issues.append(f"reference has no screenshots ({ref_dir})")
    if not cand:
        issues.append(f"candidate has no screenshots ({cand_dir})")

    count_ok = len(ref) == len(cand)
    if not count_ok:
        issues.append(f"screen count differs: reference {len(ref)} vs candidate {len(cand)}")

    dup_ok = True
    trans_ok = True
    n_trans_off = 0
    if count_ok and ref:
        ref_dup = _dup_pattern(ref, dup_threshold)
        cand_dup = _dup_pattern(cand, dup_threshold)
        if ref_dup != cand_dup:
            dup_ok = False
            mismatch = [i + 1 for i, (a, b) in enumerate(zip(ref_dup, cand_dup)) if a != b]
            issues.append(f"duplicate structure differs at step index(es): {mismatch}")

        ref_tr = _transitions(ref)
        cand_tr = _transitions(cand)
        for i, (rp, cp) in enumerate(zip(ref_tr, cand_tr)):
            if not _within(cp, rp, tol, abs_floor):
                trans_ok = False
                n_trans_off += 1
                if n_trans_off <= 5:
                    issues.append(
                        f"transition {ref[i].name}->{ref[i+1].name}: "
                        f"reference {rp:.2f}% vs candidate {cp:.2f}% (outside +/-{tol*100:.0f}%)")
        if n_trans_off > 5:
            issues.append(f"... and {n_trans_off - 5} more transition(s) out of band")

    ref_copied = _copied_hashes(ref_dir)
    cand_copied = _copied_hashes(cand_dir)
    copies_ok = ref_copied == cand_copied
    if not copies_ok:
        missing = sorted(h[:12] for h in ref_copied - cand_copied)
        extra = sorted(h[:12] for h in cand_copied - ref_copied)
        if missing:
            issues.append(f"copied images missing vs reference: {missing}")
        if extra:
            issues.append(f"copied images not in reference: {extra}")

    passed = count_ok and dup_ok and trans_ok and copies_ok and bool(ref) and bool(cand)
    return {
        "test": name,
        "passed": passed,
        "ref_screens": len(ref),
        "cand_screens": len(cand),
        "count_ok": count_ok,
        "dup_ok": dup_ok,
        "transitions_ok": trans_ok,
        "copies_ok": copies_ok,
        "issues": issues,
    }


def _print_report(results: list[dict], tol: float) -> None:
    print("\n  Cross-platform consistency vs reference (Linux gold standard)")
    print("  " + "-" * 60)
    for r in results:
        verdict = "PASS" if r["passed"] else "FAIL"
        print(f"  [{r['test']}] {verdict}  "
              f"(screens ref={r['ref_screens']} cand={r['cand_screens']})")
        for issue in r["issues"]:
            print(f"      X {issue}")
    n_ok = sum(1 for r in results if r["passed"])
    print("  " + "-" * 60)
    print(f"  {n_ok}/{len(results)} test(s) consistent with reference "
          f"(transition tolerance +/-{tol*100:.0f}%)")
    print()


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="Cross-platform consistency gate")
    p.add_argument("--reference", default=str(_TESTS_DIR / "baseline_approved"),
                   help="reference run tree (the Linux gold standard)")
    p.add_argument("--candidate", default=str(_TESTS_DIR / "test_run"),
                   help="candidate run tree (this OS's results)")
    p.add_argument("--skip", action="append", default=[], metavar="TEST",
                   help="test to exclude (repeatable); e.g. model-generated story")
    p.add_argument("--tol", type=float, default=0.5,
                   help="relative tolerance for transition magnitudes (0.5 = +/-50%%)")
    p.add_argument("--abs-floor", type=float, default=1.0,
                   help="absolute floor (percentage points) for the transition band")
    p.add_argument("--dup-threshold", type=float, default=_MIN_PCT,
                   help="below this %% diff, two screens count as duplicates")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = p.parse_args()

    ref_root = Path(args.reference)
    cand_root = Path(args.candidate)

    tests = sorted(
        d.name for d in ref_root.iterdir()
        if d.is_dir() and d.name not in args.skip and any(d.glob("*.png"))
    )

    results = [check_test(t, ref_root, cand_root, args.tol, args.abs_floor,
                          args.dup_threshold) for t in tests]

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        _print_report(results, args.tol)

    sys.exit(0 if all(r["passed"] for r in results) else 1)


if __name__ == "__main__":
    main()
