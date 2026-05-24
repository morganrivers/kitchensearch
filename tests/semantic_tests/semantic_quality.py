#!/usr/bin/env python3
"""
Semantic search quality benchmark.

Tests the current MiniLM pipeline against hand-crafted queries.

Usage:
  python3 tests/semantic_tests/semantic_quality.py           # run and print
  python3 tests/semantic_tests/semantic_quality.py --save    # save results to results/
"""

import sys
import json
import argparse
import datetime
import numpy as np
from pathlib import Path

REPO         = Path(__file__).resolve().parent.parent.parent
DATA         = REPO / "data"
EMB_DIR      = DATA / "embeddings"
ASSETS       = DATA / "ui_assets"
SEARCH_INDEX = ASSETS / "search-index.tsv"

# ── data paths ───────────────────────────────────────────────────────────────

# MiniLM (current pipeline)
SEM_PCA      = EMB_DIR / "minilm-pca340.npy"
SEM_PCA_MAT  = EMB_DIR / "minilm-pca340-matrix.npy"
SEM_PCA_MEAN = EMB_DIR / "minilm-pca340-mean.npy"

# ── test cases ───────────────────────────────────────────────────────────────

# Descriptive/conceptual queries where only image understanding helps.
# None of these phrases appear literally in the keyword text.
# Metric: rank of the known-correct target (lower = better).
# Summary: mean rank across all cases.

# (query, target_alt)
RANK_TESTS = [
    ("a house rising out of the mountains",  "sunrise_over_mountains-house"),
    ("dancing cow",                          "ballet_shoes-cow"),
    ("laptop hurricane",                     "tornado-computer"),
    ("raining coffee",                       "rain_cloud-coffee"),
    ("checklist",                            "white_check_mark-spades"),
    ("life in the slow lane",                "motorway-snail"),
    ("i do love chocolate chips",            "goat-cookie"),
    ("fantastic boat trip",                  "comet-canoe"),
]

# ── helpers ───────────────────────────────────────────────────────────────────

def pca_project(vec, mat, mean):
    v = (vec - mean) @ mat
    n = np.linalg.norm(v)
    return v / max(n, 1e-8)

def score_and_rank(emb, q_vec):
    scores = emb @ q_vec
    return scores.argsort()[::-1]

def rank_of(ranked_alts, target):
    for i, a in enumerate(ranked_alts):
        if a == target:
            return i + 1  # 1-indexed
    return None

# ── index loaders ─────────────────────────────────────────────────────────────

def load_minilm():
    sys.path.insert(0, str(REPO))
    import _minilm_text
    print("Loading MiniLM ONNX model...", flush=True)
    model = _minilm_text.load()
    model.embed(["warmup"])
    rows = [l.rstrip("\n").split("\t", 2) for l in SEARCH_INDEX.read_text().splitlines()]
    alts = [r[1] for r in rows if len(r) == 3]
    emb  = np.load(SEM_PCA).astype(np.float32)
    mat  = np.load(SEM_PCA_MAT).astype(np.float32)
    mean = np.load(SEM_PCA_MEAN).astype(np.float32)
    n = min(len(alts), len(emb))
    return model, emb[:n], mat, mean, alts[:n]

def embed_query_minilm(model, query, mat, mean):
    q = model.embed([query])[0].astype(np.float32)
    return pca_project(q, mat, mean)

# ── main ─────────────────────────────────────────────────────────────────────

def run_model(name, embed_fn, emb, alts):
    ranks = []
    for query, target in RANK_TESTS:
        q = embed_fn(query)
        order = score_and_rank(emb, q)
        ranked_alts = [alts[i] for i in order]
        r = rank_of(ranked_alts, target)
        top3 = ", ".join(ranked_alts[:3])
        r_str = str(r) if r else "NOT FOUND"
        print(f"  {query!r:45s}  -> {target}: rank={r_str:>6}  | top3: {top3}")
        ranks.append(r if r else len(alts))
    mean = sum(ranks) / len(ranks)
    print(f"  {'MEAN RANK':45s}     {mean:.1f}\n")
    return mean


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true",
                        help="save results to tests/semantic_tests/results/")
    args = parser.parse_args()

    sem_model, sem_emb, sem_mat, sem_mean, sem_alts = load_minilm()
    models = [("MiniLM (current)",
               lambda q, m=sem_model, mat=sem_mat, mean=sem_mean:
                   embed_query_minilm(m, q, mat, mean),
               sem_emb, sem_alts)]

    print("\n" + "="*80)
    print("SEARCH QUALITY  —  rank of known-correct target (lower = better)")
    print(f"corpus={len(sem_alts):,}  |  {len(RANK_TESTS)} test queries")
    print("="*80)

    summary = []
    per_query = []
    for name, embed_fn, emb, alts in models:
        print(f"\n[{name}]")
        ranks = []
        for query, target in RANK_TESTS:
            q = embed_fn(query)
            order = score_and_rank(emb, q)
            ranked_alts = [alts[i] for i in order]
            r = rank_of(ranked_alts, target)
            top3 = ", ".join(ranked_alts[:3])
            r_str = str(r) if r else "NOT FOUND"
            print(f"  {query!r:45s}  -> {target}: rank={r_str:>6}  | top3: {top3}")
            ranks.append(r if r else len(alts))
            per_query.append({"query": query, "target": target, "rank": r, "top3": ranked_alts[:3]})
        mean = sum(ranks) / len(ranks)
        print(f"  {'MEAN RANK':45s}     {mean:.1f}\n")
        summary.append((name, mean))

    print("="*80)
    print("SUMMARY — mean rank (lower = better):")
    for name, mean in sorted(summary, key=lambda x: x[1]):
        print(f"  {mean:8.1f}  {name}")
    print()

    if args.save:
        results_dir = Path(__file__).parent / "results"
        results_dir.mkdir(exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out = results_dir / f"{ts}.json"
        payload = {
            "recorded_at": ts,
            "corpus_size": len(sem_alts),
            "mean_rank": summary[0][1],
            "queries": per_query,
        }
        out.write_text(json.dumps(payload, indent=2))
        print(f"Saved → {out}")


if __name__ == "__main__":
    main()

