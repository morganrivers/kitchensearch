#!/usr/bin/env python3
"""
Build jina-clip-v1 image AND text embeddings for all 147k emoji kitchen combos.
Both encoders share the same 768-dim space, so queries can score against both.

Usage:
  embed-all-emojikitchen-clip.py              # full build / resume
  embed-all-emojikitchen-clip.py --reset      # delete and rebuild
  embed-all-emojikitchen-clip.py --limit 1000 # embed first N for testing
"""

import sys
import os
import hashlib
import tempfile
import urllib.request
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    print("Run: pip install onnxruntime Pillow numpy fastembed", file=sys.stderr)
    raise SystemExit(1)

_REPO          = Path(__file__).resolve().parent
DATA_DIR       = _REPO / "data" / "embeddings"
UI_ASSETS_DIR  = _REPO / "data" / "ui_assets"
CACHE_DIR      = _REPO / "data" / "cache"
THUMB_DIR      = CACHE_DIR / "thumbs"
SEARCH_INDEX   = UI_ASSETS_DIR / "search-index.tsv"
IMG_EMBEDDINGS = DATA_DIR / "nomic-image-embeddings.npy"
TXT_EMBEDDINGS = DATA_DIR / "nomic-text-embeddings.npy"
NOMIC_URLS     = UI_ASSETS_DIR / "nomic-urls.txt"
NOMIC_ALTS     = UI_ASSETS_DIR / "nomic-alts.txt"

BATCH = 32


def thumb_path(url):
    return THUMB_DIR / (hashlib.md5(url.encode()).hexdigest() + ".png")


def fetch_image(url):
    """Return (path, is_temp) or (None, False) on failure."""
    cached = thumb_path(url)
    if cached.exists():
        try:
            Image.open(cached)
            return str(cached), False
        except Exception:
            cached.unlink(missing_ok=True)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    try:
        urllib.request.urlretrieve(url, tmp.name)
        Image.open(tmp.name)
        return tmp.name, True
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return None, False


def load_existing():
    if not all(f.exists() for f in (IMG_EMBEDDINGS, TXT_EMBEDDINGS, NOMIC_URLS, NOMIC_ALTS)):
        return set(), [], [], [], []
    done_urls = set(NOMIC_URLS.read_text().splitlines())
    img_embs  = np.load(IMG_EMBEDDINGS)
    txt_embs  = np.load(TXT_EMBEDDINGS)
    urls      = NOMIC_URLS.read_text().splitlines()
    alts      = NOMIC_ALTS.read_text().splitlines()
    return done_urls, img_embs, txt_embs, urls, alts


def save(all_img, all_txt, all_urls, all_alts):
    img_tmp  = IMG_EMBEDDINGS.with_suffix(".npy.tmp")
    txt_tmp  = TXT_EMBEDDINGS.with_suffix(".npy.tmp")
    urls_tmp = NOMIC_URLS.with_suffix(".tmp")
    alts_tmp = NOMIC_ALTS.with_suffix(".tmp")
    np.save(img_tmp,  np.vstack(all_img).astype(np.float16))
    np.save(txt_tmp,  np.vstack(all_txt).astype(np.float16))
    urls_tmp.write_text("\n".join(all_urls))
    alts_tmp.write_text("\n".join(all_alts))
    img_tmp.replace(IMG_EMBEDDINGS)
    txt_tmp.replace(TXT_EMBEDDINGS)
    urls_tmp.replace(NOMIC_URLS)
    alts_tmp.replace(NOMIC_ALTS)


def main():
    reset = "--reset" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    if reset:
        for f in (IMG_EMBEDDINGS, TXT_EMBEDDINGS, NOMIC_URLS, NOMIC_ALTS):
            f.unlink(missing_ok=True)
        print("Reset done.")

    if not SEARCH_INDEX.exists():
        print("search-index.tsv not found.", file=sys.stderr)
        raise SystemExit(1)

    rows = [line.rstrip("\n").split("\t", 2)
            for line in SEARCH_INDEX.read_text().splitlines()]
    all_entries = [(r[0], r[1], r[2]) for r in rows if len(r) == 3]
    if limit:
        all_entries = all_entries[:limit]
    total = len(all_entries)

    done_urls, existing_img, existing_txt, done_url_list, done_alt_list = load_existing()
    todo = [(u, a, t) for u, a, t in all_entries if u not in done_urls]

    print(f"Total: {total:,}  Already done: {len(done_urls):,}  Remaining: {len(todo):,}")
    if not todo:
        print("Nothing to do.")
        return

    print("Loading models...", flush=True)
    import _nomic_vision, _nomic_text
    if not _nomic_vision.is_cached():
        _nomic_vision.download()
    img_model = _nomic_vision.load()
    if not _nomic_text.is_cached():
        _nomic_text.download()
    txt_model = _nomic_text.load()

    THUMB_DIR.mkdir(parents=True, exist_ok=True)

    all_img_buf = [existing_img] if len(done_urls) else []
    all_txt_buf = [existing_txt] if len(done_urls) else []
    all_url_buf = list(done_url_list)
    all_alt_buf = list(done_alt_list)

    processed = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]

        paths, texts, chunk_urls, chunk_alts, tmps = [], [], [], [], []
        for url, alt, text in chunk:
            path, is_temp = fetch_image(url)
            if path:
                paths.append(path)
                texts.append(text)
                chunk_urls.append(url)
                chunk_alts.append(alt)
                if is_temp:
                    tmps.append(path)

        if paths:
            img_vecs = img_model.embed(paths)
            txt_vecs = txt_model.embed(texts)
            img_vecs /= np.maximum(np.linalg.norm(img_vecs, axis=1, keepdims=True), 1e-8)
            txt_vecs /= np.maximum(np.linalg.norm(txt_vecs, axis=1, keepdims=True), 1e-8)
            all_img_buf.append(img_vecs)
            all_txt_buf.append(txt_vecs)
            all_url_buf.extend(chunk_urls)
            all_alt_buf.extend(chunk_alts)

        for tmp in tmps:
            try:
                os.unlink(tmp)
            except OSError:
                pass

        processed += len(chunk)
        done_total = len(done_urls) + processed
        pct = done_total / total * 100
        print(f"  {done_total:>7,}/{total:,}  ({pct:.1f}%)", end="\r", flush=True)

        if processed % 4096 < BATCH:
            print(f"\n  checkpoint at {done_total:,}...", flush=True)
            save(all_img_buf, all_txt_buf, all_url_buf, all_alt_buf)

    print(flush=True)
    save(all_img_buf, all_txt_buf, all_url_buf, all_alt_buf)
    final = np.load(IMG_EMBEDDINGS)
    print(f"Done. {final.shape[0]:,} embeddings saved  "
          f"(image {IMG_EMBEDDINGS.stat().st_size // 1_000_000} MB, "
          f"text {TXT_EMBEDDINGS.stat().st_size // 1_000_000} MB)")


if __name__ == "__main__":
    main()
