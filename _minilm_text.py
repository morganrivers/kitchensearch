"""
ONNX runtime wrapper for sentence-transformers/all-MiniLM-L6-v2 (via fastembed cache).

Lookup order:
  1. Bundled at data/models/all-MiniLM-L6-v2-onnx/ (binary releases).
  2. Fastembed cache at ~/.cache/fastembed/models--qdrant--all-MiniLM-L6-v2-onnx/
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

_REPO       = Path(__file__).resolve().parent
BUNDLED_DIR = _REPO / "data" / "models" / "all-MiniLM-L6-v2-onnx"

_FASTEMBED_CACHE = (
    Path.home() / ".cache" / "fastembed" /
    "models--qdrant--all-MiniLM-L6-v2-onnx" / "snapshots"
)


def _bundled_paths() -> tuple[Path, Path] | None:
    onnx = BUNDLED_DIR / "model.onnx"
    if onnx.exists() and (BUNDLED_DIR / "tokenizer.json").exists():
        return onnx, BUNDLED_DIR
    return None


def _fastembed_paths() -> tuple[Path, Path] | None:
    if not _FASTEMBED_CACHE.exists():
        return None
    for snap in sorted(_FASTEMBED_CACHE.iterdir()):
        onnx = snap / "model.onnx"
        tok  = snap / "tokenizer.json"
        if onnx.exists() and tok.exists():
            return onnx, snap
    return None


def load() -> "MiniLMText":
    paths = _bundled_paths() or _fastembed_paths()
    if paths is None:
        raise FileNotFoundError(
            "MiniLM model not found. Install fastembed and run: "
            "from fastembed import TextEmbedding; "
            "TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')"
        )
    onnx_path, tok_dir = paths
    return MiniLMText(onnx_path, tok_dir)


class MiniLMText:
    def __init__(self, onnx_path: Path, tok_dir: Path) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        cfg_path = tok_dir / "tokenizer_config.json"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
            max_len = min(cfg.get("model_max_length") or cfg.get("max_length", 512), 512)
            pad_id  = cfg.get("pad_token_id", 0)
            pad_tok = cfg.get("pad_token", "[PAD]")
        else:
            max_len, pad_id, pad_tok = 512, 0, "[PAD]"

        self._tok = Tokenizer.from_file(str(tok_dir / "tokenizer.json"))
        self._tok.enable_truncation(max_length=max_len)
        if not self._tok.padding:
            self._tok.enable_padding(pad_id=pad_id, pad_token=pad_tok)

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = 16
        opts.inter_op_num_threads = 4
        opts.execution_mode = ort.ExecutionMode.ORT_PARALLEL
        self._sess   = ort.InferenceSession(str(onnx_path), opts)
        self._inputs = {inp.name for inp in self._sess.get_inputs()}

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return L2-normalised float32 embeddings, shape (len(texts), 384)."""
        encoded = self._tok.encode_batch(texts)
        ids  = np.array([e.ids for e in encoded], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)

        feeds: dict[str, np.ndarray] = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feeds["token_type_ids"] = np.zeros_like(ids)

        out = self._sess.run(None, feeds)[0]  # (N, seq, 384)

        attn = mask[:, :, None].astype(np.float32)
        vecs = (out * attn).sum(axis=1) / attn.sum(axis=1)

        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return (vecs / np.maximum(norms, 1e-8)).astype(np.float32)
