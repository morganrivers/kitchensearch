#!/bin/bash
# Uninstall Kitchen Search (Linux source install).
set -euo pipefail

DESKTOP="$HOME/.local/share/applications/kitchensearch.desktop"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/kitchensearch"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/kitchensearch"

# .desktop file
if [ -f "$DESKTOP" ]; then
    rm "$DESKTOP"
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    echo "Removed $DESKTOP"
else
    echo "No .desktop file found (already removed?)"
fi

# User data — ask before deleting
for DIR in "$CACHE_DIR" "$CONFIG_DIR"; do
    if [ -d "$DIR" ]; then
        printf "Remove %s ? [y/N] " "$DIR"
        read -r REPLY
        if [[ "$REPLY" =~ ^[Yy]$ ]]; then
            rm -rf "$DIR"
            echo "Removed $DIR"
        else
            echo "Kept $DIR"
        fi
    fi
done

echo ""
echo "Done. The source folder itself was not touched — delete it manually if you want."
