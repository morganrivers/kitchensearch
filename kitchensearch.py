#!/usr/bin/env python3
"""
Emoji kitchen picker - tkinter UI replacing rofi.
Results appear one-by-one as thumbnails download. Borderless, half-screen, centered.

Bind in i3 config:
  bindsym $mod+shift+e exec --no-startup-id python3 ~/.local/bin/kitchensearch.py
"""
import os, sys, re, json, hashlib
from pathlib import Path
from PIL import Image
from ksapp.picker_utils import (
    DATA_DIR, UI_ASSETS_DIR, CACHE_DIR, CONFIG_DIR, THUMB_DIR,
    SEARCH_INDEX, _PYTHON,
    STORY_OUT, STORY_PY, STORY_BIN,
    DAEMON_LOG,
    BATCH_SIZE, LOAD_MORE, MAX_RESULTS, HEADER_MARKER,
    _has_semantic_models, _notify, _dbg,
    load_index, search, build_base_emoji_index, format_label,
    get_thumb, render_emoji_pil,
    copy_image_to_clipboard, record_copy,
    query_daemon,
    _trim_thumb_cache, _spawn_daemon, _daemon_alive,
    load_favorites, remove_favorite, toggle_favorite, top_copied,
)
from ksapp.picker_ui import (
    TkPicker, pick_base_emoji,
)
from ksapp.license import LicenseManager

_REPO = Path(__file__).resolve().parent


# ── menu mode keys ───────────────────────────────────────────────────────────

_MODE_SEMANTIC  = "semantic"
_MODE_KEYWORD   = "keyword"
_MODE_COMBO     = "combo"
_MODE_STORY     = "story"
_MODE_FAVORITES = "favorites"
_MODE_SETTINGS  = "settings"

_SETTINGS_KEY_HOTKEY             = "__hotkey__"
_SETTINGS_KEY_LICENSE_BUY        = "__license_buy__"
_SETTINGS_KEY_LICENSE_ENTER      = "__license_enter__"
_SETTINGS_KEY_LICENSE_INFO       = "__license_info__"
_SETTINGS_KEY_LICENSE_DEACTIVATE = "__license_deactivate__"

# ── settings ──────────────────────────────────────────────────────────────────

SETTINGS_FILE = CONFIG_DIR / "picker-settings.json"

def _is_tiling_wm():
    return bool(os.environ.get("I3SOCK") or os.environ.get("SWAYSOCK"))


_DEFAULT_SETTINGS = {
    "notify_on_copy":  True,
    "exit_on_select":  False,
    "semantic_first":  True,
    "show_keyword":    False,
    "show_semantic":   True,
    "show_combo":      True,
    "show_story":      True,
    "floating":        _is_tiling_wm(),
    "frameless":       False,
    "always_on_top":   False,
    "dark_mode":       False,
}

_BOOL_SETTINGS_ITEMS = [
    ("notify_on_copy", "Show notification when copied"),
    ("semantic_first", "Normal search as default (first in menu)"),
    ("show_keyword",   "Show keyword search on front menu"),
    ("show_semantic",  "Show normal search on front menu"),
    ("show_combo",     "Show combo search on front menu"),
    ("show_story",     "Show emoji story on front menu"),
    ("exit_on_select", "Exit app when emoji selected"),
    ("always_on_top",  "Always on top (takes effect on restart)"),
]


def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return {**_DEFAULT_SETTINGS, **data}
    except Exception as e:
        _dbg(f"load_settings failed: {e}")
        return dict(_DEFAULT_SETTINGS)


def save_settings(s):
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except Exception as e:
        _dbg(f"save_settings failed: {e}")



_emoji_thumb_cache: dict = {}

def _render_emoji_thumb(emoji_str, size=48):
    """Render one or two emoji chars into a cached PNG file; return path or None."""
    if emoji_str in _emoji_thumb_cache:
        return _emoji_thumb_cache[emoji_str]
    imgs = [render_emoji_pil(ch, size=size) for ch in emoji_str]
    imgs = [i for i in imgs if i is not None]
    if not imgs:
        return None
    if len(imgs) == 1:
        combined = imgs[0]
    else:
        w = sum(i.width for i in imgs)
        h = max(i.height for i in imgs)
        combined = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        x = 0
        for img in imgs:
            combined.paste(img, (x, (h - img.height) // 2))
            x += img.width
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    path = str(THUMB_DIR / f"_menu_{hashlib.md5(emoji_str.encode()).hexdigest()}.png")
    combined.save(path)
    _emoji_thumb_cache[emoji_str] = path
    return path


def _menu_on_url(val):
    """on_url for the main menu: handles CDN URLs and local emoji fallbacks."""
    if not val:
        return None
    if val.startswith("http"):
        return get_thumb(val)
    return _render_emoji_thumb(val)


_MENU_ICONS = {
    ("tornado",               "mag_right"):              "\U0001f5dd",
    ("fire",                  "slot_machine"):            "🎰",
    ("sunrise_over_mountains","mag_right"):               "🔍",
    ("llama",                 "fire"):                    "📖",
    ("computer",              "face_with_raised_eyebrow"): "⚙️",
}


def _menu_icon(name1, name2):
    return _MENU_ICONS.get((name1, name2)) or _MENU_ICONS.get((name2, name1))


# ── main ──────────────────────────────────────────────────────────────────────

def _open_hotkey_settings():
    import subprocess
    daemon_exe = _REPO / "kitchensearch-daemon.exe"
    daemon_py  = _REPO / "kitchensearch_daemon.py"
    if daemon_exe.exists():
        subprocess.run([str(daemon_exe), "--settings"])
    elif daemon_py.exists():
        subprocess.run([_PYTHON, str(daemon_py), "--settings"])


def _run_settings(picker, settings, lic):
    sel_idx = 0
    tiling = _is_tiling_wm()
    while True:
        base = list(_BOOL_SETTINGS_ITEMS)
        if sys.platform != "win32":
            if tiling:
                base.append(("floating", "Float in tiling layout (takes effect on restart)"))
                base.append(("frameless", "Remove title bar added by WM (takes effect on restart)"))
            else:
                base.append(("floating", "Remove window decorations (takes effect on restart)"))

        item_keys     = [key for key, _ in base]
        display_items = [f"{'[x]' if settings[key] else '[ ]'} {text}" for key, text in base]

        if sys.platform == "win32":
            hotkey = settings.get("hotkey", "Ctrl+Alt+K")
            display_items.append(f"Keyboard shortcut: {hotkey}  (click to change)")
            item_keys.append(_SETTINGS_KEY_HOTKEY)

        if lic.configured():
            licensed = lic.is_licensed()
            if licensed:
                display_items.append(f"License: {lic.status_summary()}  (click for info)")
                item_keys.append(_SETTINGS_KEY_LICENSE_INFO)
                display_items.append("Deactivate license on this device")
                item_keys.append(_SETTINGS_KEY_LICENSE_DEACTIVATE)
            else:
                if lic.checkout_url():
                    display_items.append(
                        "Unlock favorites & dark mode — buy a license")
                    item_keys.append(_SETTINGS_KEY_LICENSE_BUY)
                display_items.append(
                    f"Enter license key  ({lic.status_summary()})")
                item_keys.append(_SETTINGS_KEY_LICENSE_ENTER)

        assert len(item_keys) == len(display_items)

        choice = picker.pick_settings("Settings", display_items, initial_sel=sel_idx)
        if not choice:
            return
        try:
            sel_idx = display_items.index(choice)
        except ValueError:
            sel_idx = 0

        key = item_keys[sel_idx]
        if key == _SETTINGS_KEY_HOTKEY:
            _open_hotkey_settings()
            try:
                updated = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                settings["hotkey"] = updated.get("hotkey", settings.get("hotkey", "Ctrl+Alt+K"))
            except Exception as e:
                _dbg(f"re-read settings after hotkey change failed: {e}")
            continue
        if key == _SETTINGS_KEY_LICENSE_BUY:
            url = lic.checkout_url()
            if url:
                import webbrowser
                webbrowser.open(url)
                entered = picker.ask(
                    "Opening the license checkout in your browser.\n\n"
                    "After purchase, paste your license key below and press "
                    "Enter (or Esc to dismiss):",
                    placeholder="XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX")
                if entered:
                    _ok, msg = lic.activate(entered)
                    picker.message(msg)
            continue
        if key == _SETTINGS_KEY_LICENSE_ENTER:
            entered = picker.ask("Paste your license key, then press Enter:",
                                 placeholder="XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX")
            if entered:
                _ok, msg = lic.activate(entered)
                picker.message(msg)
            continue
        if key == _SETTINGS_KEY_LICENSE_INFO:
            picker.message(
                f"License is {lic.status_summary()}.\n\n"
                "Unlocks the Favorites menu and the dark-mode toggle.\n"
                "Use 'Deactivate license on this device' to free this "
                "activation for another machine.")
            continue
        if key == _SETTINGS_KEY_LICENSE_DEACTIVATE:
            _ok, msg = lic.deactivate()
            picker.message(msg)
            continue
        settings[key] = not settings[key]
        save_settings(settings)


def _run_favorites(picker, settings, entries):
    """Show favorites (starred) + top-100 most-copied below a separator.
    Clicking ♥ toggles the favorite state; the list re-renders on any change."""
    alt_to_entry = {alt: (url, text) for url, alt, text in entries}

    while True:
        favs      = load_favorites()
        fav_alts  = {f["alt"] for f in favs}
        fav_urls  = {f["url"] for f in favs}

        # ── explicit favorites ──────────────────────────────────────────
        fav_section = [(format_label(f["alt"], f["url"], f.get("text", "")), f["url"], 0)
                       for f in favs]

        # ── top 100 most copied (excluding those already starred) ───────
        top_section = []
        for count, alt in top_copied(100):
            if alt in fav_alts:
                continue
            entry = alt_to_entry.get(alt)
            if not entry:
                continue
            url, text = entry
            top_section.append((format_label(alt, url, text), url, count))

        if not fav_section and not top_section:
            picker.message("No favorites yet.\n\n"
                           "Click ♥ on any search or combo result to add it here.")
            return

        all_entries = list(fav_section)
        if top_section:
            if fav_section:
                all_entries.append(("", HEADER_MARKER, "__SEPARATOR__"))
            all_entries.append(("Most copied", HEADER_MARKER, "#888888"))
            all_entries.extend(top_section)

        def _label_to_alt(label):
            m = re.match(r'^\S+', label)
            return m.group(0) if m else label

        def _resolve_fav(label, _favs=favs):
            alt = _label_to_alt(label)
            return next((f for f in _favs if f["alt"] == alt), None)

        def _copy_item(label):
            f = _resolve_fav(label)
            if f:
                url, alt_v = f["url"], f["alt"]
            else:
                entry = alt_to_entry.get(_label_to_alt(label))
                if not entry:
                    return
                url, _ = entry
                alt_v  = _label_to_alt(label)
            path = get_thumb(url)
            if path:
                copy_image_to_clipboard(path)
                record_copy(url, alt_v)
                if settings["notify_on_copy"]:
                    _notify("Copied to clipboard")

        changed = [False]
        def _toggle_fav(label):
            alt = _label_to_alt(label)
            f   = _resolve_fav(label)
            if f:
                url, text = f["url"], f.get("text", "")
            else:
                entry = alt_to_entry.get(alt)
                if not entry:
                    return
                url, text = entry
            now_fav = toggle_favorite(alt, url, text)
            picker.queue_toast("added to favorites" if now_fav else "removed from favorites")
            changed[0] = True
            picker.cancel()

        def _is_fav(label, _fa=fav_alts):
            return _label_to_alt(label) in _fa

        on_sel = None if settings["exit_on_select"] else _copy_item
        result = picker.pick_with_images(
            "Favorites  (♥ to add/remove):", all_entries, get_thumb,
            on_select=on_sel, on_favorite=_toggle_fav, is_favorite_fn=_is_fav)
        if changed[0]:
            continue
        if result and settings["exit_on_select"]:
            _copy_item(result)
        return


def _hotkey_daemon_alive():
    import ctypes
    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "Global\\KitchenSearchDaemon")
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


_TRAY_DAEMON_PID = CONFIG_DIR / "daemon.pid"


def _tray_daemon_alive_darwin():
    if not _TRAY_DAEMON_PID.exists():
        return False
    try:
        pid = int(_TRAY_DAEMON_PID.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        _TRAY_DAEMON_PID.unlink(missing_ok=True)
        return False
    except PermissionError:
        return True
    return True


def _spawn_tray_daemon_darwin():
    import shutil, subprocess
    cmd = shutil.which("kitchensearch_daemon")
    if cmd:
        argv = [cmd]
    else:
        daemon_py = _REPO / "kitchensearch_daemon.py"
        assert daemon_py.exists(), (
            f"cannot find 'kitchensearch_daemon' on PATH or {daemon_py}"
        )
        argv = [_PYTHON, str(daemon_py)]
    subprocess.Popen(
        argv, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _set_process_name(name):
    if sys.platform.startswith("linux"):
        try:
            import ctypes
            ctypes.cdll.LoadLibrary("libc.so.6").prctl(15, name.encode(), 0, 0, 0)
        except Exception:
            pass


def _ensure_desktop_integration():
    if not sys.platform.startswith("linux"):
        return
    import subprocess
    dest = os.path.expanduser("~/.local/share/applications/kitchensearch.desktop")
    template = _REPO / "packaging" / "kitchensearch.desktop"
    if not template.exists():
        return
    install_dir = str(_REPO)
    venv_python = _REPO / ".venv" / "bin" / "python"
    script = _REPO / "kitchensearch.py"
    if venv_python.exists() and script.exists():
        exec_cmd = f"{venv_python} {script}"
    else:
        ui_entry = _REPO / "kitchensearch"
        assert ui_entry.exists(), (
            f"kitchensearch UI entry point not found at {ui_entry}; "
            "Nuitka multidist binary would dispatch to a non-UI mode"
        )
        exec_cmd = str(ui_entry)
    new = (template.read_text()
           .replace("__INSTALL_DIR__", install_dir)
           .replace("__EXEC_CMD__", exec_cmd))
    if os.path.exists(dest):
        with open(dest) as f:
            current = f.read()
    else:
        current = ""
    if current == new:
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as f:
        f.write(new)
    try:
        subprocess.run(["update-desktop-database",
                        os.path.expanduser("~/.local/share/applications")],
                       check=False, capture_output=True)
    except FileNotFoundError:
        pass


def main():
    _set_process_name("kitchensearch")
    _ensure_desktop_integration()
    _dbg("APP_START")
    # KITCHENSEARCH_NO_DAEMON lets the GUI run fully self-contained: skip the
    # background search/hotkey daemons entirely. The picker only needs them for
    # semantic search (looked up lazily, on demand), not to start up — so the
    # window comes up the same with or without them. Used by the test harnesses.
    _no_daemon = bool(os.environ.get("KITCHENSEARCH_NO_DAEMON"))
    if sys.platform == "win32" and not _no_daemon:
        if not _daemon_alive():
            _spawn_daemon()
        if not _hotkey_daemon_alive():
            import subprocess as _sp
            _flags = _sp.CREATE_NO_WINDOW | _sp.CREATE_NEW_PROCESS_GROUP
            _hotkey_exe = _REPO / "kitchensearch-daemon.exe"
            _hotkey_py  = _REPO / "kitchensearch_daemon.py"
            if _hotkey_exe.exists():
                _sp.Popen([str(_hotkey_exe)], creationflags=_flags)
            elif _hotkey_py.exists():
                _sp.Popen([_PYTHON, str(_hotkey_py)], creationflags=_flags)
    # macOS: tray daemon auto-spawn is disabled. Earlier attempt to spawn it
    # from here broke subsequent picker launches under the mac-smoke harness —
    # tests 02+ couldn't find the Tk window by pid, likely because rumps's
    # NSApplication and Tk Aqua collide when both run under the same app name.
    # Users can launch `kitchensearch_daemon` manually for now; we'll re-enable
    # auto-spawn once the underlying conflict is understood.
    settings = load_settings()
    lic = LicenseManager()
    lic.refresh_async()
    if (sys.platform != "win32"
            and not _no_daemon
            and settings.get("semantic_first", True)
            and _has_semantic_models()
            and not _daemon_alive()):
        _spawn_daemon()
    _dbg("APP: TkPicker init start")
    def _on_dark_toggle(dark):
        settings["dark_mode"] = dark
        save_settings(settings)

    picker = TkPicker(
        floating=settings["floating"],
        frameless=settings["frameless"] and not sys.platform == "win32",
        dark=settings.get("dark_mode", False),
        on_dark_toggle=_on_dark_toggle,
        always_on_top=settings.get("always_on_top", False),
    )
    _dbg("APP: TkPicker init done")

    try:
        _dbg("APP: load_index start")
        entries     = load_index()
        _dbg(f"APP: load_index done n_entries={len(entries)}")
        base_index  = build_base_emoji_index(entries)
        while True:
            has_sem  = _has_semantic_models()
            has_data = SEARCH_INDEX.exists()
            _nd      = "  (not downloaded)"

            # build menu entries with combo thumbnail icons
            _dbg("MENU_BUILD_START")
            sem_spec = (_MODE_SEMANTIC, "normal search" + ("" if has_sem else _nd),
                        _menu_icon("sunrise_over_mountains", "mag_right"))
            kw_spec  = (_MODE_KEYWORD,  "keyword search",
                        _menu_icon("tornado", "mag_right"))
            if settings.get("semantic_first", True):
                first_spec, second_spec = sem_spec, kw_spec
                show_first, show_second = settings["show_semantic"], settings["show_keyword"]
            else:
                first_spec, second_spec = kw_spec, sem_spec
                show_first, show_second = settings["show_keyword"], settings["show_semantic"]

            raw_menu = []
            if show_first:
                raw_menu.append(first_spec)
            if show_second:
                raw_menu.append(second_spec)
            if settings["show_combo"]:
                raw_menu.append((_MODE_COMBO, "combine two emojis",
                                 _menu_icon("fire", "slot_machine")))
            if settings["show_story"]:
                raw_menu.append((_MODE_STORY, "emoji story" + ("" if has_data else _nd),
                                 _menu_icon("llama", "fire")))
            # Favorites is a paid feature: only surface it once a license is
            # active. Kept fully hidden (not greyed out) when unlicensed.
            _licensed = lic.is_licensed()
            if _licensed:
                raw_menu.append((_MODE_FAVORITES, "favorites", "❤"))
            raw_menu.append((_MODE_SETTINGS, "settings",
                             _menu_icon("computer", "face_with_raised_eyebrow")))

            menu_entries   = [(label, icon) for _, label, icon in raw_menu]
            _label_to_mode = {label: key for key, label, _ in raw_menu}
            _dbg(f"MENU_BUILD_DONE n={len(menu_entries)}")

            _dbg("MENU_SHOW_START")
            _menu_prompt = "Use the emojikitchen image search directly or select an option below." if settings.get("semantic_first", True) else "Use emojikitchen keyword search directly or select an option below."
            mode = picker.pick_with_images(_menu_prompt, menu_entries, _menu_on_url,
                                           thumb_size=48, preload=True,
                                           placeholder="type to search..." if settings.get("semantic_first", True) else "type to keyword search...",
                                           filter=False,
                                           show_dark_btn=_licensed,
                                           show_back_btn=False)
            _dbg(f"MENU_SHOW_DONE mode={mode!r}")
            if not mode:
                sys.exit(0)

            mode_key = _label_to_mode.get(mode)

            # ── settings ─────────────────────────────────────────────────
            if mode_key == _MODE_SETTINGS:
                _run_settings(picker, settings, lic)
                continue

            # ── favorites (paid) ─────────────────────────────────────────
            if mode_key == _MODE_FAVORITES:
                if lic.is_licensed():
                    _run_favorites(picker, settings, entries)
                continue

            # ── search helpers ────────────────────────────────────────────
            def _run_semantic(q):
                try:
                    raw = query_daemon(q)
                except Exception as exc:
                    picker.message(f"Search error for '{q}':\n{exc}")
                    return None
                if raw is None:
                    picker.message(f"Search daemon failed to start.\nSee {DAEMON_LOG} for details.")
                    return None
                if raw == "loading":
                    if not picker.show_model_loading_progress():
                        return None
                    try:
                        raw = query_daemon(q)
                    except Exception as exc:
                        picker.message(f"Search error for '{q}':\n{exc}")
                        return None
                    if raw is None:
                        picker.message(f"Search daemon failed to start.\nSee {DAEMON_LOG} for details.")
                        return None
                if not raw:
                    picker.message(f"No results for '{q}'")
                    return None
                _a2t = {alt: text for _, alt, text in entries}
                return ([(rank, alt, url, _a2t.get(alt, "")) for rank, alt, url, _ in raw],
                        [], f"'{q}' (normal)")

            def _run_keyword(q):
                try:
                    r = search(entries, q)
                except Exception as exc:
                    picker.message(f"Search error for '{q}':\n{exc}")
                    return None
                if not r:
                    picker.message(f"No results for '{q}'")
                    return None
                pats = [re.compile(r'\b' + re.escape(w) + r'\b') for w in q.lower().split()]
                return r, pats, f"'{q}'"

            # A click on a menu row must resolve to a known mode; only typed
            # text is allowed to bypass _label_to_mode and fall through to a
            # search. The legacy `or mode_key is None` fallback hid label
            # mismatches as silent searches (the suspected old-Windows bug).
            assert picker.result_typed or mode_key is not None, (
                f"clicked menu label {mode!r} did not map to a known mode; "
                f"known labels: {list(_label_to_mode.keys())!r}"
            )

            # ── typed text → default search ───────────────────────────────
            if picker.result_typed:
                query = mode
                if settings.get("semantic_first", True) and has_sem:
                    if not _daemon_alive():
                        _spawn_daemon()
                    out = _run_semantic(query)
                    if out is None:
                        continue
                    is_semantic = True
                else:
                    out = _run_keyword(query)
                    if out is None:
                        continue
                    is_semantic = False
                results, patterns, query_label = out
                mode = "_results"

            # ── story ────────────────────────────────────────────────────
            elif mode_key == _MODE_STORY:
                text = picker.ask_story("story text:")
                if not text:
                    continue
                _story_cmd = ([str(STORY_BIN)] if STORY_BIN.exists() else [_PYTHON, str(STORY_PY)]) + ["--output", str(STORY_OUT), text]
                err = picker.show_story_progress(_story_cmd)
                if err:
                    picker.message(err)
                    continue
                action = picker.show_image(
                    "Emoji story  (Enter to copy  |  Esc to cancel)",
                    str(STORY_OUT))
                if action == "copy":
                    copy_image_to_clipboard(str(STORY_OUT))
                    if settings["notify_on_copy"]:
                        _notify("Story copied to clipboard")
                    if settings["exit_on_select"]:
                        break
                    picker.queue_toast("story copied")
                continue

            # ── combo ────────────────────────────────────────────────────
            elif mode_key == _MODE_COMBO:
                _dbg("COMBO_SELECTED")
                _dbg("COMBO: pick_first_emoji start")
                first = pick_base_emoji(base_index, "first emoji:", picker)
                _dbg(f"COMBO: pick_first_emoji done first={first!r}")
                if not first:
                    continue
                emoji1, term1 = first
                _dbg(f"COMBO: pick_second_emoji start term1={term1!r}")
                second = pick_base_emoji(
                    base_index,
                    f"second emoji (+ {emoji1}{' ' + term1 if emoji1 else term1}):",
                    picker)
                _dbg(f"COMBO: pick_second_emoji done second={second!r}")
                if not second:
                    continue
                emoji2, term2 = second
                _dbg(f"COMBO: search start query={term1!r}+{term2!r}")
                results = search(entries, f"{term1} {term2}")
                _dbg(f"COMBO: search done n_results={len(results)}")
                if not results:
                    picker.message(f"No results for '{term1} {term2}'")
                    continue
                exact_alts = {f"{term1}-{term2}".lower(), f"{term2}-{term1}".lower()}
                exact   = [r for r in results if r[1].lower() in exact_alts]
                rest    = [r for r in results if r[1].lower() not in exact_alts]
                patterns    = [re.compile(re.escape(term1)), re.compile(re.escape(term2))]
                query_label = f"'{term1}+{term2}'"

                _ui_assets = UI_ASSETS_DIR
                _match_img    = str(_ui_assets / "face_holding_back_tears_turtle.png")
                _no_match_img = str(_ui_assets / "cry_turtle.png")
                all_combo = exact + rest

                def _copy_combo(label, _all=all_combo):
                    m = re.match(r'^\S+', label)
                    sel_alt = m.group(0) if m else label
                    for _, alt, url, _ in _all:
                        if alt == sel_alt:
                            path = get_thumb(url)
                            if path:
                                copy_image_to_clipboard(path)
                                record_copy(url, alt)
                                if settings["notify_on_copy"]:
                                    _notify("Copied to clipboard")
                            break

                def _toggle_fav_combo(label, _all=all_combo):
                    m = re.match(r'^\S+', label)
                    sel_alt = m.group(0) if m else label
                    for _, alt, url, text in _all:
                        if alt == sel_alt:
                            now_fav = toggle_favorite(alt, url, text)
                            picker.queue_toast("added to favorites" if now_fav
                                               else "removed from favorites")
                            break

                on_sel_combo = None if settings["exit_on_select"] else _copy_combo
                on_fav_combo = _toggle_fav_combo if lic.is_licensed() else None
                if lic.is_licensed():
                    _fav_alts_combo = {f["alt"] for f in load_favorites()}
                    def _is_fav_combo(label, _fa=_fav_alts_combo):
                        m = re.match(r'^\S+', label)
                        return (m.group(0) if m else label) in _fa
                else:
                    _is_fav_combo = None
                rest_entries = [(format_label(alt, url, text), url, ts)
                                for ts, alt, url, text in rest]
                if exact:
                    n_fixed = 2 + len(exact)
                    similar_init = f"1-{min(BATCH_SIZE, len(rest))} of {len(rest)}"
                    count = f"({len(exact)} exact, {similar_init} similar)"
                    combo_entries = [
                        ("Match found!", HEADER_MARKER, "#228844", _match_img),
                        *[(format_label(alt, url, text), url, ts)
                          for ts, alt, url, text in exact],
                        ("Other similar combos", HEADER_MARKER, "#999999"),
                        *rest_entries,
                    ]
                    def _combo_prompt(loaded, total, _ne=len(exact), _nr=len(rest)):
                        sim = max(0, min(loaded - (2 + _ne), _nr))
                        return f"{query_label} ({_ne} exact, 1-{sim} of {_nr} similar):"
                else:
                    n_fixed = 2
                    similar_init = f"1-{min(BATCH_SIZE, len(rest))} of {len(rest)}" if rest else "0"
                    count = f"({similar_init} similar)"
                    combo_entries = [
                        ("Match could not be found", HEADER_MARKER, "#8B0000", _no_match_img),
                        ("Other similar combos", HEADER_MARKER, "#999999"),
                        *rest_entries,
                    ]
                    def _combo_prompt(loaded, total, _nr=len(rest)):
                        sim = max(0, min(loaded - 2, _nr))
                        return f"{query_label} (1-{sim} of {_nr} similar):"
                _dbg(f"COMBO: pick_with_images n_entries={len(combo_entries)}")
                result = picker.pick_with_images(
                    f"{query_label} {count}:", combo_entries, get_thumb,
                    on_select=on_sel_combo, patterns=patterns,
                    prompt_fn=_combo_prompt, on_favorite=on_fav_combo,
                    is_favorite_fn=_is_fav_combo)
                _dbg(f"COMBO: pick_with_images done result={result!r}")
                if result and settings["exit_on_select"]:
                    _copy_combo(result)

                _trim_thumb_cache()
                if settings["exit_on_select"] and result:
                    break
                continue

            # ── semantic ─────────────────────────────────────────────────
            elif mode_key == _MODE_SEMANTIC:
                if not has_sem:
                    picker.message("Search data not available.")
                    continue
                if not _daemon_alive():
                    _spawn_daemon()
                query = picker.ask_with_loading_bar("emoji search:", placeholder='try e.g. "ski chicken" then press enter')
                if not query:
                    continue
                out = _run_semantic(query)
                if out is None:
                    continue
                results, patterns, query_label = out
                is_semantic = True

            # ── keyword ──────────────────────────────────────────────────
            elif mode_key == _MODE_KEYWORD:
                query = picker.ask("emoji search:", placeholder='try e.g. "ski chicken" then press enter')
                if not query:
                    continue
                out = _run_keyword(query)
                if out is None:
                    continue
                results, patterns, query_label = out
                is_semantic = False

            else:
                _dbg(f"unexpected mode_key={mode_key!r} mode={mode!r}")
                continue

            # ── results ──────────────────────────────────────────────────
            while True:
                count = f"(1-{min(BATCH_SIZE, len(results))} of {len(results)})"
                icon_entries = [(format_label(alt, url, text), url, ts)
                                for ts, alt, url, text in results]

                def _copy_selected(label, _results=results):
                    m = re.match(r'^\S+', label)
                    sel_alt = m.group(0) if m else label
                    for _, alt, url, _ in _results:
                        if alt == sel_alt:
                            path = get_thumb(url)
                            if path:
                                copy_image_to_clipboard(path)
                                record_copy(url, alt)
                                if settings["notify_on_copy"]:
                                    _notify("Copied to clipboard")
                            break

                def _toggle_fav_result(label, _results=results):
                    m = re.match(r'^\S+', label)
                    sel_alt = m.group(0) if m else label
                    for _, alt, url, text in _results:
                        if alt == sel_alt:
                            now_fav = toggle_favorite(alt, url, text)
                            picker.queue_toast("added to favorites" if now_fav
                                               else "removed from favorites")
                            break

                on_sel = None if settings["exit_on_select"] else _copy_selected
                on_fav = _toggle_fav_result if lic.is_licensed() else None
                if lic.is_licensed():
                    _fav_alts_res = {f["alt"] for f in load_favorites()}
                    def _is_fav_res(label, _fa=_fav_alts_res):
                        m = re.match(r'^\S+', label)
                        return (m.group(0) if m else label) in _fa
                else:
                    _is_fav_res = None
                _ql = query_label
                def _make_prompt(loaded, total, _ql=_ql):
                    return f"{_ql} (1-{loaded} of {total}):"
                result = picker.pick_with_images(
                    f"{query_label} {count}:", icon_entries, get_thumb,
                    on_select=on_sel, patterns=patterns, show_research_cb=True,
                    prompt_fn=_make_prompt, on_favorite=on_fav,
                    is_favorite_fn=_is_fav_res)

                if picker.result_typed and result:
                    query = result
                    out = _run_semantic(query) if is_semantic else _run_keyword(query)
                    if out is None:
                        break
                    results, patterns, query_label = out
                    continue
                if result and settings["exit_on_select"]:
                    _copy_selected(result)
                break

            _trim_thumb_cache()
            if settings["exit_on_select"] and result:
                break

    finally:
        picker.destroy()


if __name__ == "__main__":
    main()
