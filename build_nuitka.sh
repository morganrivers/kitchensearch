#!/bin/bash
set -e
REPO_DIR=$(pwd)

if [ ! -d "$REPO_DIR/data/fonts" ]; then
  echo "=== Extracting app assets ==="
  python "$REPO_DIR/extract_app_assets.py"
fi

echo "=== Building multidist (all three binaries, shared packages) ==="
python -m nuitka \
  --standalone \
  --main=emoji-split-daemon.py \
  --main=emoji-story.py \
  --main=emoji-picker-tk.py \
  --main=kitchensearch-daemon.py \
  --enable-plugin=numpy \
  --enable-plugin=tk-inter \
  --include-package=platformdirs \
  --include-package=onnxruntime.capi \
  --include-package=tokenizers \
  --include-package=PIL \
  --include-package=screeninfo \
  --nofollow-import-to=pytest \
  --nofollow-import-to=torch \
  --nofollow-import-to=matplotlib \
  --nofollow-import-to=setuptools \
  --nofollow-import-to=transformers \
  --nofollow-import-to=huggingface_hub \
  --nofollow-import-to=faiss \
  --nofollow-import-to=numba \
  --nofollow-import-to=onnxruntime.backend \
  --nofollow-import-to=onnxruntime.datasets \
  --nofollow-import-to=onnxruntime.quantization \
  --nofollow-import-to=onnxruntime.tools \
  --nofollow-import-to=onnxruntime.transformers \
  --nofollow-import-to=PIL.ImageQt \
  --nofollow-import-to=PIL.ImageWin \
--include-data-dir="$REPO_DIR/data/models/all-MiniLM-L6-v2-onnx=data/models/all-MiniLM-L6-v2-onnx" \
  --include-data-dir="$REPO_DIR/data/embeddings=data/embeddings" \
  --include-data-dir="$REPO_DIR/data/fonts=data/fonts" \
  --include-data-file="$REPO_DIR/data/app_assets.tar.gz=data/app_assets.tar.gz" \
  --output-dir="$REPO_DIR/nuitka-build"
echo "=== build done ==="

cd "$REPO_DIR/nuitka-build"
rm -rf emoji-kitchen
mv emoji-split-daemon.dist emoji-kitchen
cd emoji-kitchen

if [ "$OS" = "Windows_NT" ]; then
  echo "Windows build — separate .exe per entry point, no symlinks or tar"
  cp emoji-split-daemon.exe emoji-picker-tk.exe
  cp emoji-split-daemon.exe emoji-story.exe
  cp emoji-split-daemon.exe kitchensearch-daemon.exe

  ls -1 *.exe
else
  # Rename primary binary (strip .bin if present)
  [ -f emoji-split-daemon.bin ] && mv emoji-split-daemon.bin emoji-split-daemon
  # Multidist: single binary dispatches on argv[0] — create symlinks for other entry points
  ln -sf emoji-split-daemon emoji-picker-tk
  ln -sf emoji-split-daemon emoji-story
  cd "$REPO_DIR/nuitka-build"
  tar -czf "$REPO_DIR/emoji-kitchen-linux-x86_64.tar.gz" emoji-kitchen/
  echo "=== Done: emoji-kitchen-linux-x86_64.tar.gz ==="
fi
