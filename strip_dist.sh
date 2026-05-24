#!/bin/bash
# Creates a stripped copy of the nuitka dist and reports size savings.
set -e
REPO_DIR=$(pwd)
SRC="$REPO_DIR/nuitka-build/emoji-kitchen"
DST="$REPO_DIR/nuitka-build/emoji-kitchen-stripped"

if [ ! -d "$SRC" ]; then
  echo "ERROR: $SRC not found — run build_nuitka.sh first"
  exit 1
fi

echo "Copying $SRC -> $DST ..."
rm -rf "$DST"
cp -a "$SRC" "$DST"

echo "Stripping debug symbols from all ELF binaries ..."
stripped=0
failed=0
while IFS= read -r -d '' f; do
  if strip --strip-unneeded "$f" 2>/dev/null; then
    (( stripped++ )) || true
  else
    (( failed++ )) || true
  fi
done < <(find "$DST" -type f \( -name "*.so*" -o -name "*.so" -o ! -name "*.*" \) -print0)

echo "  Stripped: $stripped files  |  Skipped/failed: $failed"

echo "Deduplicating identical libraries ..."
cd "$DST"
if [ -f libblas.so.3 ] && [ -f libcblas.so.3 ]; then
  if cmp -s libblas.so.3 libcblas.so.3; then
    rm libcblas.so.3
    ln -s libblas.so.3 libcblas.so.3
    echo "  libcblas.so.3 -> libblas.so.3 (saved ~19M)"
  fi
fi
cd "$REPO_DIR"

echo ""
echo "=== Size comparison ==="
before=$(du -sb "$SRC" | cut -f1)
after=$(du -sb "$DST" | cut -f1)
before_h=$(du -sh "$SRC" | cut -f1)
after_h=$(du -sh "$DST" | cut -f1)
saved=$(( (before - after) / 1024 / 1024 ))
echo "  Before: $before_h"
echo "  After:  $after_h"
echo "  Saved:  ~${saved}M"

# echo ""
# echo "=== Tarball ==="
# cd "$REPO_DIR/nuitka-build"
# tar -czf "$REPO_DIR/emoji-kitchen-stripped-linux-x86_64.tar.gz" emoji-kitchen-stripped/
# sz=$(du -sh "$REPO_DIR/emoji-kitchen-stripped-linux-x86_64.tar.gz" | cut -f1)
# echo "  emoji-kitchen-stripped-linux-x86_64.tar.gz: $sz"
