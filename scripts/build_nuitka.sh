#!/bin/bash
# run with: ./scripts/build_nuitka.sh ;mv kitchensearch-linux-x86_64.tar.gz ~; cd ~; tar -xvf kitchensearch-linux-x86_64.tar.gz ; cd kitchensearch; ./kitchensearch

set -e
REPO_DIR=$(pwd)
VERSION=$(git -C "$REPO_DIR" describe --tags --abbrev=0 2>/dev/null || echo "dev")

if [ ! -d "$REPO_DIR/ksapp/data/fonts" ]; then
  echo "=== Extracting app assets ==="
  python "$REPO_DIR/scripts/extract_app_assets.py"
fi

if [ "$OS" = "Windows_NT" ]; then
  TRAY_ICON_ARG="--include-data-file=$REPO_DIR/ksapp/data/ui_assets/tray-icon.png=ksapp/data/ui_assets/tray-icon.png"
else
  TRAY_ICON_ARG=""
fi

echo "=== Building multidist (all four binaries, shared packages) ==="
python -m nuitka \
  --standalone \
  --main=ksapp/emoji_split_daemon.py \
  --main=ksapp/emoji_story.py \
  --main=kitchensearch.py \
  --main=kitchensearch_daemon.py \
  --enable-plugin=numpy \
  --enable-plugin=tk-inter \
  --include-package=platformdirs \
  --include-package=onnxruntime.capi \
  --include-package=tokenizers \
  --include-package=PIL \
  --include-package=screeninfo \
  --include-package=certifi \
  --include-package-data=certifi \
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
--include-data-dir="$REPO_DIR/ksapp/data/models/all-MiniLM-L6-v2-onnx=ksapp/data/models/all-MiniLM-L6-v2-onnx" \
  --include-data-dir="$REPO_DIR/ksapp/data/embeddings=ksapp/data/embeddings" \
  --include-data-dir="$REPO_DIR/ksapp/data/fonts=ksapp/data/fonts" \
  --include-data-file="$REPO_DIR/ksapp/data/app_assets.tar.gz=ksapp/data/app_assets.tar.gz" \
  --include-data-file="$REPO_DIR/LICENSE.txt=LICENSE.txt" \
  --include-data-file="$REPO_DIR/packaging/kitchensearch-icon.png=packaging/kitchensearch-icon.png" \
  --include-data-file="$REPO_DIR/packaging/kitchensearch.desktop=packaging/kitchensearch.desktop" \
  ${TRAY_ICON_ARG:+"$TRAY_ICON_ARG"} \
  --output-dir="$REPO_DIR/nuitka-build"
echo "=== build done ==="

cd "$REPO_DIR/nuitka-build"
rm -rf kitchensearch
mv emoji_split_daemon.dist kitchensearch
cd kitchensearch

if [ "$OS" = "Windows_NT" ]; then
  echo "Windows build — separate .exe per entry point, no symlinks or tar"
  cp emoji_split_daemon.exe kitchensearch.exe
  cp emoji_split_daemon.exe emoji_story.exe
  cp emoji_split_daemon.exe kitchensearch_daemon.exe

  ls -1 *.exe
else
  # Rename primary binary (strip .bin if present)
  [ -f emoji_split_daemon.bin ] && mv emoji_split_daemon.bin emoji_split_daemon
  # Multidist: single binary dispatches on argv[0] — create symlinks for other entry points
  ln -sf emoji_split_daemon kitchensearch
  ln -sf emoji_split_daemon emoji_story
  ln -sf emoji_split_daemon kitchensearch_daemon
  cd "$REPO_DIR/nuitka-build"
  echo "=== Scrubbing leaked build-machine paths ==="
  python "$REPO_DIR/scripts/scrub_paths.py" "$REPO_DIR/nuitka-build/kitchensearch"
  echo "=== Stripping debug symbols ==="
  bash "$REPO_DIR/scripts/strip_dist.sh" "$REPO_DIR/nuitka-build/kitchensearch"
  tar -czf "$REPO_DIR/kitchensearch-linux-x86_64.tar.gz" kitchensearch/
  echo "=== Done: kitchensearch-linux-x86_64.tar.gz ==="
fi
