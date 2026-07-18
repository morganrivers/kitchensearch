#!/usr/bin/env python3
"""Side-by-side compare of the images each OS copied to the clipboard.

Offline companion to ``tests/os_compare.py``. Instead of pulling artifacts with
the GitHub CLI, this points straight at directories of PNGs you already have on
disk — unzipped CI artifacts, or a local ``tests/test_run`` — and builds one
self-contained, multi-page HTML report: a contact-sheet overview page followed
by one page per test placing each OS's copied image next to the others, matched
by test (and optionally by step). The pages carry print page-breaks, so the
browser's "Save as PDF" yields a single multi-page PDF.

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
_OVERVIEW_THUMB = 140   # small contact-sheet thumbnails on the summary page


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


def _hash6(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:6]


def _figures(entries, size, all_steps):
    """Inner HTML (stacked <figure>s) + the set of content hashes for one OS."""
    if not entries:
        return '<div class="missing">— copied nothing</div>', set()
    hashes, blocks = set(), []
    for label, path in entries:
        b64, short, dims = _thumb(path, size)
        hashes.add(short)
        cap = f"{label} · {short} · {dims}" if all_steps else f"{short} · {dims}"
        blocks.append(
            f'<figure><a href="data:image/png;base64,{b64}" target="_blank">'
            f'<img src="data:image/png;base64,{b64}"></a>'
            f'<figcaption>{cap}</figcaption></figure>')
    return "".join(blocks), hashes


def _diverges(per_os, active, test):
    """(differs, n_present) — final copied image per OS compared across OSes."""
    final_hashes, present = set(), 0
    for o in active:
        entries = per_os[o].get(test, [])
        if entries:
            present += 1
            final_hashes.add(_hash6(entries[-1][1]))
    return (len(final_hashes) > 1 or present < len(active)), present


def _overview_table(per_os, active, tests, all_steps):
    """Page 1: compact contact-sheet of every test, small thumbnails."""
    rows = ['<table><thead><tr><th>test</th>'
            + "".join(f"<th>{o}</th>" for o in active) + "</tr></thead><tbody>"]
    for test in tests:
        cells = []
        for o in active:
            inner, _ = _figures(per_os[o].get(test, []), _OVERVIEW_THUMB, all_steps)
            cls = ' class="missing"' if not per_os[o].get(test) else ""
            cells.append(f"<td{cls}>{inner}</td>")
        differs, _ = _diverges(per_os, active, test)
        cls = ' class="diff"' if differs else ""
        flag = "⚠" if differs else "✓"
        anchor = f'<a href="#{test}">{test}</a>'
        rows.append(f'<tr{cls}><td class="step">{flag} {anchor}</td>{"".join(cells)}</tr>')
    rows.append("</tbody></table>")
    return "".join(rows)


def _detail_page(per_os, active, test, size, all_steps):
    """One printed page per test: larger side-by-side columns + a verdict banner."""
    differs, present = _diverges(per_os, active, test)
    if present == 0:
        banner = '<span class="badge warn">no OS copied anything</span>'
    elif differs:
        banner = '<span class="badge warn">⚠ diverges across OSes</span>'
    else:
        banner = '<span class="badge ok">✓ identical across OSes</span>'
    cols = []
    for o in active:
        inner, _ = _figures(per_os[o].get(test, []), size, all_steps)
        cols.append(f'<div class="col"><div class="os">{o}</div>{inner}</div>')
    return (f'<section class="page" id="{test}">'
            f'<h2>{test} {banner}</h2>'
            f'<div class="cols">{"".join(cols)}</div></section>')


def _build_html(per_os, dirs, size, all_steps):
    active = [o for o in _OS_ORDER if o in dirs]
    tests = sorted({t for o in active for t in per_os[o]})

    srcs = "".join(f"<li><b>{o}</b>: <code>{dirs[o]}</code> "
                   f"({len(per_os[o])} test(s) with a copied image)</li>"
                   for o in active)
    mode = "every copied step" if all_steps else "final copied image per test"

    if not tests:
        pages = '<p class="empty">No clipboard PNGs found in the given directories.</p>'
    else:
        pages = (f'<section class="page">'
                 f'<h1>Cross-OS clipboard compare</h1>'
                 f'<p>The {mode}, side by side. The 6-char tag is the PNG content '
                 f'hash: matching tags = byte-identical across OSes. A ⚠ marks a '
                 f'test where an OS diverged or copied nothing. Each test also has '
                 f'its own page below (click a name to jump). Click any image for '
                 f'full size. Informational only — does not gate CI.</p>'
                 f'<ul>{srcs}</ul>'
                 f'{_overview_table(per_os, active, tests, all_steps)}'
                 f'</section>')
        pages += "".join(_detail_page(per_os, active, t, size, all_steps)
                         for t in tests)

    return f"""<!doctype html><meta charset="utf-8">
<title>Cross-OS clipboard compare</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:0;background:#eceef1;color:#222}}
 h1{{margin:0 0 4px}} h2{{margin:0 0 14px;font-size:18px}}
 .page{{background:#fff;max-width:1000px;margin:20px auto;padding:24px 28px;
        border:1px solid #dcdfe3;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
 table{{border-collapse:collapse;margin:16px 0;width:100%}}
 th,td{{border:1px solid #ccc;padding:8px;text-align:center;vertical-align:top}}
 thead th{{background:#eef}}
 td.step{{font-family:monospace;font-size:13px;text-align:left;white-space:nowrap}}
 td.missing,.missing{{color:#c00;background:#fdf0f0}}
 tr.diff td.step{{background:#fff6e0}}
 .cols{{display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start}}
 .col{{flex:1;min-width:200px;text-align:center}}
 .os{{font-weight:600;margin-bottom:8px;padding-bottom:4px;border-bottom:2px solid #eef}}
 figure{{margin:0 0 12px}} figure:last-child{{margin-bottom:0}}
 img{{display:block;margin:auto;max-width:{size}px;border:1px solid #ddd;background:#f0f0f0}}
 figcaption{{font:11px monospace;color:#666;margin-top:3px}}
 code{{background:#eee;padding:1px 4px;border-radius:3px}}
 a{{color:#245}} .empty{{color:#888;padding:24px}}
 .badge{{font:600 12px system-ui;padding:2px 8px;border-radius:10px;vertical-align:middle}}
 .badge.ok{{background:#e6f6e6;color:#1a7f1a}} .badge.warn{{background:#fdeccf;color:#8a5a00}}
 @page{{margin:14mm}}
 @media print{{
   body{{background:#fff}}
   .page{{break-after:page;box-shadow:none;border:none;margin:0;max-width:none}}
   .page:last-child{{break-after:auto}}
   thead th{{background:#eef !important;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
 }}
</style>
{pages}
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
