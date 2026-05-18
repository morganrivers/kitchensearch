"""
Lightweight runtime wrapper for jinaai/jina-clip-v1 text_model.onnx.
Downloads model + tokenizer from HuggingFace on first use.
No fastembed or transformers required — only onnxruntime + tokenizers.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable

import numpy as np

HF_REPO   = "jinaai/jina-clip-v1"
ONNX_FILE = "onnx/text_model.onnx"
TOK_FILES = ["tokenizer.json", "tokenizer_config.json", "config.json", "special_tokens_map.json"]
SIZE_MB   = 548


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
    stop = threading.Event()
    blobs = _blobs_dir()

    if status_cb:
        def _watch():
            while not stop.wait(0.4):
                try:
                    n = sum(f.stat().st_size for f in blobs.iterdir() if f.is_file())
                    frac = min(n / (SIZE_MB * 1_000_000), 0.99)
                    status_cb(
                        f"Downloading jina-clip text model ({n/1e6:.0f} / {SIZE_MB} MB)",
                        pct_start + (pct_end - pct_start) * frac,
                    )
                except Exception:
                    pass
        status_cb("Downloading jina-clip text model...", pct_start)
        threading.Thread(target=_watch, daemon=True).start()

    for fname in TOK_FILES + [ONNX_FILE]:
        hf_hub_download(HF_REPO, fname)
    stop.set()


def load() -> "JinaText":
    from huggingface_hub import hf_hub_download
    onnx_path = Path(hf_hub_download(HF_REPO, ONNX_FILE))
    tok_dir   = Path(hf_hub_download(HF_REPO, "tokenizer.json")).parent
    return JinaText(onnx_path, tok_dir)


class JinaText:
    """onnxruntime inference for jina-clip-v1 text encoder (fp32, 548 MB)."""

    def __init__(self, onnx_path: Path, tok_dir: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        with open(tok_dir / "tokenizer_config.json") as f:
            cfg = json.load(f)
        max_len = min(cfg.get("model_max_length") or cfg.get("max_length", 512), 8192)
        pad_id = cfg.get("pad_token_id", 0)

        self._tok = Tokenizer.from_file(str(tok_dir / "tokenizer.json"))
        self._tok.enable_truncation(max_length=max_len)
        if not self._tok.padding:
            self._tok.enable_padding(
                pad_id=pad_id,
                pad_token=cfg.get("pad_token", "[PAD]"),
            )

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._sess = ort.InferenceSession(str(onnx_path), opts)
        self._input_names = {inp.name for inp in self._sess.get_inputs()}

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return L2-normalised float32 embeddings, shape (len(texts), 768)."""
        encoded = self._tok.encode_batch(texts)
        inp_ids = np.array([e.ids for e in encoded], dtype=np.int64)

        feeds: dict[str, np.ndarray] = {"input_ids": inp_ids}
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(inp_ids)

        out = self._sess.run(None, feeds)[0]
        if out.ndim == 3:
            out = out[:, 0]  # CLS token

        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return (out / np.maximum(norms, 1e-8)).astype(np.float32)
