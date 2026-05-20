"""
Ko-fi support-prompt A/B testing + weekly update check.

Variant assignment is deterministic from the build timestamp (set at
build/install time and shipped with the app). The server-side update-check
endpoint sees the same build timestamp and the request IP, which is how
the test is logged externally.

Variants (build_ts_ms % 6):
    0 -> ("simple", "A")    3 -> ("indie",  "A")
    1 -> ("simple", "B")    4 -> ("indie",  "B")
    2 -> ("simple", "C")    5 -> ("indie",  "C")

Timing thresholds (BOTH must be satisfied):
    A: 5 copies  + 36h since first launch
    B: 7 copies  + 48h
    C: 12 copies + 72h
"""
import json
import locale
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from picker_utils import _REPO, CACHE_DIR, _dbg

VERSION = "1.0.0"

BUILD_INFO_FILE = _REPO / "build_info.json"
UPDATE_CHECK_URL = "https://morganrivers.com/kitchensearchversions"
UPDATE_CHECK_INTERVAL_SEC = 7 * 24 * 3600  # weekly

KOFI_URL = "https://ko-fi.com/morganrivers"
# Prefer ui_assets/ (where the real button would be bundled), fall back to the
# committed placeholder in data/.
_KOFI_BUTTON_CANDIDATES = [
    _REPO / "data" / "ui_assets" / "kofi_button.png",
    _REPO / "data" / "kofi_button.png",
]


def _kofi_button_path():
    for p in _KOFI_BUTTON_CANDIDATES:
        if p.exists():
            return p
    return None


KOFI_BUTTON_IMG = _kofi_button_path()

_EU_COUNTRY_CODES = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
})

_TIMING = {
    "A": (5,  36),
    "B": (7,  48),
    "C": (12, 72),
}


def get_build_ts_ms():
    """Build timestamp in milliseconds. Falls back to repo mtime if no
    build_info.json was stamped (e.g. running directly from a git checkout
    with no install step). The fallback is stable per-install."""
    try:
        info = json.loads(BUILD_INFO_FILE.read_text())
        return int(info["build_ts_ms"])
    except Exception:
        try:
            return int(_REPO.stat().st_mtime * 1000)
        except Exception:
            return 0


def get_variant():
    """Return (copy_variant, timing_variant) for this install."""
    bucket = get_build_ts_ms() % 6
    copy_v = "indie" if bucket >= 3 else "simple"
    timing_v = ("A", "B", "C")[bucket % 3]
    return copy_v, timing_v


def get_timing_thresholds(timing_variant):
    return _TIMING[timing_variant]


def get_currency_symbol():
    """€ for EU locales, $ otherwise. Used only for any locale-sensitive
    surrounding text — the Ko-fi page itself handles currency on its end."""
    try:
        loc = locale.getlocale()[0] or locale.getdefaultlocale()[0] or ""
    except Exception:
        loc = ""
    parts = loc.replace("-", "_").split("_")
    country = parts[-1].upper() if len(parts) > 1 else ""
    return "€" if country in _EU_COUNTRY_CODES else "$"


def should_show_banner(settings):
    """True iff the Ko-fi banner should appear on the main menu."""
    if settings.get("kofi_banner_dismissed"):
        return False
    if not settings.get("kofi_banner_unlocked"):
        return False
    return True


def record_copy(settings):
    """Increment copy counter and unlock the banner if thresholds are met.
    Mutates settings in-place; caller is responsible for persisting."""
    settings["copy_count"] = int(settings.get("copy_count", 0)) + 1
    if settings.get("kofi_banner_unlocked") or settings.get("kofi_banner_dismissed"):
        return
    _, timing_v = get_variant()
    need_copies, need_hours = get_timing_thresholds(timing_v)
    first = float(settings.get("first_install_time") or 0)
    elapsed_h = (time.time() - first) / 3600 if first else 0
    if settings["copy_count"] >= need_copies and elapsed_h >= need_hours:
        settings["kofi_banner_unlocked"] = True


def get_banner_config(settings):
    """Return a dict the UI layer can render, or None if no banner."""
    if not should_show_banner(settings):
        return None
    copy_v, _ = get_variant()
    headline = "🥹 Support an indie developer!" if copy_v == "indie" else None
    btn_path = _kofi_button_path()
    img_path = str(btn_path) if btn_path else None
    return {
        "headline":  headline,
        "image":     img_path,
        "url":       KOFI_URL,
        "variant":   copy_v,
    }


# ── update check ─────────────────────────────────────────────────────────────

_update_lock = threading.Lock()


def _do_update_check(settings, save_settings, on_new_version):
    try:
        params = urllib.parse.urlencode({
            "v":     VERSION,
            "build": str(get_build_ts_ms()),
        })
        url = f"{UPDATE_CHECK_URL}?{params}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read(4096).decode("utf-8", errors="replace").strip()
        latest = _parse_update_response(body)
        with _update_lock:
            settings["last_update_check"] = int(time.time())
            if latest and latest != VERSION:
                settings["latest_known_version"] = latest
                on_new_version(latest)
            save_settings(settings)
    except Exception as e:
        _dbg(f"update check failed: {e!r}")


def _parse_update_response(body):
    if not body:
        return None
    try:
        obj = json.loads(body)
        if isinstance(obj, dict) and "latest" in obj:
            return str(obj["latest"]).strip()
    except Exception:
        pass
    first_line = body.splitlines()[0].strip()
    if all(c.isdigit() or c == "." for c in first_line) and first_line:
        return first_line
    return None


def maybe_run_update_check(settings, save_settings, on_new_version):
    """Run the update check in a background thread if a week has elapsed.
    `on_new_version(version_str)` is invoked from the worker thread when a
    newer version is detected — callers must marshal to the UI thread."""
    last = float(settings.get("last_update_check") or 0)
    if time.time() - last < UPDATE_CHECK_INTERVAL_SEC:
        return
    t = threading.Thread(
        target=_do_update_check,
        args=(settings, save_settings, on_new_version),
        daemon=True,
    )
    t.start()


def is_update_available(settings):
    latest = settings.get("latest_known_version")
    return bool(latest) and latest != VERSION
