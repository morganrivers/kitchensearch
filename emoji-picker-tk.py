#!/usr/bin/env python3
"""
Emoji kitchen picker - tkinter UI replacing rofi.
Results appear one-by-one as thumbnails download. Borderless, half-screen, centered.

Bind in i3 config:
  bindsym $mod+shift+e exec --no-startup-id python3 ~/.local/bin/emoji-picker-tk.py
"""
import os, sys, re, json, hashlib, webbrowser, time
from PIL import Image
from picker_utils import (
    DATA_DIR, UI_ASSETS_DIR, CACHE_DIR, CONFIG_DIR, THUMB_DIR,
    SEARCH_INDEX, _REPO, _PYTHON,
    STORY_OUT, STORY_PY, STORY_BIN,
    DAEMON_LOG,
    BATCH_SIZE, LOAD_MORE, MAX_RESULTS, HEADER_MARKER,
    _has_semantic_models, _notify, _dbg,
    load_index, search, build_base_emoji_index, format_label,
    get_thumb, render_emoji_pil,
    copy_image_to_clipboard,
    query_daemon,
    _trim_thumb_cache, _spawn_daemon, _daemon_alive,
    get_buymeacoffee_url, get_banner_config, _next_tuesday_ts,
)
from picker_ui import (
    TkPicker, pick_base_emoji,
)


# ── settings ──────────────────────────────────────────────────────────────────

SETTINGS_FILE = CONFIG_DIR / "picker-settings.json"

_DEFAULT_SETTINGS = {
    "notify_on_copy":  True,
    "exit_on_select":  True,
    "semantic_first":  True,
    "show_keyword":    True,
    "show_semantic":   True,
    "show_combo":      True,
    "show_story":      True,
    "floating":        True,
    "frameless":       False,
    "dark_mode":       False,
}


def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return {**_DEFAULT_SETTINGS, **data}
    except Exception:
        return dict(_DEFAULT_SETTINGS)


def save_settings(s):
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except Exception:
        pass


def _find_combo_url(entries, name1, name2):
    for url, alt, _ in entries:
        parts = alt.split("-", 1)
        if len(parts) == 2:
            if (parts[0] == name1 and parts[1] == name2) or \
               (parts[0] == name2 and parts[1] == name1):
                return url
    return None


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


_MENU_FALLBACKS = {
    ("tornado",               "mag_right"):             "🌪️🔍",
    ("fire",                  "slot_machine"):           "🔥🎰",
    ("sunrise_over_mountains","mag_right"):              "🌄🔍",
    ("llama",                 "fire"):                   "🦙🔥",
    ("computer",              "face_with_raised_eyebrow"): "💻🤨",
}


def _find_combo_url_or_emoji(entries, name1, name2):
    url = _find_combo_url(entries, name1, name2)
    if url is not None:
        return url
    return _MENU_FALLBACKS.get((name1, name2)) or _MENU_FALLBACKS.get((name2, name1))


# ── main ──────────────────────────────────────────────────────────────────────

def _open_hotkey_settings():
    import subprocess
    daemon_exe = _REPO / "kitchensearch-daemon.exe"
    daemon_py  = _REPO / "kitchensearch-daemon.py"
    if daemon_exe.exists():
        subprocess.run([str(daemon_exe), "--settings"])
    elif daemon_py.exists():
        subprocess.run([_PYTHON, str(daemon_py), "--settings"])


def _run_settings(picker, settings):
    sel_idx = 0
    while True:
        def _lbl(key, text):
            return f"{'[x]' if settings[key] else '[ ]'} {text}"
        items = [
            _lbl("notify_on_copy",  "Show notification when copied"),
            _lbl("semantic_first",  "Semantic search as default (first in menu)"),
            _lbl("show_keyword",    "Show keyword search on front menu"),
            _lbl("show_semantic",   "Show semantic search on front menu"),
            _lbl("show_combo",      "Show combo search on front menu"),
            _lbl("show_story",      "Show emoji story on front menu"),
            _lbl("exit_on_select",  "Exit app when emoji selected"),
            _lbl("floating",        "Start as floating window (takes effect on restart)"),
        ]
        if not sys.platform == "win32":
            items.append(_lbl("frameless", "Start frameless & no title bar (takes effect on restart)"))
        if sys.platform == "win32":
            hotkey = settings.get("hotkey", "Ctrl+Alt+K")
            items.append(f"Keyboard shortcut: {hotkey}  (click to change)")
        if "hide_ads" in settings and not (time.time() < settings.get("snooze_until", 0)):
            items.append(_lbl("hide_ads", "Don't show support banner"))
        choice = picker.pick_settings("Settings", items, initial_sel=sel_idx)
        if not choice:
            return
        try:
            sel_idx = items.index(choice)
        except ValueError:
            sel_idx = 0
        if "notification when copied"    in choice: settings["notify_on_copy"]  = not settings["notify_on_copy"]
        elif "Semantic search as default" in choice: settings["semantic_first"]  = not settings["semantic_first"]
        elif "Exit app when emoji selected" in choice: settings["exit_on_select"] = not settings["exit_on_select"]
        elif "keyword search"  in choice: settings["show_keyword"]  = not settings["show_keyword"]
        elif "semantic"        in choice: settings["show_semantic"] = not settings["show_semantic"]
        elif "combo"           in choice: settings["show_combo"]    = not settings["show_combo"]
        elif "emoji story"     in choice: settings["show_story"]    = not settings["show_story"]
        elif "floating window" in choice: settings["floating"]      = not settings["floating"]
        elif "no title bar"    in choice: settings["frameless"]     = not settings["frameless"]
        elif "support banner"  in choice: settings["hide_ads"]      = not settings["hide_ads"]
        elif sys.platform == "win32" and "Keyboard shortcut:" in choice:
            _open_hotkey_settings()
            try:
                updated = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                settings["hotkey"] = updated.get("hotkey", settings.get("hotkey", "Ctrl+Alt+K"))
            except Exception:
                pass
            continue
        save_settings(settings)


def _hotkey_daemon_alive():
    import ctypes
    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "Global\\KitchenSearchDaemon")
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


def main():
    _dbg("APP_START")
    if sys.platform == "win32":
        if not _daemon_alive():
            _spawn_daemon()
        if not _hotkey_daemon_alive():
            import subprocess as _sp
            _flags = _sp.CREATE_NO_WINDOW | _sp.CREATE_NEW_PROCESS_GROUP
            _hotkey_exe = _REPO / "kitchensearch-daemon.exe"
            _hotkey_py  = _REPO / "kitchensearch-daemon.py"
            if _hotkey_exe.exists():
                _sp.Popen([str(_hotkey_exe)], creationflags=_flags)
            elif _hotkey_py.exists():
                _sp.Popen([_PYTHON, str(_hotkey_py)], creationflags=_flags)
    settings = load_settings()
    _dbg("APP: TkPicker init start")
    def _on_dark_toggle(dark):
        settings["dark_mode"] = dark
        save_settings(settings)

    picker = TkPicker(
        floating=settings["floating"],
        frameless=settings["frameless"] and not sys.platform == "win32",
        dark=settings.get("dark_mode", False),
        on_dark_toggle=_on_dark_toggle,
    )
    _dbg("APP: TkPicker init done")

    try:
        _dbg("APP: load_index start")
        entries     = load_index()
        _dbg(f"APP: load_index done n_entries={len(entries)}")
        base_index  = build_base_emoji_index(entries)
        while True:
            has_sem     = _has_semantic_models()
            has_data    = SEARCH_INDEX.exists()
            sem_label   = "semantic search (better, slow)"
            story_label = "emoji story"
            _nd         = "  (not downloaded)"

            # build menu entries with combo thumbnail icons
            _dbg("MENU_BUILD_START")
            menu_entries = []
            sem_entry = (sem_label + ("" if has_sem else _nd),
                         _find_combo_url_or_emoji(entries, "sunrise_over_mountains", "mag_right"))
            kw_entry  = ("keyword search",
                         _find_combo_url_or_emoji(entries, "tornado", "mag_right"))
            if settings.get("semantic_first", True):
                first, second = sem_entry, kw_entry
                show_first, show_second = settings["show_semantic"], settings["show_keyword"]
            else:
                first, second = kw_entry, sem_entry
                show_first, show_second = settings["show_keyword"], settings["show_semantic"]
            if show_first:
                menu_entries.append(first)
            if show_second:
                menu_entries.append(second)
            if settings["show_combo"]:
                menu_entries.append(("combo",
                                     _find_combo_url_or_emoji(entries, "fire", "slot_machine")))
            if settings["show_story"]:
                menu_entries.append((story_label + ("" if has_data else _nd),
                                     _find_combo_url_or_emoji(entries, "llama", "fire")))
            menu_entries.append(("settings",
                                 _find_combo_url_or_emoji(entries, "computer", "face_with_raised_eyebrow")))
            _dbg(f"MENU_BUILD_DONE n={len(menu_entries)}")

            _dbg("MENU_SHOW_START")
            snoozed = time.time() < settings.get("snooze_until", 0)
            force_banner = os.environ.get("KITCHENSEARCH_SHOW_BANNER") == "1"
            banner = None if (settings.get("hide_ads") or snoozed) and not force_banner else get_banner_config()
            if banner is not None and "hide_ads" not in settings:
                settings["hide_ads"] = False
                save_settings(settings)
            mode = picker.pick_with_images("Use quick keyword search directly or select an option below.", menu_entries, _menu_on_url,
                                           thumb_size=48, preload=True,
                                           placeholder="type to semantic search..." if settings.get("semantic_first", True) else "type to keyword search...",
                                           filter=False, banner=banner,
                                           show_dark_btn=True)
            _dbg(f"MENU_SHOW_DONE mode={mode!r}")
            if not mode:
                sys.exit(0)

            if mode == TkPicker.BMC_BANNER_LABEL:
                settings["hide_ads"] = True
                save_settings(settings)
                webbrowser.open(get_buymeacoffee_url())
                continue
            if mode == TkPicker.BMC_SNOOZE_LABEL:
                settings["snooze_until"] = _next_tuesday_ts()
                save_settings(settings)
                continue
            if mode == TkPicker.BMC_DISMISS_LABEL:
                settings["hide_ads"] = True
                save_settings(settings)
                continue

            # ── settings ─────────────────────────────────────────────────
            if mode == "settings":
                _run_settings(picker, settings)
                continue

            # ── typed text → default search ───────────────────────────────
            mode_names = {e[0] for e in menu_entries}
            if picker.result_typed or mode not in mode_names:
                query = mode
                if settings.get("semantic_first", True) and has_sem:
                    if not _daemon_alive():
                        _spawn_daemon()
                    results = query_daemon(query)
                    if results is None:
                        picker.message(f"Search daemon failed to start.\nSee {DAEMON_LOG} for details.")
                        continue
                    if results == "loading":
                        picker.message("Search daemon is still loading. Please try again in a moment.")
                        continue
                    alt_to_text = {alt: text for _, alt, text in entries}
                    results = [(rank, alt, url, alt_to_text.get(alt, ""))
                               for rank, alt, url, _ in results]
                    patterns    = []
                    query_label = f"'{query}' (semantic)"
                else:
                    results = search(entries, query)
                    if not results:
                        picker.message(f"No results for '{query}'")
                        continue
                    patterns    = [re.compile(r'\b' + re.escape(w) + r'\b')
                                    for w in query.lower().split()]
                    query_label = f"'{query}'"
                mode = "_results"

            # ── story ────────────────────────────────────────────────────
            elif mode.startswith(story_label):
                text = picker.ask("story text:")
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
            elif mode == "combo":
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

                _ui_assets = _REPO / "data" / "ui_assets"
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
                                if settings["notify_on_copy"]:
                                    _notify("Copied to clipboard")
                            break

                on_sel_combo = None if settings["exit_on_select"] else _copy_combo
                offset = 0
                while True:
                    batch_rest = rest[0:offset + BATCH_SIZE]
                    if len(rest) > 0:
                        similar_range = f"1-{len(batch_rest)} of {len(rest)}"
                    else:
                        similar_range = "0"
                    if exact:
                        count = f"({len(exact)} exact, {similar_range} similar)"
                        combo_entries = [
                            ("Match found!", HEADER_MARKER, "#228844", _match_img),
                            *[(format_label(alt, url, text), url, ts)
                              for ts, alt, url, text in exact],
                            ("Other similar combos", HEADER_MARKER, "#999999"),
                            *[(format_label(alt, url, text), url, ts)
                              for ts, alt, url, text in batch_rest],
                        ]
                    else:
                        count = f"({similar_range} similar)"
                        combo_entries = [
                            ("Match could not be found", HEADER_MARKER, "#8B0000", _no_match_img),
                            ("Other similar combos", HEADER_MARKER, "#999999"),
                            *[(format_label(alt, url, text), url, ts)
                              for ts, alt, url, text in batch_rest],
                        ]
                    if offset + BATCH_SIZE < len(rest):
                        combo_entries.append((LOAD_MORE, None))
                    _dbg(f"COMBO: pick_with_images offset={offset} n_entries={len(combo_entries)}")
                    result = picker.pick_with_images(
                        f"{query_label} {count}:", combo_entries, get_thumb,
                        on_select=on_sel_combo, patterns=patterns)
                    _dbg(f"COMBO: pick_with_images done result={result!r}")
                    if result == LOAD_MORE:
                        offset += BATCH_SIZE
                        continue
                    if result and settings["exit_on_select"]:
                        _copy_combo(result)
                    break

                _trim_thumb_cache()
                if settings["exit_on_select"] and result and result != LOAD_MORE:
                    break
                continue

            # ── semantic ─────────────────────────────────────────────────
            elif mode.startswith(sem_label):
                if not has_sem:
                    picker.message("Semantic search data not available.")
                    continue
                if not _daemon_alive():
                    _spawn_daemon()
                query = picker.ask_with_loading_bar("emoji search (semantic):")
                if not query:
                    continue
                results = query_daemon(query)
                if results is None:
                    picker.message(
                        f"Search daemon failed to start.\nSee {DAEMON_LOG} for details.")
                    continue
                if results == "loading":
                    picker.message("Search daemon is still loading. Please try again in a moment.")
                    continue
                alt_to_text = {alt: text for _, alt, text in entries}
                results = [(rank, alt, url, alt_to_text.get(alt, ""))
                           for rank, alt, url, _ in results]
                patterns    = []
                query_label = f"'{query}' (semantic)"

            # ── keyword ──────────────────────────────────────────────────
            else:
                query = picker.ask("emoji search:")
                if not query:
                    continue
                results = search(entries, query)
                if not results:
                    picker.message(f"No results for '{query}'")
                    continue
                patterns    = [re.compile(r'\b' + re.escape(w) + r'\b')
                                for w in query.lower().split()]
                query_label = f"'{query}'"

            # ── results ──────────────────────────────────────────────────
            offset = 0
            while True:
                batch = results[0:offset + BATCH_SIZE]
                count = f"(1-{len(batch)} of {len(results)})"
                icon_entries = [(format_label(alt, url, text), url, ts)
                                for ts, alt, url, text in batch]
                if offset + BATCH_SIZE < len(results):
                    icon_entries.append((LOAD_MORE, None))

                def _copy_selected(label, _results=results):
                    m = re.match(r'^\S+', label)
                    sel_alt = m.group(0) if m else label
                    for _, alt, url, _ in _results:
                        if alt == sel_alt:
                            path = get_thumb(url)
                            if path:
                                copy_image_to_clipboard(path)
                                if settings["notify_on_copy"]:
                                    _notify("Copied to clipboard")
                            break

                on_sel = None if settings["exit_on_select"] else _copy_selected
                result = picker.pick_with_images(
                    f"{query_label} {count}:", icon_entries, get_thumb,
                    on_select=on_sel, patterns=patterns)

                if result == LOAD_MORE:
                    offset += BATCH_SIZE
                    continue
                if result and settings["exit_on_select"]:
                    _copy_selected(result)
                break

            _trim_thumb_cache()
            if settings["exit_on_select"] and result and result != LOAD_MORE:
                break

    finally:
        picker.destroy()


if __name__ == "__main__":
    main()
