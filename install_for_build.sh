#!/bin/bash
set -e

VENV_DIR=".venv"

# Pick the right activate path for Windows (Git Bash) vs Linux
if [ "$OS" = "Windows_NT" ]; then
  ACTIVATE="$VENV_DIR/Scripts/activate"
else
  ACTIVATE="$VENV_DIR/bin/activate"
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "=== Creating venv at $VENV_DIR ==="
  python -m venv "$VENV_DIR"
else
  echo "=== Reusing existing venv at $VENV_DIR ==="
fi

echo "=== Activating venv ==="
# shellcheck disable=SC1090
source "$ACTIVATE"

echo "=== Upgrading pip ==="
python -m pip install --upgrade pip

echo "=== Installing build requirements ==="
pip install -r requirements_to_build_nuitka.txt

echo ""
echo "=== Done ==="
echo "To use this env in a new shell, run:"
echo "  source $ACTIVATE"
