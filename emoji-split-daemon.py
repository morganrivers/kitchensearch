#!/usr/bin/env python3
"""
Split-query search daemon using jinaai/jina-clip-v1 (text encoder only at runtime).
Combo images and their keyword texts are pre-embedded in the same 768-dim space
and compressed to 128 dims via shared PCA.  Both are scored against each query
and fused with RRF so results reflect both visual and textual similarity.

  1 word  - scores base emojis, returns squared combo (emoji-emoji) first
  2 words - cross-ranks all combos by combined base-emoji similarity
  3+ words - image RRF + text RRF combined search only

Protocol: newline-terminated JSON each direction.
  Request:  {"query": "...", "limit": 5000}
  Response: [{"alt": "...", "url": "...", "rank": 0}, ...]
"""

import os
import json
import re
import signal
import socket
import sys
import threading
from pathlib import Path

import numpy as np
import _nomic_text

_REPO        = Path(__file__).resolve().parent
if not (_REPO / "data").exists():
    _REPO = Path(sys.executable).resolve().parent
DATA_DIR      = _REPO / "data" / "embeddings"
UI_ASSETS_DIR = _REPO / "data" / "ui_assets"
CACHE_DIR     = _REPO / "data" / "cache"
SOCK_PATH     = CACHE_DIR / "split-daemon.sock"
STATUS_PATH   = CACHE_DIR / "split-daemon-loading.json"
SEARCH_INDEX  = UI_ASSETS_DIR / "search-index.tsv"

BASE_CODES     = UI_ASSETS_DIR / "base-emoji-codes.txt"
BASE_NAMES     = UI_ASSETS_DIR / "base-emoji-names.txt"
BASE_NOMIC     = DATA_DIR / "base-emoji-nomic.npy"
IMG_EMBEDDINGS = DATA_DIR / "nomic-image-pca128.npy"
TXT_EMBEDDINGS = DATA_DIR / "nomic-text-pca128.npy"
PCA_MATRIX     = DATA_DIR / "nomic-pca128-matrix.npy"
PCA_MEAN       = DATA_DIR / "nomic-pca128-mean.npy"
NOMIC_URLS     = UI_ASSETS_DIR / "nomic-urls.txt"
NOMIC_ALTS     = UI_ASSETS_DIR / "nomic-alts.txt"

IDLE_TIMEOUT = 600


def _write_status(step, pct):
    try:
        tmp = STATUS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"step": step, "pct": round(float(pct), 1)}))
        tmp.replace(STATUS_PATH)
    except Exception:
        pass


def load():
    required = [BASE_CODES, BASE_NAMES, BASE_NOMIC,
                IMG_EMBEDDINGS, TXT_EMBEDDINGS, PCA_MATRIX, PCA_MEAN,
                NOMIC_URLS, NOMIC_ALTS]
    for f in required:
        if not f.exists():
            print(f"Missing {f.name} - download data.tar.gz from releases.", flush=True)
            sys.exit(1)

    if _nomic_text.is_cached():
        _write_status("Loading nomic-embed-text model...", 5)
    else:
        _nomic_text.download(status_cb=_write_status, pct_start=5, pct_end=40)
        _write_status("Loading nomic-embed-text model...", 40)
    model = _nomic_text.load()

    _write_status("Warming up model...", 42)
    model.embed(["warmup"])

    _write_status("Loading base emoji data...", 50)
    base_codes  = BASE_CODES.read_text().splitlines()
    base_nomic  = np.load(BASE_NOMIC).astype(np.float32)
    code_to_idx = {c: i for i, c in enumerate(base_codes)}

    _write_status("Loading search index...", 58)
    combo_map = {}
    with open(SEARCH_INDEX) as f:
        for line in f:
            parts = line.rstrip("\n").split("\t", 2)
            if len(parts) < 2:
                continue
            url, alt = parts[0], parts[1]
            m = re.search(r'/([^/]+)/([^/]+)_([^/]+)\.png$', url)
            if not m:
                continue
            c1, c2 = m.group(2), m.group(3)
            combo_map[(c1, c2)] = (url, alt)

    _write_status("Loading embeddings...", 70)
    img_emb     = np.load(IMG_EMBEDDINGS).astype(np.float32)
    txt_emb     = np.load(TXT_EMBEDDINGS).astype(np.float32)
    pca_matrix  = np.load(PCA_MATRIX).astype(np.float32)
    pca_mean    = np.load(PCA_MEAN).astype(np.float32)
    nomic_urls  = NOMIC_URLS.read_text().splitlines()
    nomic_alts  = NOMIC_ALTS.read_text().splitlines()

    _write_status("Ready", 100)
    print(f"Ready - {len(base_codes)} base emojis, {len(combo_map):,} combos, "
          f"{len(nomic_urls):,} embedded.", flush=True)

    return (model, base_nomic, code_to_idx, combo_map,
            img_emb, txt_emb, pca_matrix, pca_mean,
            nomic_alts, nomic_urls)


def rank_base(query_word, model, base_nomic):
    q = model.embed([query_word], query=True)[0]
    return (base_nomic @ q).argsort()[::-1].argsort()


def search_one(word, model, base_nomic, code_to_idx, combo_map):
    ranks = rank_base(word, model, base_nomic)
    idx_to_code = {v: k for k, v in code_to_idx.items()}
    best_code = idx_to_code.get(int(ranks.argmin()))
    if best_code and (best_code, best_code) in combo_map:
        url, alt = combo_map[(best_code, best_code)]
        return [(0, alt, url)]
    return []


def search_two(word1, word2, model, base_nomic, code_to_idx, combo_map):
    ranks_w1 = rank_base(word1, model, base_nomic)
    ranks_w2 = rank_base(word2, model, base_nomic)
    scored = []
    for (c1, c2), (url, alt) in combo_map.items():
        i = code_to_idx.get(c1)
        j = code_to_idx.get(c2)
        if i is None or j is None:
            continue
        score = min(
            int(ranks_w1[i]) + int(ranks_w2[j]),
            int(ranks_w1[j]) + int(ranks_w2[i]),
        )
        scored.append((score, alt, url))
    scored.sort(key=lambda x: x[0])
    return scored


def search_combined(query, model, img_emb, txt_emb, pca_matrix, pca_mean,
                    nomic_alts, nomic_urls):
    q = model.embed([query], query=True)[0]
    q_pca = (q - pca_mean) @ pca_matrix
    q_pca /= max(np.linalg.norm(q_pca), 1e-8)

    txt_scores = txt_emb @ q_pca
    img_scores = img_emb @ q_pca
    combined   = txt_scores + 2.0 * img_scores
    top_idx    = combined.argsort()[::-1]
    return [(float(combined[i]), nomic_alts[i], nomic_urls[i]) for i in top_idx]


def handle(conn, model, base_nomic, code_to_idx, combo_map,
           img_emb, txt_emb, pca_matrix, pca_mean, nomic_alts, nomic_urls):
    try:
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            if data.endswith(b"\n"):
                break
        req   = json.loads(data.decode())
        query = req["query"]
        limit = req.get("limit", 5000)
        words = query.strip().split()

        decomposed = []
        if len(words) == 1:
            decomposed = search_one(words[0], model, base_nomic, code_to_idx, combo_map)
        elif len(words) == 2:
            decomposed = search_two(words[0], words[1], model, base_nomic,
                                    code_to_idx, combo_map)

        fallback = search_combined(query, model, img_emb, txt_emb,
                                   pca_matrix, pca_mean, nomic_alts, nomic_urls)

        seen   = {url for _, _, url in decomposed}
        merged = decomposed + [(r, a, u) for r, a, u in fallback if u not in seen]

        results = [{"alt": a, "url": u, "rank": r} for r, a, u in merged[:limit]]
        conn.sendall((json.dumps(results) + "\n").encode())
    except Exception as e:
        try:
            conn.sendall((json.dumps({"error": str(e)}) + "\n").encode())
        except Exception:
            pass
    finally:
        conn.close()


def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if SOCK_PATH.exists():
        SOCK_PATH.unlink()
    if STATUS_PATH.exists():
        STATUS_PATH.unlink()

    (model, base_nomic, code_to_idx, combo_map,
     img_emb, txt_emb, pca_matrix, pca_mean,
     nomic_alts, nomic_urls) = load()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCK_PATH))
    server.listen(8)
    server.settimeout(IDLE_TIMEOUT)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"Listening on {SOCK_PATH}", flush=True)

    try:
        while True:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                print("Idle timeout - exiting.", flush=True)
                break
            threading.Thread(
                target=handle,
                args=(conn, model, base_nomic, code_to_idx, combo_map,
                      img_emb, txt_emb, pca_matrix, pca_mean,
                      nomic_alts, nomic_urls),
                daemon=True,
            ).start()
    finally:
        server.close()
        if SOCK_PATH.exists():
            SOCK_PATH.unlink()
        if STATUS_PATH.exists():
            STATUS_PATH.unlink()


if __name__ == "__main__":
    main()
