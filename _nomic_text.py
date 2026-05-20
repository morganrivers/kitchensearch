"""
ONNX runtime wrapper for keisuke-miyako/nomic-embed-text-v1-onnx-int8.
Int8 quantized (~138 MB).

Lookup order:
  1. Default HuggingFace cache (populated on first source-install run).
  2. Bundled at data/models/nomic-embed-text-v1-onnx-int8/ (binary releases).
If neither is present, download() pulls from HF Hub into the default cache.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable

import numpy as np

_REPO       = Path(__file__).resolve().parent
BUNDLED_DIR = _REPO / "data" / "models" / "nomic-embed-text-v1-onnx-int8"

HF_REPO   = "keisuke-miyako/nomic-embed-text-v1-onnx-int8"
ONNX_FILE = "model_quantized.onnx"
TOK_FILES = ["tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"]
SIZE_MB   = 138

DOC_PREFIX   = "search_document: "
QUERY_PREFIX = "search_query: "


def _blobs_dir() -> Path:
    root = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    return root / f"models--{HF_REPO.replace('/', '--')}" / "blobs"


def _hf_cached_paths() -> tuple[Path, Path] | None:
    try:
        from huggingface_hub import hf_hub_download
        onnx = Path(hf_hub_download(HF_REPO, ONNX_FILE, local_files_only=True))
        tok  = Path(hf_hub_download(HF_REPO, "tokenizer.json", local_files_only=True)).parent
        return onnx, tok
    except Exception:
        return None


def _bundled_paths() -> tuple[Path, Path] | None:
    onnx = BUNDLED_DIR / ONNX_FILE
    if onnx.exists() and (BUNDLED_DIR / "tokenizer.json").exists():
        return onnx, BUNDLED_DIR
    return None


def is_cached() -> bool:
    return _hf_cached_paths() is not None or _bundled_paths() is not None


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
                        f"Downloading nomic-embed-text ({n/1e6:.0f} / {SIZE_MB} MB)",
                        pct_start + (pct_end - pct_start) * frac,
                    )
                except Exception:
                    pass
        status_cb("Downloading nomic-embed-text model...", pct_start)
        threading.Thread(target=_watch, daemon=True).start()

    for fname in TOK_FILES + [ONNX_FILE]:
        hf_hub_download(HF_REPO, fname)
    stop.set()


def load() -> "NomicText":
    paths = _hf_cached_paths() or _bundled_paths()
    if paths is None:
        raise FileNotFoundError(
            f"nomic-embed-text model not found in HF cache or bundled at {BUNDLED_DIR}. "
            "Call download() first."
        )
    onnx_path, tok_dir = paths
    return NomicText(onnx_path, tok_dir)


class NomicText:
    def __init__(self, onnx_path: Path, tok_dir: Path) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        with open(tok_dir / "tokenizer_config.json") as f:
            cfg = json.load(f)
        max_len = min(cfg.get("model_max_length") or cfg.get("max_length", 512), 8192)
        pad_id  = cfg.get("pad_token_id", 0)

        self._tok = Tokenizer.from_file(str(tok_dir / "tokenizer.json"))
        self._tok.enable_truncation(max_length=max_len)
        if not self._tok.padding:
            self._tok.enable_padding(
                pad_id=pad_id,
                pad_token=cfg.get("pad_token", "[PAD]"),
            )

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = 16
        opts.inter_op_num_threads = 4
        opts.execution_mode = ort.ExecutionMode.ORT_PARALLEL
        self._sess   = ort.InferenceSession(str(onnx_path), opts)
        self._inputs = {inp.name for inp in self._sess.get_inputs()}

    def embed(self, texts: list[str], query: bool = False) -> np.ndarray:
        """Return L2-normalised float32 embeddings, shape (len(texts), 768)."""
        prefix   = QUERY_PREFIX if query else DOC_PREFIX
        prefixed = [prefix + t for t in texts]
        encoded  = self._tok.encode_batch(prefixed)
        ids      = np.array([e.ids for e in encoded], dtype=np.int64)
        mask     = np.array([e.attention_mask for e in encoded], dtype=np.int64)

        feeds: dict[str, np.ndarray] = {"input_ids": ids}
        if "attention_mask" in self._inputs:
            feeds["attention_mask"] = mask
        if "token_type_ids" in self._inputs:
            feeds["token_type_ids"] = np.zeros_like(ids)

        out = self._sess.run(None, feeds)[0]  # (N, seq, 768)

        attn  = mask[:, :, None].astype(np.float32)
        vecs  = (out * attn).sum(axis=1) / attn.sum(axis=1)

        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return (vecs / np.maximum(norms, 1e-8)).astype(np.float32)
