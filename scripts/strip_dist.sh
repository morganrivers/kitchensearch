#!/bin/bash
# Strips debug symbols from ELF binaries in a Nuitka dist (in place) and
# dedups identical libraries via symlink. Idempotent. Reports size savings.
#
# Usage: strip_dist.sh [dist_directory]
#        default dist: ./nuitka-build/kitchensearch
set -e

DIST="${1:-$(pwd)/nuitka-build/kitchensearch}"

if [ ! -d "$DIST" ]; then
  echo "ERROR: $DIST not found — run build_nuitka.sh first" >&2
  exit 1
fi

before_h=$(du -sh "$DIST" | cut -f1)

echo "Stripping debug symbols from .so files in $DIST ..."
# Only .so files. Do NOT strip the main Nuitka binary (emoji-split-daemon):
# --strip-unneeded removes the section Nuitka uses for embedded Python
# constants, causing 'Error, corrupted constants object' at startup.
stripped=0
failed=0
while IFS= read -r -d '' f; do
  if strip --strip-unneeded "$f" 2>/dev/null; then
    stripped=$((stripped+1))
  else
    failed=$((failed+1))
  fi
done < <(find "$DIST" -type f \( -name "*.so" -o -name "*.so.*" \) -print0)
echo "  Stripped: $stripped files  |  Skipped/failed: $failed"

echo "Deduplicating identical libraries ..."
(
  cd "$DIST"
  if [ -f libblas.so.3 ] && [ -f libcblas.so.3 ] && cmp -s libblas.so.3 libcblas.so.3; then
    rm libcblas.so.3
    ln -s libblas.so.3 libcblas.so.3
    echo "  libcblas.so.3 -> libblas.so.3 (saved ~19M)"
  fi
)

after_h=$(du -sh "$DIST" | cut -f1)
echo "  Before: $before_h   After: $after_h"
