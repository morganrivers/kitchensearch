"""
Image + widget-geometry comparison utilities for the test harness.

compare_runs(baseline_dir, run_dir, diff_dir)
    → dict of {screenshot_name: {"status": "ok"|"changed"|"new"|"missing",
                                  "pct": float,
                                  "widget_diff": [str, ...]}}
"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageChops
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from widget_dump import diff_dumps


_THRESHOLD = 8       # per-channel difference to count as changed
_MIN_PCT   = 0.05    # below this → treated as "ok" (rounding / anti-alias noise)


def _compare_clipboard(baseline_dir: Path, run_dir: Path, name: str) -> str | None:
    """Return a human-readable diff string if clipboard content changed, else None."""
    for ext in ("png", "txt"):
        bpath = baseline_dir / f"{name}_clipboard.{ext}"
        cpath = run_dir      / f"{name}_clipboard.{ext}"
        if not bpath.exists() and not cpath.exists():
            continue
        if bpath.exists() and not cpath.exists():
            return f"clipboard: was {ext}, now empty"
        if not bpath.exists() and cpath.exists():
            return f"clipboard: new {ext} content"
        if ext == "txt":
            b, c = bpath.read_text(errors="replace"), cpath.read_text(errors="replace")
            if b != c:
                return f"clipboard text: {b.strip()!r} → {c.strip()!r}"
        else:
            # Image comparison
            try:
                _, pct = compare_images(bpath, cpath)
                if pct > _MIN_PCT:
                    return f"clipboard image changed {pct:.1f}%"
            except Exception:
                pass
    return None


def _compare_widget_dumps(baseline_json: Path, current_json: Path) -> list[str]:
    """Return diff lines between two widget-dump JSON files, or [] if either is absent."""
    if not baseline_json.exists() or not current_json.exists():
        return []
    try:
        b = json.loads(baseline_json.read_text(encoding="utf-8"))
        c = json.loads(current_json.read_text(encoding="utf-8"))
        return diff_dumps(b, c)
    except Exception:
        return []


def compare_images(
    baseline_path: Path, current_path: Path
) -> tuple[Image.Image, float]:
    """
    Returns (diff_image, pct_changed).
    diff_image is the current image with changed pixels highlighted in red.
    """
    base = Image.open(baseline_path).convert("RGB")
    curr = Image.open(current_path).convert("RGB")

    if base.size != curr.size:
        curr = curr.resize(base.size, Image.LANCZOS)

    diff = ImageChops.difference(base, curr)
    arr  = np.asarray(diff, dtype=np.uint8)
    mask = np.any(arr > _THRESHOLD, axis=2)   # (H, W) bool
    pct  = float(mask.mean() * 100)

    # Build overlay: dim current, highlight changed pixels red
    curr_arr  = np.asarray(curr, dtype=np.uint8).copy()
    # Darken unchanged areas slightly
    unchanged = ~mask
    curr_arr[unchanged] = (curr_arr[unchanged] * 0.55).astype(np.uint8)
    # Paint changed pixels bright red
    curr_arr[mask] = [220, 30, 30]

    diff_img = Image.fromarray(curr_arr, "RGB")
    return diff_img, pct


def compare_runs(
    baseline_dir: str | Path,
    run_dir:      str | Path,
    diff_dir:     str | Path,
) -> dict[str, dict]:
    baseline_dir = Path(baseline_dir)
    run_dir      = Path(run_dir)
    diff_dir     = Path(diff_dir)
    diff_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict] = {}

    for run_shot in sorted(f for f in run_dir.glob("*.png")):
        name          = run_shot.stem
        baseline_shot = baseline_dir / run_shot.name

        if not baseline_shot.exists():
            results[name] = {"status": "new", "pct": 100.0,
                             "run": str(run_shot), "baseline": None, "diff": None,
                             "widget_diff": [], "clipboard_diff": None}
            continue

        diff_img, pct = compare_images(baseline_shot, run_shot)
        diff_path     = diff_dir / run_shot.name

        widget_diff    = _compare_widget_dumps(
            baseline_dir / f"{name}.json", run_dir / f"{name}.json"
        )
        clipboard_diff = _compare_clipboard(baseline_dir, run_dir, name)

        if pct > _MIN_PCT:
            diff_img.save(diff_path)
            results[name] = {
                "status":        "changed",
                "pct":           round(pct, 2),
                "run":           str(run_shot),
                "baseline":      str(baseline_shot),
                "diff":          str(diff_path),
                "widget_diff":   widget_diff,
                "clipboard_diff": clipboard_diff,
            }
        else:
            # Clipboard-only change still counts as changed
            status = "changed" if clipboard_diff else "ok"
            results[name] = {
                "status":         status,
                "pct":            round(pct, 2),
                "run":            str(run_shot),
                "baseline":       str(baseline_shot),
                "diff":           None,
                "widget_diff":    widget_diff,
                "clipboard_diff": clipboard_diff,
            }

    # ── also save widget-diff text files ──────────────────────────────────────
    for name, r in results.items():
        wdiff = r.get("widget_diff")
        if wdiff:
            (diff_dir / f"{name}_widget.diff").write_text("\n".join(wdiff), encoding="utf-8")

    # Shots in baseline but missing from run
    if baseline_dir.exists():
        for baseline_shot in baseline_dir.glob("*.png"):
            name = baseline_shot.stem
            if name not in results:
                results[name] = {
                    "status":   "missing",
                    "pct":      100.0,
                    "run":      None,
                    "baseline": str(baseline_shot),
                    "diff":     None,
                }

    return results


def print_summary(results: dict[str, dict]):
    changed = [n for n, r in results.items() if r["status"] != "ok"]
    total   = len(results)
    print(f"\n{'─'*60}")
    print(f"  {total} screenshots  |  {len(changed)} changed  |  {total - len(changed)} ok")
    print(f"{'─'*60}")
    for name, r in sorted(results.items()):
        icon = {"ok": "✓", "changed": "≠", "new": "+", "missing": "✗"}.get(r["status"], "?")
        print(f"  {icon}  {name:<40}  {r['pct']:5.1f}%  [{r['status']}]")
        if r["status"] != "ok":
            if r.get("clipboard_diff"):
                print(f"       clipboard: {r['clipboard_diff']}")
            for line in r.get("widget_diff", [])[:6]:
                print(f"       {line}")
    print(f"{'─'*60}\n")
