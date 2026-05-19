#!/bin/bash
# Installs a venv which allows the app to run (this is simplest "building from source" option)
# Only tested on Ubuntu.
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
"$VENV/bin/pip" install --quiet -r "requirements.txt"
