#!/usr/bin/env python3
"""
Emoji kitchen picker - tkinter UI replacing rofi.
Results appear one-by-one as thumbnails download. Borderless, half-screen, centered.

Bind in i3 config:
  bindsym $mod+shift+e exec --no-startup-id python3 ~/.local/bin/emoji-picker-tk.py
"""
import sys, re, json, hashlib, webbrowser 
from PIL import Image
from picker_utils import (  
    DATA_DIR, UI_ASSETS_DIR, CACHE_DIR, THUMB_DIR,      
    SEARCH_INDEX, _REPO, _PYTHON,  
    STORY_OUT, STORY_PY, STORY_BIN,
    DAEMON_LOG,      
    BATCH_SIZE, LOAD_MORE, MAX_RESULTS, HEADER_MARKER,
    _has_semantic_models, _notify, 
    load_index, search, build_base_emoji_index, format_label,
    get_thumb, render_emoji_pil,   
    copy_image_to_clipboard,     
    query_daemon,    
    _trim_thumb_cache, _spawn_daemon, _daemon_alive,   
)      
from picker_ui import TkPicker, pick_base_emoji    


# ── settings ──────────────────────────────────────────────────────────────────

SETTINGS_FILE = CACHE_DIR / "picker-settings.json"

_DEFAULT_SETTINGS = {
    "exit_on_select": True,
    "show_keyword":   True,
    "show_combo":     True,
    "show_semantic":  True,
    "show_story":     True,
    "floating":       True,
    "frameless":      False,
}


def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        return {**_DEFAULT_SETTINGS, **data}
    except Exception:
        return dict(_DEFAULT_SETTINGS)


def save_settings(s):
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(s, indent=2))
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

def _run_settings(picker, settings):
    sel_idx = 0
    while True:
        def _lbl(key, text):
            return f"{'[x]' if settings[key] else '[ ]'} {text}"
        items = [
            _lbl("show_keyword",   "Show keyword search on front menu"),
            _lbl("show_combo",     "Show combo search on front menu"),
            _lbl("show_semantic",  "Show semantic search on front menu"),
            _lbl("show_story",     "Show emoji story on front menu"),
            _lbl("exit_on_select", "Exit app when emoji selected"),
            _lbl("floating",       "Start as floating window (takes effect on restart)"),
            _lbl("frameless",      "Start frameless & no title bar (takes effect on restart)"),
            "buy me a coffee ☕",
        ]
        choice = picker.pick_settings("Settings", items, initial_sel=sel_idx)
        if not choice:
            return
        try:
            sel_idx = items.index(choice)
        except ValueError:
            sel_idx = 0
        if "Exit app when emoji selected"   in choice: settings["exit_on_select"] = not settings["exit_on_select"]
        elif "keyword search" in choice: settings["show_keyword"]   = not settings["show_keyword"]
        elif "combo"          in choice: settings["show_combo"]     = not settings["show_combo"]
        elif "semantic"       in choice: settings["show_semantic"]  = not settings["show_semantic"]
        elif "emoji story"    in choice: settings["show_story"]     = not settings["show_story"]
        elif "floating window" in choice: settings["floating"]      = not settings["floating"]
        elif "no title bar"   in choice: settings["frameless"]      = not settings["frameless"]
        elif "buy me a coffee" in choice:
            webbrowser.open("https://buymeacoffee.com/morganrivers")
            return
        save_settings(settings)


def main():
    settings = load_settings()
    picker   = TkPicker(floating=settings["floating"], frameless=settings["frameless"])

    try:
        entries     = load_index()
        while True:
            has_sem     = _has_semantic_models()
            has_data    = SEARCH_INDEX.exists()
            sem_label   = "semantic search (better, slow)"
            story_label = "emoji story"
            _nd         = "  (not downloaded)"

            # build menu entries with combo thumbnail icons
            menu_entries = []
            if settings["show_keyword"]:
                menu_entries.append(("keyword search",
                                     _find_combo_url_or_emoji(entries, "tornado", "mag_right")))
            if settings["show_combo"]:
                menu_entries.append(("combo",
                                     _find_combo_url_or_emoji(entries, "fire", "slot_machine")))
            if settings["show_semantic"]:
                menu_entries.append((sem_label + ("" if has_sem else _nd),
                                     _find_combo_url_or_emoji(entries, "sunrise_over_mountains", "mag_right")))
            if settings["show_story"]:
                menu_entries.append((story_label + ("" if has_data else _nd),
                                     _find_combo_url_or_emoji(entries, "llama", "fire")))
            menu_entries.append(("settings",
                                 _find_combo_url_or_emoji(entries, "computer", "face_with_raised_eyebrow")))

            mode = picker.pick_with_images("Use quick keyword search directly or select an option below.", menu_entries, _menu_on_url,
                                           thumb_size=48)
            if not mode:
                sys.exit(0)

            # ── settings ─────────────────────────────────────────────────
            if mode == "settings":
                _run_settings(picker, settings)
                continue

            # ── typed text → keyword search ───────────────────────────────
            mode_names = {e[0] for e in menu_entries}
            if mode not in mode_names:
                query   = mode
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
                    _notify("Story copied to clipboard")
                continue

            # ── combo ────────────────────────────────────────────────────
            elif mode == "combo":
                base_index = build_base_emoji_index(entries)
                first = pick_base_emoji(base_index, "first emoji:", picker)
                if not first:
                    continue
                emoji1, term1 = first
                second = pick_base_emoji(
                    base_index,
                    f"second emoji (+ {emoji1}{' ' + term1 if emoji1 else term1}):",
                    picker)
                if not second:
                    continue
                emoji2, term2 = second
                results = search(entries, f"{term1} {term2}")
                if not results:
                    picker.message(f"No results for '{term1} {term2}'")
                    continue
                exact_alts = {f"{term1}-{term2}".lower(), f"{term2}-{term1}".lower()}
                exact   = [r for r in results if r[1].lower() in exact_alts]
                rest    = [r for r in results if r[1].lower() not in exact_alts]
                patterns    = [re.compile(re.escape(term1)), re.compile(re.escape(term2))]
                query_label = f"'{term1}+{term2}'"

                # Build combo icon_entries with match/no-match header
                _ui_assets = _REPO / "data" / "ui_assets"
                _match_img    = str(_ui_assets / "face_holding_back_tears_turtle.png")
                _no_match_img = str(_ui_assets / "cry_turtle.png")
                all_combo = exact + rest
                if exact:
                    combo_entries = [
                        ("Match found!", HEADER_MARKER, "#228844", _match_img),
                        *[(format_label(alt, url, text), url, ts)
                          for ts, alt, url, text in exact],
                        ("Other similar combos", HEADER_MARKER, "#999999"),
                        *[(format_label(alt, url, text), url, ts)
                          for ts, alt, url, text in rest],
                    ]
                else:
                    combo_entries = [
                        ("Match could not be found", HEADER_MARKER, "#8B0000", _no_match_img),
                        ("Other similar combos", HEADER_MARKER, "#999999"),
                        *[(format_label(alt, url, text), url, ts)
                          for ts, alt, url, text in rest],
                    ]
                count = f"({len(exact)} exact, {len(rest)} similar)"

                def _copy_combo(label, _all=all_combo):
                    m = re.match(r'^\S+', label)
                    sel_alt = m.group(0) if m else label
                    for _, alt, url, _ in _all:
                        if alt == sel_alt:
                            path = get_thumb(url)
                            if path:
                                copy_image_to_clipboard(path)
                                _notify("Copied to clipboard")
                            break

                on_sel_combo = None if settings["exit_on_select"] else _copy_combo
                result = picker.pick_with_images(
                    f"{query_label} {count}:", combo_entries, get_thumb,
                    on_select=on_sel_combo, patterns=patterns)
                if result and settings["exit_on_select"]:
                    _copy_combo(result)
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
                if not results or results == "loading":
                    picker.message(
                        f"Search daemon failed to start.\nSee {DAEMON_LOG} for details.")
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
                batch = results[offset:offset + BATCH_SIZE]
                count = f"({offset+1}-{offset+len(batch)} of {len(results)})"
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
