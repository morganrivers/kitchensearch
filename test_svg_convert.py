import random
import subprocess
import tarfile
import urllib.request
import os
import time
import vtracer
from pathlib import Path

OUT_DIR = Path("/tmp/emoji_svg_test")
OUT_DIR.mkdir(exist_ok=True)

# Load URLs from asset bundle
with tarfile.open("data/app_assets.tar.gz") as tar:
    f = tar.extractfile("data/ui_assets/urls.txt")
    urls = [line.decode().strip() for line in f if line.strip()]

sample = random.sample(urls, 100)

results = {"ok": 0, "fail": 0, "errors": []}

for i, url in enumerate(sample):
    name = url.split("/")[-1].replace(".png", "")
    png_path = str(OUT_DIR / f"{name}.png")
    svg_path = str(OUT_DIR / f"{name}.svg")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "emojikitchen-picker"})
        with urllib.request.urlopen(req, timeout=10) as resp, open(png_path, "wb") as fout:
            fout.write(resp.read())
    except Exception as e:
        results["fail"] += 1
        results["errors"].append(f"download {url}: {e}")
        continue

    try:
        vtracer.convert_image_to_svg_py(
            png_path,
            svg_path,
            colormode="color",
            hierarchical="stacked",
            mode="spline",
            filter_speckle=4,
            color_precision=6,
            layer_difference=16,
            corner_threshold=60,
            length_threshold=4.0,
            max_iterations=10,
            splice_threshold=45,
            path_precision=3,
        )
        png_kb = os.path.getsize(png_path) / 1024
        svg_kb = os.path.getsize(svg_path) / 1024
        results["ok"] += 1
        if i % 10 == 0:
            print(f"[{i+1}/100] {name}: PNG {png_kb:.1f}KB → SVG {svg_kb:.1f}KB")
    except Exception as e:
        results["fail"] += 1
        results["errors"].append(f"convert {name}: {e}")

print(f"\nDone: {results['ok']} ok, {results['fail']} failed")
if results["errors"]:
    print("Errors:")
    for e in results["errors"][:10]:
        print(" ", e)

# Size summary
pngs = list(OUT_DIR.glob("*.png"))
svgs = list(OUT_DIR.glob("*.svg"))
if pngs and svgs:
    avg_png = sum(f.stat().st_size for f in pngs) / len(pngs) / 1024
    avg_svg = sum(f.stat().st_size for f in svgs) / len(svgs) / 1024
    print(f"\nAverage PNG size: {avg_png:.1f} KB")
    print(f"Average SVG size: {avg_svg:.1f} KB")
    print(f"Size ratio: {avg_svg/avg_png:.1f}x")
    total_svg_147k_gb = avg_svg * 147_000 / 1024 / 1024
    print(f"\nProjected total for 147k SVGs: {total_svg_147k_gb:.1f} GB")
