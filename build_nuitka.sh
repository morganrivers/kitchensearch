#!/bin/bash
set -e
REPO_DIR=$(pwd)
CTK_DIR=/home/dmrivers/micromamba/envs/py311/lib/python3.11/site-packages/customtkinter
VENV_PY="$REPO_DIR/.venv/bin/python3"

echo "=== [1/3] Building emoji-split-daemon ==="
micromamba run -n py311 python -m nuitka \
  --standalone \
  --enable-plugin=numpy \
  --include-package=platformdirs \
  --include-package=onnxruntime \
  --include-package=tokenizers \
  --nofollow-import-to=pytest \
  --nofollow-import-to=torch \
  --nofollow-import-to=matplotlib \
  --nofollow-import-to=setuptools \
  --include-data-dir="$REPO_DIR/data/models=data/models" \
  --include-data-dir="$REPO_DIR/data/embeddings=data/embeddings" \
  --output-dir="$REPO_DIR/nuitka-build" \
  emoji-split-daemon.py
echo "=== daemon done ==="

echo "=== [2/3] Building emoji-story ==="
micromamba run -n py311 python -m nuitka \
  --standalone \
  --include-package=PIL \
  --include-package=platformdirs \
  --nofollow-import-to=pytest \
  --output-dir="$REPO_DIR/nuitka-build" \
  emoji-story.py
echo "=== story done ==="

echo "=== [3/3] Building emoji-picker-tk ==="
micromamba run -n py311 python -m nuitka \
  --standalone \
  --enable-plugin=tk-inter \
  --include-package=customtkinter \
  --include-package=Xlib \
  --include-package=PIL \
  --include-package=platformdirs \
  --include-package=screeninfo \
  --nofollow-import-to=pytest \
  --include-data-dir="$CTK_DIR=customtkinter" \
  --include-data-dir="$REPO_DIR/data/fonts=data/fonts" \
  --include-data-file="$REPO_DIR/data/app_assets.tar.gz=data/app_assets.tar.gz" \
  --output-dir="$REPO_DIR/nuitka-build" \
  emoji-picker-tk.py
echo "=== picker done ==="

echo "=== Merging dist folders ==="
DIST="$REPO_DIR/nuitka-build/emoji-picker-tk.dist"

# Merge daemon and story dists into the main dist
rsync -a "$REPO_DIR/nuitka-build/emoji-split-daemon.dist/" "$DIST/"
rsync -a "$REPO_DIR/nuitka-build/emoji-story.dist/" "$DIST/"

# Strip .bin extension from the three main binaries
for name in emoji-picker-tk emoji-split-daemon emoji-story; do
  if [ -f "$DIST/$name.bin" ]; then
    mv "$DIST/$name.bin" "$DIST/$name"
  fi
done

echo "=== Creating release tarball ==="
cd "$REPO_DIR/nuitka-build"
rm -rf emoji-kitchen
mv emoji-picker-tk.dist emoji-kitchen
tar -czf "$REPO_DIR/emoji-kitchen-linux-x86_64.tar.gz" emoji-kitchen/
echo "=== Done: emoji-kitchen-linux-x86_64.tar.gz ==="
