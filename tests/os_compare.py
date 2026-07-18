#!/usr/bin/env python3
"""Cross-OS side-by-side of the images each platform actually copied.

Downloads the newest Linux / macOS / Windows smoke-test artifacts with ``gh``
and builds a single self-contained HTML page that places each copied clipboard
image next to its counterpart on the other OSes, matched by step. Lets you
eyeball whether a platform diverged.

Informational only - it never gates CI. Pass/fail is the render-health gate
(tests/health_check.py). There is deliberately no diff score here.

One command (needs the GitHub CLI, authenticated for this repo):

    python tests/os_compare.py            # build tests/os_compare_report/index.html
    python tests/os_compare.py --open     # ...and open it in a browser

Point at specific runs instead of the newest with --linux/--mac/--windows <id>.
"""
import argparse
import base64
import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image

_TESTS_DIR = Path(__file__).parent
_WORKFLOWS = {"linux": "Linux tests", "mac": "macOS tests", "windows": "Windows tests"}
_OS_ORDER = ["linux", "mac", "windows"]
_STEP_RE = re.compile(r"(test_\d+_[a-z0-9]+)", re.I)
_THUMB = 150


def _gh_json(*args):
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"gh failed: {' '.join(args)}\n{out.stderr.strip()}")
    return json.loads(out.stdout)


def _latest_run_id(workflow):
    runs = _gh_json("run", "list", "--workflow", workflow, "--limit", "1",
                    "--json", "databaseId,headSha,conclusion,createdAt")
    return runs[0] if runs else None


def _download(run_id, dest):
    dest.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["gh", "run", "download", str(run_id), "--dir", str(dest),
                        "--pattern", "*gif*"], capture_output=True, text=True)
    # A run with no matching artifacts exits non-zero; treat as "nothing to show".
    if r.returncode != 0 and "no artifact" not in (r.stderr or "").lower():
        print(f"  warning: download for run {run_id}: {r.stderr.strip()}", file=sys.stderr)


_STEP_NUM_RE = re.compile(r"^(\d+)_")


def _final_clip(folder):
    """The last image a run copied in one test folder, across OS conventions.

    Linux writes per-step ``NN_Name_clipboard.png``; the final copy is the
    highest-numbered one. mac/Windows write a single ``clipboard.png``. A test
    that copied nothing has neither.
    """
    numbered = []
    for p in folder.glob("*_clipboard.png"):
        m = _STEP_NUM_RE.match(p.name)
        if m:
            numbered.append((int(m.group(1)), p))
    if numbered:
        return max(numbered, key=lambda t: t[0])[1]
    bare = folder / "clipboard.png"
    return bare if bare.exists() else None


def _index(os_dir):
    """{test_name: final_clipboard_png} — the image each test finally copied."""
    idx = {}
    for folder in sorted(os_dir.rglob("*gif-test_*")):
        if not folder.is_dir():
            continue
        m = _STEP_RE.search(folder.name)
        if not m:
            continue
        clip = _final_clip(folder)
        if clip:
            idx[m.group(1)] = clip
    return idx


def _thumb_data_uri(path):
    im = Image.open(path).convert("RGBA")
    im.thumbnail((_THUMB, _THUMB))
    # flatten onto a checker-ish neutral so transparent art is visible
    bg = Image.new("RGBA", im.size, (240, 240, 240, 255))
    bg.alpha_composite(im)
    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    short = hashlib.sha256(path.read_bytes()).hexdigest()[:6]
    return b64, short


def _cell(per_os, os_key, test):
    path = per_os.get(os_key, {}).get(test)
    if not path:
        return '<td class="missing">—</td>'
    b64, short = _thumb_data_uri(path)
    return (f'<td><img src="data:image/png;base64,{b64}">'
            f'<div class="h">{short}</div></td>')


def _build_html(per_os, meta):
    rows = ['<table><tr><th>test</th>'
            + "".join(f"<th>{o}</th>" for o in _OS_ORDER) + "</tr>"]
    tests = sorted({t for idx in per_os.values() for t in idx})
    for test in tests:
        cells = "".join(_cell(per_os, o, test) for o in _OS_ORDER)
        rows.append(f'<tr><td class="step">{test}</td>{cells}</tr>')
    rows.append("</table>")
    body = "".join(rows) if tests else \
        '<p>No clipboard images found in the downloaded artifacts.</p>'

    hdr = "".join(
        f"<li><b>{o}</b>: run {meta[o]['databaseId']} "
        f"({meta[o].get('conclusion') or '?'}, {meta[o].get('createdAt','')[:10]})</li>"
        if meta.get(o) else f"<li><b>{o}</b>: no run found</li>"
        for o in _OS_ORDER)

    return f"""<!doctype html><meta charset="utf-8"><title>Cross-OS clipboard compare</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:24px;background:#fafafa;color:#222}}
 table{{border-collapse:collapse;margin:8px 0 24px}}
 th,td{{border:1px solid #ccc;padding:6px;text-align:center;vertical-align:top}}
 td.step{{font-family:monospace;font-size:12px;text-align:left;max-width:180px}}
 td.missing{{color:#c00;background:#fdf0f0;font-size:20px}}
 img{{display:block}} .h{{font:11px monospace;color:#666;margin-top:2px}}
 h2{{margin-top:28px}}
</style>
<h1>Cross-OS clipboard compare</h1>
<p>The final image each OS actually copied per test. The 6-char tag is the
image content hash: same tag = byte-identical across OSes. This page is
informational and does not affect pass/fail.</p>
<ul>{hdr}</ul>
{body}
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(_TESTS_DIR / "os_compare_report"))
    p.add_argument("--open", action="store_true", help="open the report when done")
    for o in _OS_ORDER:
        p.add_argument(f"--{o}", metavar="RUN_ID", help=f"use a specific {o} run id")
    args = p.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    meta, per_os = {}, {}
    for o in _OS_ORDER:
        override = getattr(args, o)
        run = {"databaseId": override} if override else _latest_run_id(_WORKFLOWS[o])
        meta[o] = run
        if not run:
            print(f"  {o}: no run found")
            per_os[o] = {}
            continue
        dest = out / "artifacts" / o
        print(f"  {o}: downloading artifacts for run {run['databaseId']} …")
        _download(run["databaseId"], dest)
        per_os[o] = _index(dest)
        print(f"  {o}: {len(per_os[o])} test(s) with a copied image")

    report = out / "index.html"
    report.write_text(_build_html(per_os, meta), encoding="utf-8")
    print(f"\n  Report: {report}")
    if args.open:
        import webbrowser
        webbrowser.open(report.resolve().as_uri())


if __name__ == "__main__":
    main()
