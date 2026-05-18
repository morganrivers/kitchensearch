"""
ONNX runtime wrapper for nomic-ai/nomic-embed-vision-v1.
CLIP-style image encoder; outputs CLS token from last_hidden_state.
"""
from __future__ import annotations

import threading
import numpy as np
from pathlib import Path
from typing import Callable
import os

HF_REPO   = "nomic-ai/nomic-embed-vision-v1"
ONNX_FILE = "onnx/model.onnx"
SIZE_MB   = 374

# CLIP normalization constants (same as jina-clip-v1)
_MEAN = np.array([0.48145466, 0.4578275,  0.40821073], dtype=np.float32)
_STD  = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def _blobs_dir() -> Path:
    root = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    return root / f"models--{HF_REPO.replace('/', '--')}" / "blobs"


def is_cached() -> bool:
    try:
        from huggingface_hub import hf_hub_download
        hf_hub_download(HF_REPO, ONNX_FILE, local_files_only=True)
        return True
    except Exception:
        return False


def download(status_cb: Callable[[str, float], None] | None = None,
             pct_start: float = 0, pct_end: float = 100) -> None:
    from huggingface_hub import hf_hub_download
    stop  = threading.Event()
    blobs = _blobs_dir()

    if status_cb:
        def _watch() -> None:
            while not stop.wait(0.4):
                try:
                    n = sum(f.stat().st_size for f in blobs.iterdir() if f.is_file())
                    frac = min(n / (SIZE_MB * 1_000_000), 0.99)
                    status_cb(
                        f"Downloading nomic-embed-vision ({n/1e6:.0f} / {SIZE_MB} MB)",
                        pct_start + (pct_end - pct_start) * frac,
                    )
                except Exception:
                    pass
        status_cb("Downloading nomic-embed-vision model...", pct_start)
        threading.Thread(target=_watch, daemon=True).start()

    hf_hub_download(HF_REPO, ONNX_FILE)
    stop.set()


def load() -> "NomicVision":
    from huggingface_hub import hf_hub_download
    onnx_path = Path(hf_hub_download(HF_REPO, ONNX_FILE))
    return NomicVision(onnx_path)


class NomicVision:
    def __init__(self, onnx_path: Path) -> None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._sess = ort.InferenceSession(str(onnx_path), opts)

    def _preprocess(self, path: str) -> np.ndarray:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        w, h = img.size
        scale = 224 / min(w, h)
        new_w, new_h = int(round(w * scale)), int(round(h * scale))
        img  = img.resize((new_w, new_h), Image.BICUBIC)
        left = (new_w - 224) // 2
        top  = (new_h - 224) // 2
        img  = img.crop((left, top, left + 224, top + 224))
        arr  = np.array(img, dtype=np.float32) / 255.0
        arr  = (arr - _MEAN) / _STD
        return arr.transpose(2, 0, 1)  # (3, 224, 224)

    def embed(self, paths: list[str]) -> np.ndarray:
        """Return L2-normalised float32 embeddings, shape (len(paths), 768)."""
        batch = np.stack([self._preprocess(p) for p in paths])
        out   = self._sess.run(None, {"pixel_values": batch})[0]  # (N, 197, 768)
        vecs  = out[:, 0, :]  # CLS token
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return (vecs / np.maximum(norms, 1e-8)).astype(np.float32)
