"""Shared helpers for the per-OS test drivers (linux_run / mac_run / win_run)
and the render-health gate (health_check).

Single source of truth for two things every OS needs to agree on:

* ``image_health`` — decide whether a captured PNG is a real render or a broken
  one (all-black, solid-fill, or fully transparent). This is what lets the gate
  pass on harmless cross-OS pixel differences while still catching an OS that
  renders nothing.
* ``write_status`` / ``read_status`` — the driver records whether the run
  raised, so the gate can fail on a bug that surfaced during the test without
  the workflow having to drop ``continue-on-error`` on every step.
"""
import json
import math
import sys
from pathlib import Path

from PIL import Image

STATUS_FILE = "status.json"


def force_utf8_stdio() -> None:
    """Make stdout/stderr encode UTF-8 regardless of the console code page.

    The drivers print non-ASCII (e.g. the ``→`` in progress lines). On a
    Windows CI console (cp1252) an un-reconfigured stream raises
    UnicodeEncodeError mid-run, which the driver then mis-reports as a generic
    test failure. Every entry point that prints should call this first.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

# Luminance standard deviation below which an image is treated as uniform (a
# solid fill: black screen, white screen, blank). Real content sits at ~40-50;
# every broken synthetic case measures 0.0, so the margin is large. Screenshots
# use a stricter floor because a sparse-but-valid UI state can be fairly flat.
_STD_MIN = {"screenshot": 2.0, "clipboard": 5.0}
# A copied combo is transparent-background art; if almost nothing is opaque the
# clipboard held no real image.
_OPAQUE_MIN = 0.005


def _luminance_std(rgba: Image.Image) -> float:
    bg = Image.new("RGBA", rgba.size, (128, 128, 128, 255))
    bg.alpha_composite(rgba)
    hist = bg.convert("L").histogram()
    n = sum(hist)
    assert n, "empty image"
    mean = sum(i * c for i, c in enumerate(hist)) / n
    var = sum(((i - mean) ** 2) * c for i, c in enumerate(hist)) / n
    return math.sqrt(var)


def _opaque_fraction(rgba: Image.Image) -> float:
    alpha = rgba.getchannel("A").histogram()
    return sum(alpha[16:]) / (rgba.width * rgba.height)


def image_health(path, kind: str) -> tuple[bool, str]:
    """(is_healthy, reason) for one captured PNG.

    ``kind`` is "screenshot" (opaque window capture) or "clipboard" (a copied
    combo image, transparent background).
    """
    assert kind in _STD_MIN, f"unknown image kind: {kind}"
    rgba = Image.open(path).convert("RGBA")
    if kind == "clipboard":
        frac = _opaque_fraction(rgba)
        if frac < _OPAQUE_MIN:
            return False, f"blank/transparent (opaque {frac:.3%})"
    std = _luminance_std(rgba)
    if std < _STD_MIN[kind]:
        return False, f"uniform/solid fill (luminance stddev {std:.2f})"
    return True, f"ok (luminance stddev {std:.2f})"


def write_status(out_dir, failed: bool, stderr: str = "") -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / STATUS_FILE).write_text(
        json.dumps({"failed": bool(failed), "stderr": stderr or ""}),
        encoding="utf-8")


def read_status(out_dir):
    """Returns the driver's recorded status dict, or None if it wrote none."""
    p = Path(out_dir) / STATUS_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
