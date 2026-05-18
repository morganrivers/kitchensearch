#!/usr/bin/env python3
import os, time, threading, shutil
from pathlib import Path

os.environ["FASTEMBED_CACHE_PATH"] = str(Path.home() / ".cache" / "fastembed")
os.environ["HF_HUB_DISABLE_XET_TRANSFER"] = "1"

from fastembed import TextEmbedding

CACHE = Path(os.environ["FASTEMBED_CACHE_PATH"])
MODEL_DIR = CACHE / "models--Qdrant--clip-ViT-B-32-text"
BLOBS = MODEL_DIR / "blobs"

# wipe any incomplete/partial download so fastembed downloads fresh
if BLOBS.exists() and any(f.suffix == ".incomplete" for f in BLOBS.iterdir()):
    print(f"Removing incomplete cache at {MODEL_DIR}", flush=True)
    shutil.rmtree(MODEL_DIR)

def watch():
    while True:
        time.sleep(1)
        try:
            files = list(BLOBS.iterdir())
            total = sum(f.stat().st_size for f in files)
            inc = [f.name for f in files if f.suffix == ".incomplete"]
            print(f"  blobs total: {total/1e6:.2f} MB  incomplete: {inc}", flush=True)
        except Exception as e:
            print(f"  (watching: {e})", flush=True)

threading.Thread(target=watch, daemon=True).start()

print("Loading CLIP model (Qdrant/clip-ViT-B-32-text)...", flush=True)
t0 = time.time()
try:
    m = TextEmbedding("Qdrant/clip-ViT-B-32-text")
    print(f"Loaded in {time.time()-t0:.1f}s", flush=True)
    result = list(m.embed(["test"]))
    print(f"Embed OK, shape: {result[0].shape}", flush=True)
except Exception as e:
    print(f"FAILED: {e}", flush=True)
