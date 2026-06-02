#!/bin/bash
set -e
REPO_DIR=$(pwd)
VERSION=$(git -C "$REPO_DIR" describe --tags --abbrev=0 2>/dev/null || echo "dev")

if [ ! -d "$REPO_DIR/data/fonts" ]; then
  echo "=== Extracting app assets ==="
  python "$REPO_DIR/extract_app_assets.py"
fi

if [ "$OS" = "Windows_NT" ]; then
  TRAY_ICON_ARG="--include-data-file=$REPO_DIR/data/ui_assets/tray-icon.png=data/ui_assets/tray-icon.png"
else
  TRAY_ICON_ARG=""
fi

echo "=== Building multidist (all four binaries, shared packages) ==="
python -m nuitka \
  --standalone \
  --main=emoji-split-daemon.py \
  --main=emoji-story.py \
  --main=kitchensearch.py \
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
  --windows-console-mode=disable \
--include-data-dir="$REPO_DIR/data/models/all-MiniLM-L6-v2-onnx=data/models/all-MiniLM-L6-v2-onnx" \
  --include-data-dir="$REPO_DIR/data/embeddings=data/embeddings" \
  --include-data-dir="$REPO_DIR/data/fonts=data/fonts" \
  --include-data-file="$REPO_DIR/data/app_assets.tar.gz=data/app_assets.tar.gz" \
  ${TRAY_ICON_ARG:+"$TRAY_ICON_ARG"} \
  --output-dir="$REPO_DIR/nuitka-build"
echo "=== build done ==="

cd "$REPO_DIR/nuitka-build"
rm -rf kitchensearch
mv emoji-split-daemon.dist kitchensearch
cd kitchensearch

if [ "$OS" = "Windows_NT" ]; then
  echo "Windows build — separate .exe per entry point, no symlinks or tar"
  cp emoji-split-daemon.exe kitchensearch.exe
  cp emoji-split-daemon.exe emoji-story.exe
  cp emoji-split-daemon.exe kitchensearch-daemon.exe

  ls -1 *.exe
else
  # Rename primary binary (strip .bin if present)
  [ -f emoji-split-daemon.bin ] && mv emoji-split-daemon.bin emoji-split-daemon
  # Multidist: single binary dispatches on argv[0] — create symlinks for other entry points
  ln -sf emoji-split-daemon kitchensearch
  ln -sf emoji-split-daemon emoji-story
  ln -sf emoji-split-daemon kitchensearch-daemon
  cd "$REPO_DIR/nuitka-build"
  tar -czf "$REPO_DIR/kitchensearch-linux-x86_64.tar.gz" kitchensearch/
  echo "=== Done: kitchensearch-linux-x86_64.tar.gz ==="
fi
