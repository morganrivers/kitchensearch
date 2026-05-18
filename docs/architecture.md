# Emoji Kitchen Search — Architecture

## Overview

A desktop emoji picker backed by semantic search over ~147k Google Emoji Kitchen
combination images.  The user types a natural-language query ("crying potato",
"sad coffee") and gets ranked results with thumbnails, copyable to clipboard.

The app has two distinct phases:

- **Build time** (developer runs once): download all combo images, embed them
  with a vision model + embed their keyword texts with a text model, PCA-compress
  the embeddings, commit the compressed files to a GitHub release.
- **Runtime** (user): download the compressed embeddings (~150 MB) from GitHub
  and the text model (~138 MB) from HuggingFace, then run a local daemon that
  serves ranked results over a Unix socket.

---

## Model: jinaai/jina-clip-v1

All embeddings use a single model: **jinaai/jina-clip-v1**.

| Encoder | ONNX file | Size | Used by |
|---------|-----------|------|---------|
| Text (int8 quantized) | `onnx/text_model_int8.onnx` | 138 MB | Runtime daemon, build scripts |
| Vision (fp32) | `onnx/vision_model.onnx` | 344 MB | Build time only (image embedding) |

Both encoders output **768-dimensional L2-normalised vectors in the same space**,
meaning a text query vector can be directly dot-producted against both
pre-embedded image vectors and pre-embedded text vectors.

Key advantages over the previous two-model setup (CLIP ViT-B/32 + MiniLM):

- **8192 token context** vs. CLIP's hard 77-token truncation — the long keyword
  strings in the search index fit in full.
- **Unified space** — no need to fuse two independent ranking lists; image and
  text embeddings are directly comparable.
- **Single runtime download** — only the text model is needed at runtime (138 MB
  int8 vs. the old 88 MB MiniLM + 245 MB CLIP text = 333 MB combined).

Score fusion follows jina-clip-v1's own recommendation: text-text cosine
similarities are empirically larger than text-image cosine similarities, so
image scores are upweighted:

```
combined = sim(query, keyword_text) + 2.0 * sim(query, image)
```

---

## File Map

### Scripts

| File | Phase | Purpose |
|------|-------|---------|
| `embed-all-emojikitchen-clip.py` | Build | Embed all 147k combo images (vision model) and their keyword texts (text model); saves raw 768-dim float16 arrays |
| `compress-clip-embeddings.py` | Build | PCA 768→128 dims; fits on image embeddings, projects both image and text with same matrix |
| `build-base-emoji-embeddings.py` | Build | Embed the ~618 individual base emoji names with the text model (full 768-dim, no PCA) |
| `_jina_text.py` | Both | Shared helper: downloads `text_model_int8.onnx` + tokenizer from HF hub, wraps onnxruntime inference |
| `emoji-split-daemon.py` | Runtime | Persistent daemon; loads model + embeddings once, serves ranked results over Unix socket |
| `emoji-picker-tk.py` | Runtime | Tkinter UI; spawns daemon on first semantic search, sends queries, renders thumbnail results |

### Data files (distributed in `data.tar.gz` from GitHub releases)

| File | Shape | Description |
|------|-------|-------------|
| `search-index.tsv` | 147k rows | Master index: `url \t alt \t keyword_text` per combo |
| `jina-image-pca128.npy` | (147k, 128) float16 | PCA-compressed vision embeddings of combo images |
| `jina-text-pca128.npy` | (147k, 128) float16 | PCA-compressed text embeddings of combo keyword strings |
| `jina-pca128-matrix.npy` | (768, 128) float32 | PCA projection matrix (fit on image embeddings) |
| `jina-pca128-mean.npy` | (768,) float32 | Mean vector subtracted before PCA projection |
| `jina-urls.txt` | 147k lines | Image URLs, row-aligned with the two `.npy` files above |
| `jina-alts.txt` | 147k lines | Short alt text (e.g. `sob-potato`), row-aligned |
| `base-emoji-codes.txt` | ~618 lines | Hex codes for the individual base emojis |
| `base-emoji-names.txt` | ~618 lines | Names for each base emoji, row-aligned with codes |
| `base-emoji-jina.npy` | (618, 768) float16 | Full-dim text embeddings of base emoji names |

### Runtime cache (`data/cache/`)

| File | Description |
|------|-------------|
| `split-daemon.sock` | Unix socket the daemon listens on |
| `split-daemon-loading.json` | Progress JSON polled by the UI during model load |
| `split-daemon.pid` | PID of the running daemon |
| `split-daemon.log` | Daemon stdout/stderr |
| `thumbs/` | Downloaded combo image thumbnails (capped at 200 MB, LRU eviction) |
| `picker-settings.json` | User settings (exit on select, search mode visibility, etc.) |

---

## Build Pipeline

Run once on the developer machine.  The vision model (344 MB) is downloaded
automatically by fastembed on first run.

```
# 1. Embed 147k combo images (vision) and their keyword texts (text model)
#    Add --limit 1000 for a quick smoke test before the full run.
python3 embed-all-emojikitchen-clip.py [--limit N] [--reset]
  → data/embeddings/jina-image-embeddings.npy   (147k × 768 float16)
  → data/embeddings/jina-text-embeddings.npy    (147k × 768 float16)
  → data/embeddings/jina-urls.txt
  → data/embeddings/jina-alts.txt

# 2. PCA compress both with a shared 768→128 projection
python3 compress-clip-embeddings.py [--dims N]   # default 128
  → data/embeddings/jina-image-pca128.npy        (147k × 128 float16, ~38 MB)
  → data/embeddings/jina-text-pca128.npy         (147k × 128 float16, ~38 MB)
  → data/embeddings/jina-pca128-matrix.npy       (768 × 128 float32)
  → data/embeddings/jina-pca128-mean.npy         (768,  float32)

# 3. Embed individual base emoji names (for 1/2-word decomposed search)
python3 build-base-emoji-embeddings.py [--force]
  → data/embeddings/base-emoji-jina.npy          (618 × 768 float16)
  → data/embeddings/base-emoji-codes.txt
  → data/embeddings/base-emoji-names.txt

# 4. Package for release
tar -czf data.tar.gz data/embeddings/search-index.tsv \
    data/embeddings/jina-*.npy data/embeddings/jina-*.txt \
    data/embeddings/base-emoji-jina.npy \
    data/embeddings/base-emoji-codes.txt data/embeddings/base-emoji-names.txt
# Upload data.tar.gz to the GitHub release at DATA_TARBALL_URL
```

The PCA is fit **only on image embeddings**.  Because jina-clip-v1 aligns both
modalities into the same space, the same projection matrix can be applied to text
embeddings without loss of cross-modal comparability.

---

## Runtime Search Pipeline

### Startup

1. User opens `emoji-picker-tk.py`.
2. If `data.tar.gz` has not been downloaded, a progress dialog fetches it from
   the GitHub release and extracts it.
3. When the user selects *semantic search*, the UI spawns `emoji-split-daemon.py`
   as a background process and polls `split-daemon-loading.json` to show a
   loading bar while the model downloads/loads.

### Daemon load sequence (`emoji-split-daemon.py`)

| Step | % | Action |
|------|---|--------|
| 1 | 5–40 | Download `text_model_int8.onnx` + tokenizer from HF hub if not cached |
| 2 | 42 | Load onnxruntime session, warm up tokenizer |
| 3 | 50 | Load `base-emoji-jina.npy` (618 × 768) + code→index map |
| 4 | 58 | Parse `search-index.tsv` → `combo_map[(code1, code2)] = (url, alt)` |
| 5 | 70 | Load `jina-image-pca128.npy`, `jina-text-pca128.npy`, PCA matrix/mean |
| 6 | 100 | Bind Unix socket, start accepting queries |

### Query handling

For a query string `q`:

**Step 1 — Embed query**

```python
q_vec = text_model.embed([q])[0]          # (768,) float32, L2-normalised
q_pca = (q_vec - pca_mean) @ pca_matrix   # (128,) float32
q_pca /= norm(q_pca)
```

**Step 2 — Decomposed search (1 or 2 words only)**

For single-word queries the daemon finds which of the 618 base emojis best
matches the word and returns its self-combination (e.g. "coffee" → coffee-coffee)
as the top result.

For two-word queries it cross-ranks every combo `(emoji_a, emoji_b)` by the
minimum sum of base-emoji ranks for both word assignments:
`min(rank[word1][a] + rank[word2][b], rank[word1][b] + rank[word2][a])`.

Base emoji scoring uses **full 768-dim dot product** (no PCA), comparing the
query vector directly against `base-emoji-jina.npy`.

**Step 3 — Combined search (all queries)**

```python
txt_scores = jina_text_pca  @ q_pca   # (147k,)
img_scores = jina_image_pca @ q_pca   # (147k,)
combined   = txt_scores + 2.0 * img_scores
```

The factor of 2 on image scores compensates for the empirical finding that
text-text cosine similarities are systematically larger than text-image cosine
similarities (jina-clip-v1 model card recommendation).

**Step 4 — Merge and return**

Decomposed results (if any) are prepended to the combined results with
duplicates removed by URL.  The merged list (up to `limit`, default 5000) is
returned as JSON over the socket.

### Idle shutdown

The daemon exits automatically after 600 seconds of no incoming queries.

---

## `_jina_text.py` — Model Helper

Thin module (~70 lines) used by the daemon and both build scripts.  No dependency
on fastembed or transformers.

| Function | Description |
|----------|-------------|
| `is_cached()` | Returns `True` if `text_model_int8.onnx` is already in the HF hub local cache |
| `download(status_cb, pct_start, pct_end)` | Downloads ONNX + tokenizer files from HF hub; calls `status_cb(message, pct)` periodically for progress display |
| `load()` | Constructs a `JinaText` instance from the cached files |
| `JinaText.embed(texts)` | Tokenize + run onnxruntime inference; returns `(N, 768)` float32 L2-normalised array |

Tokenization uses the `tokenizers` library directly (already a fastembed
dependency) with settings read from `tokenizer_config.json` in the HF hub
snapshot.  Handles both 2-D `(batch, dim)` and 3-D `(batch, seq, dim)` ONNX
outputs (takes CLS token `[:, 0]` in the 3-D case).

---

## Keyword Search (no daemon)

The tk app also has a fast keyword search that needs no model at all.  It scans
`search-index.tsv` in-process using regex word-boundary matching against the
keyword text column.  Results are scored by exact-token match count and sorted
with priority emojis (fire, cat, coffee, …) boosted.  This is instant but only
finds results that literally contain the query words.

---

## Settings

Stored in `data/cache/picker-settings.json`.

| Key | Default | Description |
|-----|---------|-------------|
| `show_keyword` | `true` | Show keyword search on main menu |
| `show_combo` | `true` | Show combo picker on main menu |
| `show_semantic` | `true` | Show semantic search on main menu |
| `show_story` | `true` | Show emoji story generator on main menu |
| `exit_on_select` | `false` | Close app after copying an emoji |
| `floating` | `false` | Use WM floating hint (i3) |
| `frameless` | `true` | No title bar (`-topmost` on Linux) |

---

## Dependencies

### Runtime (user machine)

| Package | Purpose |
|---------|---------|
| `customtkinter` | Scrollbar widget |
| `Pillow` | Thumbnail loading and rendering |
| `python-xlib` | Clipboard ownership (Linux X11) |
| `onnxruntime` | Run `text_model_int8.onnx` |
| `tokenizers` | Fast BERT tokenization |
| `huggingface_hub` | Download model from HF on first use |
| `numpy` | Embedding arithmetic |

### Build time only (developer machine, not shipped)

| Package | Purpose |
|---------|---------|
| `fastembed` | `ImageEmbedding("jinaai/jina-clip-v1")` for vision embedding of combo images |
