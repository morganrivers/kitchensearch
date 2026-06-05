import sys
import tarfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent
UI_ASSETS_DIR = _REPO / "data" / "ui_assets"
SEARCH_INDEX = UI_ASSETS_DIR / "search-index.tsv"
APP_ASSETS_TAR = _REPO / "data" / "app_assets.tar.gz"


def ensure_data():
    if SEARCH_INDEX.exists():
        return
    if not APP_ASSETS_TAR.exists():
        sys.exit(f"Data files missing and {APP_ASSETS_TAR} not found. Please re-download the app.")
    UI_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    with tarfile.open(APP_ASSETS_TAR) as tf:
        tf.extractall(_REPO)
