"""Force Tk to render the UI text with a bundled TTF (Linux only).

On some Linux installs Tk falls back to X core bitmap fonts (Helvetica,
Times, etc.), which render aliased/jagged. Registering the shipped DejaVu
Sans TTF with fontconfig at process start, and pointing every Tk named
font at that family, makes the UI text render through Xft with AA/hinting.

macOS (Aqua) and Windows already have high-quality default UI fonts, so
this module is a no-op on those platforms.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ksapp.data_assets import ensure_data, _REPO
from ksapp.log import _dbg

BUNDLED_FONT_DIR = _REPO / "data" / "fonts"
BUNDLED_SANS_TTF = BUNDLED_FONT_DIR / "DejaVuSans.ttf"
FAMILY_SANS = "DejaVu Sans"
FAMILY_MONO = "DejaVu Sans Mono"

# Family every widget in this app should name for its `font=(...)` tuple.
# Linux Tk otherwise routes "Helvetica" through fontconfig aliases that vary
# by distro (Nimbus Sans, Liberation Sans, ...) — bundling and naming a
# specific TTF keeps the UI text visually identical everywhere.
# macOS/Windows already ship high-quality UI fonts, so keep the prior names.
UI_FAMILY = {
    "linux":  FAMILY_SANS,   # bundled DejaVu Sans
    "darwin": "Helvetica",   # native Aqua sans, renders great
    "win32":  "Segoe UI",    # native Windows UI font
}.get(sys.platform, FAMILY_SANS)

_TK_NAMED_FONTS_SANS = (
    "TkDefaultFont",
    "TkTextFont",
    "TkMenuFont",
    "TkHeadingFont",
    "TkCaptionFont",
    "TkSmallCaptionFont",
    "TkIconFont",
    "TkTooltipFont",
)

_registered = False


def _register_bundled_fonts() -> bool:
    """Load every TTF in BUNDLED_FONT_DIR into this process' fontconfig.

    Uses FcConfigAppFontAddFile so no user-level install is needed and no
    other process sees the extra fonts. Returns True on success.
    """
    global _registered
    if _registered:
        return True
    if sys.platform != "linux":
        return False

    ensure_data()
    if not BUNDLED_SANS_TTF.is_file():
        # Older extraction (pre-DejaVu tarball); nothing to register. Tk
        # will fall back to whichever family it was going to pick anyway.
        _dbg(f"font_setup: {BUNDLED_SANS_TTF} not present, skipping")
        return False

    import ctypes
    import ctypes.util

    libname = ctypes.util.find_library("fontconfig") or "libfontconfig.so.1"
    fc = ctypes.CDLL(libname)
    fc.FcConfigGetCurrent.restype = ctypes.c_void_p
    fc.FcConfigAppFontAddFile.restype = ctypes.c_int
    fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

    cfg = fc.FcConfigGetCurrent()
    assert cfg, "FcConfigGetCurrent returned NULL"
    for ttf in sorted(BUNDLED_FONT_DIR.glob("*.ttf")):
        ok = fc.FcConfigAppFontAddFile(cfg, str(ttf).encode())
        assert ok, f"FcConfigAppFontAddFile failed for {ttf}"

    _registered = True
    return True


def apply(root) -> None:
    """Point Tk's named fonts at the bundled DejaVu family (Linux only).

    Call once, right after tk.Tk() (or tk.Toplevel()) is created. Safe to
    call more than once — subsequent calls just re-issue the configure.
    """
    if sys.platform != "linux":
        return
    if not _register_bundled_fonts():
        return

    import tkinter.font as tkFont

    families = tkFont.families(root=root)
    if FAMILY_SANS not in families:
        _dbg(f"font_setup: {FAMILY_SANS!r} not visible to Tk after fontconfig register")
        return

    for name in _TK_NAMED_FONTS_SANS:
        tkFont.nametofont(name, root=root).configure(family=FAMILY_SANS)

    if FAMILY_MONO in families:
        tkFont.nametofont("TkFixedFont", root=root).configure(family=FAMILY_MONO)
