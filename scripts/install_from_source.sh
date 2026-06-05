#!/bin/bash
# Build-from-source installer. Only tested on Ubuntu.
set -euo pipefail

VENV=".venv"

if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "tkinter not found. Install it with: sudo apt install python3-tk"
    exit 1
fi

if [ ! -d "$VENV" ]; then
    echo "Creating virtualenv..."
    python3 -m venv "$VENV"
fi

echo "Installing Python dependencies..."
"$VENV/bin/pip" install -r "requirements.txt"

echo "Extracting data assets..."
tar -xzf ksapp/data/app_assets.tar.gz -C ksapp

echo "Installing .desktop file..."
INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
EXEC_CMD="$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/kitchensearch.py"
mkdir -p "$HOME/.local/share/applications"
sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    -e "s|__EXEC_CMD__|$EXEC_CMD|g" \
    "$INSTALL_DIR/packaging/kitchensearch.desktop" \
    > "$HOME/.local/share/applications/kitchensearch.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo ""
echo "Done. To set a keyboard shortcut:"
echo "  GNOME : Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts"
echo "  KDE   : System Settings → Shortcuts → Custom Shortcuts"
echo "  XFCE  : Settings → Keyboard → Application Shortcuts"
echo "  i3    : add to ~/.config/i3/config:"
echo "            bindsym \$mod+shift+e exec $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/kitchensearch.py"
echo "  sway  : same syntax in ~/.config/sway/config"
