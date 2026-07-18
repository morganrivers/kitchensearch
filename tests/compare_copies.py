#!/usr/bin/env python3
"""Side-by-side compare of the images each OS copied to the clipboard.

Offline companion to ``tests/os_compare.py``. Instead of pulling artifacts with
the GitHub CLI, this points straight at directories of PNGs you already have on
disk — unzipped CI artifacts, or a local ``tests/test_run`` — and builds one
self-contained HTML page that places each OS's copied image next to the others,
matched by test (and optionally by step).

Why a separate tool: ``os_compare.py`` needs ``gh`` + network + auth. This one
needs nothing but the PNGs, so it works on a laptop with three downloaded
artifact zips, or in an environment without the GitHub CLI.

Each OS directory is searched recursively for per-test run folders, using the
same clipboard conventions the drivers write:

  * Linux    writes per-step  ``NN_Name_clipboard.png``
  * mac/Win  write a single   ``clipboard.png``

Usage:
    # three unzipped artifact trees (each holds gif-test_*/ folders):
    python tests/compare_copies.py --linux ./linux --mac ./mac --windows ./win

    # or one parent dir that has linux/ mac/ windows/ subfolders:
    python tests/compare_copies.py ./artifacts

    # show every copied step, not just the final image per test:
    python tests/compare_copies.py ./artifacts --all-steps --open

The 6-char tag under each image is its content hash: identical tags mean the
copied PNGs are byte-for-byte identical across those OSes; a highlighted row
means at least one OS diverged or copied nothing. Informational only — it never
gates CI (that is ``tests/health_check.py``).
"""
import argparse
import base64
import hashlib
import io
import re
import sys
from pathlib import Path

from PIL import Image

_TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TESTS_DIR))

from run_common import force_utf8_stdio  # noqa: E402

_OS_ORDER = ["linux", "mac", "windows"]
# lowercase substrings that identify an OS subfolder when a single root is given
_OS_ALIASES = {
    "linux":   ("linux", "ubuntu"),
    "mac":     ("mac", "macos", "darwin", "osx"),
    "windows": ("windows", "win"),
}
_STEP_RE = re.compile(r"(test_\d+_[a-z0-9_]+)", re.I)
_STEP_NUM_RE = re.compile(r"^(\d+)_")
_DEFAULT_THUMB = 360


def _clip_images(folder: Path):
    """Copied clipboard PNGs in one test run folder, ordered by step.

    Returns ``[(step_label, path), ...]``. Linux writes numbered per-step files
    (``NN_Name_clipboard.png``); mac/Windows write a single ``clipboard.png``.
    A test that copied nothing returns an empty list.
    """
    numbered = []
    for p in folder.glob("*_clipboard.png"):
        m = _STEP_NUM_RE.match(p.name)
        step = int(m.group(1)) if m else 0
        label = p.name[:-len("_clipboard.png")] or p.stem
        numbered.append((step, label, p))
    numbered.sort(key=lambda t: t[0])
    ordered = [(label, p) for _, label, p in numbered]
    bare = folder / "clipboard.png"
    if bare.exists():
        ordered.append(("final", bare))
    return ordered


def _index(os_dir: Path, all_steps: bool):
    """{test_name: [(step_label, path), ...]} for one OS directory.

    Searches recursively so it works on an unzipped artifact tree
    (``gif-test_*/``) or a raw ``tests/test_run`` (``test_*/``). In final-only
    mode each test keeps just its last copied image.
    """
    idx: dict[str, list] = {}
    candidates = [os_dir, *(d for d in os_dir.rglob("*") if d.is_dir())]
    for folder in sorted(set(candidates)):
        m = _STEP_RE.search(folder.name)
        if not m:
            continue
        clips = _clip_images(folder)
        if not clips:
            continue
        chosen = clips if all_steps else clips[-1:]
        idx.setdefault(m.group(1), []).extend(chosen)
    return idx


def _thumb(path: Path, size: int):
    """(data_uri_b64, content_hash6, 'WxH') for a transparent-aware thumbnail."""
    im = Image.open(path).convert("RGBA")
    dims = f"{im.width}x{im.height}"
    im.thumbnail((size, size))
    bg = Image.new("RGBA", im.size, (240, 240, 240, 255))
    bg.alpha_composite(im)
    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    short = hashlib.sha256(path.read_bytes()).hexdigest()[:6]
    return b64, short, dims


def _resolve_os_dirs(args):
    """Map each OS to a directory from explicit flags or a single root."""
    dirs = {}
    for o in _OS_ORDER:
        val = getattr(args, o)
        if val:
            dirs[o] = Path(val)
    if args.root and not dirs:
        root = Path(args.root)
        for o in _OS_ORDER:
            for child in sorted(p for p in root.iterdir() if p.is_dir()):
                if any(a in child.name.lower() for a in _OS_ALIASES[o]):
                    dirs[o] = child
                    break
    return dirs


def _render_cell(entries, size, all_steps):
    if not entries:
        return '<td class="missing">— copied nothing</td>', set()
    hashes = set()
    blocks = []
    for label, path in entries:
        b64, short, dims = _thumb(path, size)
        hashes.add(short)
        cap = f"{label} · {short} · {dims}" if all_steps else f"{short} · {dims}"
        blocks.append(
            f'<figure><a href="data:image/png;base64,{b64}" target="_blank">'
            f'<img src="data:image/png;base64,{b64}"></a>'
            f'<figcaption>{cap}</figcaption></figure>')
    return f'<td>{"".join(blocks)}</td>', hashes


def _build_html(per_os, dirs, size, all_steps):
    active = [o for o in _OS_ORDER if o in dirs]
    tests = sorted({t for o in active for t in per_os[o]})

    rows = ['<table><thead><tr><th>test</th>'
            + "".join(f"<th>{o}</th>" for o in active) + "</tr></thead><tbody>"]
    for test in tests:
        cells, final_hashes = [], set()
        for o in active:
            entries = per_os[o].get(test, [])
            html, hashes = _render_cell(entries, size, all_steps)
            cells.append(html)
            # divergence check uses the final image per OS (last entry)
            if entries:
                fb, fh, _ = _thumb(entries[-1][1], size)
                final_hashes.add(fh)
        present = sum(1 for o in active if per_os[o].get(test))
        differs = len(final_hashes) > 1 or present < len(active)
        cls = ' class="diff"' if differs else ""
        flag = " ⚠" if differs else " ✓"
        rows.append(f'<tr{cls}><td class="step">{test}{flag}</td>{"".join(cells)}</tr>')
    rows.append("</tbody></table>")
    body = "".join(rows) if tests else \
        '<p class="empty">No clipboard PNGs found in the given directories.</p>'

    srcs = "".join(f"<li><b>{o}</b>: <code>{dirs[o]}</code> "
                   f"({len(per_os[o])} test(s) with a copied image)</li>"
                   for o in active)
    mode = "every copied step" if all_steps else "final copied image per test"

    return f"""<!doctype html><meta charset="utf-8">
<title>Cross-OS clipboard compare</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:24px;background:#fafafa;color:#222}}
 h1{{margin:0 0 4px}}
 table{{border-collapse:collapse;margin:16px 0}}
 th,td{{border:1px solid #ccc;padding:8px;text-align:center;vertical-align:top}}
 thead th{{background:#eef;position:sticky;top:0}}
 td.step{{font-family:monospace;font-size:13px;text-align:left;white-space:nowrap}}
 td.missing{{color:#c00;background:#fdf0f0}}
 tr.diff td.step{{background:#fff6e0}}
 figure{{margin:0 0 10px}}
 figure:last-child{{margin-bottom:0}}
 img{{display:block;margin:auto;max-width:{size}px;border:1px solid #ddd;background:#f0f0f0}}
 figcaption{{font:11px monospace;color:#666;margin-top:3px}}
 code{{background:#eee;padding:1px 4px;border-radius:3px}}
 .empty{{color:#888}}
</style>
<h1>Cross-OS clipboard compare</h1>
<p>The {mode}, side by side. The 6-char tag is the PNG content hash: matching
tags = byte-identical across OSes. A ⚠ row means an OS diverged or copied
nothing. Click any image for full size. Informational only — does not gate CI.</p>
<ul>{srcs}</ul>
{body}
"""


def main():
    force_utf8_stdio()
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("root", nargs="?",
                   help="parent dir holding linux/ mac/ windows/ subfolders")
    for o in _OS_ORDER:
        p.add_argument(f"--{o}", metavar="DIR", help=f"directory of {o} PNGs")
    p.add_argument("--out", default=str(_TESTS_DIR / "copy_compare_report" / "index.html"),
                   help="output HTML path")
    p.add_argument("--all-steps", action="store_true",
                   help="show every copied image, not just the final one per test")
    p.add_argument("--thumb", type=int, default=_DEFAULT_THUMB,
                   metavar="PX", help=f"max thumbnail edge (default {_DEFAULT_THUMB})")
    p.add_argument("--open", action="store_true", help="open the report when done")
    args = p.parse_args()

    dirs = _resolve_os_dirs(args)
    if not dirs:
        p.error("give at least one of --linux/--mac/--windows, or a root dir "
                "containing linux/ mac/ windows/ subfolders")

    missing = [f"{o} ({d})" for o, d in dirs.items() if not Path(d).is_dir()]
    if missing:
        sys.exit("not a directory: " + ", ".join(missing))

    per_os = {}
    for o, d in dirs.items():
        per_os[o] = _index(Path(d), args.all_steps)
        print(f"  {o}: {len(per_os[o])} test(s) with a copied image  ({d})")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_build_html(per_os, dirs, args.thumb, args.all_steps),
                   encoding="utf-8")
    print(f"\n  Report: {out}")
    if args.open:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
