#!/usr/bin/env python3
"""
Build jina-clip-v1 text embeddings for the ~619 base emojis that participate
in emoji kitchen combinations.  Embeds each emoji's name as text using
jinaai/jina-clip-v1, which lives in the same 768-dim space as the combo
image/text embeddings used by the search daemon.

Outputs to data/embeddings/:
  base-emoji-codes.txt   one code per line, e.g. u1faa9
  base-emoji-names.txt   one name per line, e.g. mirror_ball
  base-emoji-jina.npy    jina text embeddings  (N, 768) float16

Usage:
  python3 build-base-emoji-embeddings.py
  python3 build-base-emoji-embeddings.py --force   # rebuild even if exists
"""

import re
import sys
from pathlib import Path

import numpy as np

_REPO        = Path(__file__).resolve().parent
DATA_DIR     = _REPO / "data" / "embeddings"
SEARCH_INDEX = DATA_DIR / "search-index.tsv"
CODES_FILE   = DATA_DIR / "base-emoji-codes.txt"
NAMES_FILE   = DATA_DIR / "base-emoji-names.txt"
NOMIC_FILE   = DATA_DIR / "base-emoji-nomic.npy"


def extract_base_emojis():
    code_to_name = {}
    with open(SEARCH_INDEX) as f:
        for line in f:
            parts = line.rstrip("\n").split("\t", 2)
            if len(parts) < 2:
                continue
            url, alt = parts[0], parts[1]
            m = re.search(r'/([^/]+)/([^/]+)_([^/]+)\.png$', url)
            if not m:
                continue
            code1, code2 = m.group(2), m.group(3)
            if "-" not in alt:
                continue
            name1, name2 = alt.split("-", 1)
            if code1 not in code_to_name:
                code_to_name[code1] = name1
            if code2 not in code_to_name:
                code_to_name[code2] = name2
    return code_to_name


def main():
    force = "--force" in sys.argv

    if not SEARCH_INDEX.exists():
        print("search-index.tsv not found.")
        sys.exit(1)

    if NOMIC_FILE.exists() and not force:
        print("Base emoji embeddings already exist. Pass --force to rebuild.")
        sys.exit(0)

    print("Extracting base emojis from search index...", flush=True)
    code_to_name = extract_base_emojis()
    codes = sorted(code_to_name.keys())
    names = [code_to_name[c] for c in codes]
    print(f"Found {len(codes)} base emojis.", flush=True)

    print("Building nomic-embed-text-v1 embeddings from names...", flush=True)
    import _nomic_text
    if not _nomic_text.is_cached():
        _nomic_text.download()
    model = _nomic_text.load()
    vecs = model.embed(names)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs /= np.maximum(norms, 1e-8)

    CODES_FILE.write_text("\n".join(codes))
    NAMES_FILE.write_text("\n".join(names))
    np.save(NOMIC_FILE, vecs.astype(np.float16))
    print(f"Saved embeddings for {len(codes)} base emojis → {NOMIC_FILE.name}", flush=True)


if __name__ == "__main__":
    main()
