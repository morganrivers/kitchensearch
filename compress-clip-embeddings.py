#!/usr/bin/env python3
"""
Compress jina-clip-v1 image and text embeddings via a shared PCA.
PCA is fit on image embeddings; the same matrix is applied to text embeddings
(valid because jina-clip-v1 puts both modalities in the same 768-dim space).

Outputs:
  jina-image-pca128.npy   - compressed image embeddings (float16)
  jina-text-pca128.npy    - compressed text embeddings  (float16)
  jina-pca128-matrix.npy  - 768×128 projection matrix   (float32)
  jina-pca128-mean.npy    - mean vector                  (float32)

Usage:
  python3 compress-clip-embeddings.py
  python3 compress-clip-embeddings.py --dims 256
"""

import sys
import numpy as np
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "data" / "embeddings"

dims = 128
if "--dims" in sys.argv:
    dims = int(sys.argv[sys.argv.index("--dims") + 1])

img_path = CACHE / "nomic-image-embeddings.npy"
txt_path = CACHE / "nomic-text-embeddings.npy"

for p in (img_path, txt_path):
    if not p.exists():
        print(f"{p.name} not found - run embed-all-emojikitchen-clip.py first.")
        sys.exit(1)

print("Loading image embeddings...", flush=True)
img_embs = np.load(img_path).astype(np.float32)
print(f"  shape: {img_embs.shape}  ({img_embs.nbytes // 1_000_000} MB float32)")

print("Loading text embeddings...", flush=True)
txt_embs = np.load(txt_path).astype(np.float32)
print(f"  shape: {txt_embs.shape}")

print(f"Fitting PCA on image embeddings → {dims} dims...", flush=True)
mean     = img_embs.mean(axis=0)
centered = img_embs - mean
_, _, Vt = np.linalg.svd(centered, full_matrices=False)
components = Vt[:dims].T  # 768 × dims


def project(embs):
    projected = (embs - mean) @ components
    norms = np.linalg.norm(projected, axis=1, keepdims=True)
    return projected / np.maximum(norms, 1e-8)


print("Projecting image embeddings...", flush=True)
img_pca = project(img_embs)

print("Projecting text embeddings...", flush=True)
txt_pca = project(txt_embs)

out_img  = CACHE / f"nomic-image-pca{dims}.npy"
out_txt  = CACHE / f"nomic-text-pca{dims}.npy"
out_mat  = CACHE / f"nomic-pca{dims}-matrix.npy"
out_mean = CACHE / f"nomic-pca{dims}-mean.npy"

np.save(out_img,  img_pca.astype(np.float16))
np.save(out_txt,  txt_pca.astype(np.float16))
np.save(out_mat,  components.astype(np.float32))
np.save(out_mean, mean.astype(np.float32))

print(f"Saved {out_img.name}   ({out_img.stat().st_size / 1_000_000:.1f} MB)")
print(f"Saved {out_txt.name}   ({out_txt.stat().st_size / 1_000_000:.1f} MB)")
print(f"Saved {out_mat.name}   ({out_mat.stat().st_size // 1000} KB)")
print(f"Saved {out_mean.name}")
