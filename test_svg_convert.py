"""
Test SVG conversion of cached emoji PNGs.

Usage (from repo root):
    pip install vtracer
    python3 test_svg_convert.py

Reads PNGs from ~/.local/share/kitchensearch/data/cache/thumbs/
Writes SVGs to /tmp/emoji_svg_test/
"""
import gzip
import os
import random
import tarfile
import urllib.request
from pathlib import Path

import vtracer

CACHE_DIR = Path.home() / ".local/share/kitchensearch/data/cache/thumbs"
OUT_DIR = Path("/tmp/emoji_svg_test")
OUT_DIR.mkdir(exist_ok=True)

PRESETS = {
    "medium": dict(filter_speckle=8,  color_precision=4, layer_difference=32, length_threshold=6.0,  path_precision=2),
    "low":    dict(filter_speckle=16, color_precision=3, layer_difference=48, length_threshold=10.0, path_precision=1),
    "tiny":   dict(filter_speckle=32, color_precision=2, layer_difference=64, length_threshold=15.0, path_precision=1),
}

def convert(png_path: Path, svg_path: Path, params: dict):
    vtracer.convert_image_to_svg_py(
        str(png_path), str(svg_path),
        colormode="color", hierarchical="stacked", mode="spline",
        corner_threshold=60, max_iterations=10, splice_threshold=45,
        **params,
    )

def gzip_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), compresslevel=9))

def main():
    pngs = list(CACHE_DIR.glob("*.png"))
    if not pngs:
        print(f"No cached PNGs found in {CACHE_DIR}")
        print("Run the app first to populate the cache, then re-run this script.")
        return

    sample = random.sample(pngs, min(len(pngs), 200))
    print(f"Converting {len(sample)} PNGs from cache...\n")

    stats = {label: {"raw": [], "gz": []} for label in PRESETS}
    png_raw, png_gz = [], []

    for i, png in enumerate(sample):
        png_raw.append(png.stat().st_size)
        png_gz.append(gzip_size(png))

        for label, params in PRESETS.items():
            svg = OUT_DIR / f"{png.stem}_{label}.svg"
            convert(png, svg, params)
            stats[label]["raw"].append(svg.stat().st_size)
            stats[label]["gz"].append(gzip_size(svg))

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(sample)} done...")

    n = len(sample)
    print(f"\n{'Format':<18} {'Avg raw':>10} {'Avg gz':>10} {'Proj 147k gz':>14}")
    print("-" * 56)
    avg_png_raw = sum(png_raw) / n
    avg_png_gz  = sum(png_gz) / n
    proj_png    = avg_png_gz * 147_000 / 1024**3
    print(f"{'PNG':<18} {avg_png_raw/1024:>9.1f}KB {avg_png_gz/1024:>9.1f}KB {proj_png:>13.2f}GB")

    for label, s in stats.items():
        avg_raw = sum(s["raw"]) / n
        avg_gz  = sum(s["gz"]) / n
        proj    = avg_gz * 147_000 / 1024**3
        print(f"{'SVG ' + label:<18} {avg_raw/1024:>9.1f}KB {avg_gz/1024:>9.1f}KB {proj:>13.2f}GB")

    print(f"\nSVGs written to {OUT_DIR}/ — open a few to check quality.")

if __name__ == "__main__":
    main()
