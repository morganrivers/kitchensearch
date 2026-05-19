import sys, os, re, json, hashlib, shutil, socket, subprocess                                                                                                                                
import time, urllib.request, threading, random as _random, traceback                                                                                                                         
from pathlib import Path                                                                                                                                                                     
                                                                                                                                                                                             
try:                                                                                                                                                                                       
    from screeninfo import get_monitors as _get_monitors
except ImportError:
    _get_monitors = None

from PIL import Image, ImageDraw, ImageFont as _ImageFont                                                                                                                           


# ── debug log ─────────────────────────────────────────────────────────────────
_DBG_LOG_PATH = Path("/tmp/emojipicker-debug.log")
_DBG_LOG_PATH.write_text("")  # truncate on each launch
_dbg_lock = threading.Lock()

def _dbg(msg, include_tb=False):
    ts = time.strftime("%H:%M:%S") + f".{int(time.time()*1000)%1000:03d}"
    tid = threading.get_ident()
    lines = [f"[{ts}][tid={tid}] {msg}"]
    if include_tb:
        tb_lines = traceback.format_stack(limit=8)
        lines.append("  STACK: " + " | ".join(l.strip() for l in tb_lines[:-1]))
    with _dbg_lock:
        with open(_DBG_LOG_PATH, "a") as f:
            f.write("\n".join(lines) + "\n")


_REPO        = Path(__file__).resolve().parent
if not (_REPO / "data").exists():
    _REPO = Path(sys.executable).resolve().parent
DATA_DIR     = _REPO / "data" / "embeddings"
UI_ASSETS_DIR = _REPO / "data" / "ui_assets"
CACHE_DIR    = _REPO / "data" / "cache"
_VENV_PY     = _REPO / ".venv" / "bin" / "python3"
_PYTHON      = str(_VENV_PY) if _VENV_PY.exists() else sys.executable
SEARCH_INDEX = UI_ASSETS_DIR / "search-index.tsv"
THUMB_DIR    = CACHE_DIR / "thumbs"
WALLPAPER_PATH   = CACHE_DIR / "wallpaper.png"
SOCK_PATH     = CACHE_DIR / "split-daemon.sock"
DAEMON_STATUS = CACHE_DIR / "split-daemon-loading.json"
DAEMON_PY    = _REPO / "emoji-split-daemon.py"
DAEMON_BIN   = _REPO / "emoji-split-daemon"
DAEMON_PID   = CACHE_DIR / "split-daemon.pid"
DAEMON_LOG   = CACHE_DIR / "split-daemon.log"
DATA_TARBALL_URL = "https://github.com/morganrivers/kitchensearch/releases/latest/download/data.tar.gz"

TILE_SIZE   = 200
MAX_RESULTS = 5000
SHOW_BROKEN_THUMBS = False
BATCH_SIZE     = 100
LOAD_MORE      = "⬇  load more results..."
HEADER_MARKER  = "__HEADER__"
STORY_PY    = _REPO / "emoji-story.py"
STORY_BIN   = _REPO / "emoji-story"
STORY_OUT   = CACHE_DIR / "emoji-story.png"

PRIORITY_EMOJIS = frozenset({
    "100", "bird", "boom", "bouquet", "brain", "broccoli", "car", "carrot",
    "cat", "city_sunrise", "cloud", "coconut", "coffee", "computer",
    "crystal_ball", "dango", "derelict_house_building", "dragon", "earth_africa",
    "exclamation", "exploding_head", "face_with_raised_eyebrow",
    "face_with_rolling_eyes", "facepunch", "fire", "fish", "frog",
    "glass_of_milk", "goose", "headphones", "hearts", "hole", "hot_pepper",
    "house", "imp", "iphone", "koala", "last_quarter_moon_with_face", "lemon",
    "lightning", "llama", "low_battery", "magic_wand", "milky_way", "mouse",
    "musical_keyboard", "national_park", "neutral_face", "octopus", "ok",
    "parachute", "people_hugging", "pleading_face", "rain_cloud", "rainbow",
    "relieved", "shrug", "skunk", "snail", "sob", "sparkling_heart",
    "sunrise_over_mountains", "sunglasses", "sushi", "taco", "tiger", "tornado",
    "tropical_drink", "tulip", "turtle", "unicorn_face", "upside_down_face",
    "volcano", "whale", "white_check_mark", "wood", "yum",
})


def _keyword_priority(alt):
    parts = alt.split("-", 1)
    is_priority = any(p in PRIORITY_EMOJIS for p in parts)
    return (0 if is_priority else 1, _random.Random(alt).random())


def _notify(msg):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-t", "3000", "Emoji Kitchen", msg],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print(msg, file=sys.stderr)


_xlib_clipboard_state = None

def _copy_image_xlib(png_data):
    """Own the X11 CLIPBOARD selection and serve PNG bytes to any requestor."""
    global _xlib_clipboard_state
    import select as _select
    from Xlib import display as _Xdisplay, X as _X, Xatom as _Xatom
    from Xlib.protocol import event as _Xevent

    disp   = _Xdisplay.Display()
    screen = disp.screen()
    win    = screen.root.create_window(0, 0, 1, 1, 0, screen.root_depth)

    CLIPBOARD = disp.intern_atom("CLIPBOARD")
    TARGETS   = disp.intern_atom("TARGETS")
    PNG       = disp.intern_atom("image/png")

    win.set_selection_owner(CLIPBOARD, _X.CurrentTime)
    disp.flush()
    if disp.get_selection_owner(CLIPBOARD) != win:
        disp.close()
        raise RuntimeError("could not acquire CLIPBOARD ownership")

    state = {"data": png_data, "running": True}
    if _xlib_clipboard_state:
        _xlib_clipboard_state["running"] = False
    _xlib_clipboard_state = state

    def _loop():
        while state["running"]:
            r, _, _ = _select.select([disp.fileno()], [], [], 0.5)
            if not r:
                continue
            while disp.pending_events():
                ev = disp.next_event()
                if ev.type == _X.SelectionRequest:
                    prop = ev.property if ev.property != _X.NONE else ev.target
                    if ev.target == TARGETS:
                        ev.requestor.change_property(prop, _Xatom.ATOM, 32,
                                                     [TARGETS, PNG])
                    elif ev.target == PNG:
                        ev.requestor.change_property(prop, PNG, 8, state["data"])
                    else:
                        prop = _X.NONE
                    notify = _Xevent.SelectionNotify(
                        time=ev.time, requestor=ev.requestor,
                        selection=ev.selection, target=ev.target, property=prop)
                    ev.requestor.send_event(notify)
                    disp.flush()
                elif ev.type == _X.SelectionClear:
                    state["running"] = False
        disp.close()

    threading.Thread(target=_loop, daemon=True).start()


def copy_image_to_clipboard(path):
    png_data = Path(path).read_bytes()

    # macOS
    if sys.platform == "darwin":
        r = subprocess.run(
            ["osascript", "-e",
             f'set the clipboard to (read (POSIX file "{path}") as «class PNGf»)'],
            capture_output=True)
        if r.returncode == 0:
            return
        _notify("Clipboard failed (osascript error)")
        return

    # Windows
    if sys.platform == "win32":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            f"$img = [System.Drawing.Image]::FromFile('{path}');"
            "[System.Windows.Forms.Clipboard]::SetImage($img)"
        )
        subprocess.run(["powershell", "-Command", ps],
                       capture_output=True, check=True)
        return

    # Linux: try python-xlib (pure Python, no external tools needed)
    if os.environ.get("DISPLAY"):
        try:
            _copy_image_xlib(png_data)
            return
        except Exception:
            pass

    # Fallback to external tools
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        cmd = ["wl-copy", "--type", "image/png"]
    elif shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard", "-t", "image/png"]
    else:
        _notify("No clipboard tool: install xclip (X11) or wl-clipboard (Wayland)")
        return
    with open(path, "rb") as f:
        subprocess.run(cmd, stdin=f, check=True)


def download_data_with_progress(progress_cb, stop_event=None):
    """
    Download and extract the data tarball.
    progress_cb(downloaded_bytes, total_bytes) called periodically.
    stop_event: threading.Event — set it to abort mid-download.
    Returns None on success, error string on failure/cancellation.
    """
    import tarfile, io
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(DATA_TARBALL_URL,
                                     headers={"User-Agent": "emojikitchen"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            buf = io.BytesIO()
            downloaded = 0
            while True:
                if stop_event and stop_event.is_set():
                    return "cancelled"
                chunk = resp.read(65536)
                if not chunk:
                    break
                buf.write(chunk)
                downloaded += len(chunk)
                progress_cb(downloaded, total)
        if stop_event and stop_event.is_set():
            return "cancelled"
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            tf.extractall(_REPO)
        return None
    except Exception as e:
        return str(e)


def _cleanup_incomplete_data():
    """Remove any partially extracted npy files so the next download starts clean."""
    if _has_semantic_models():
        return
    for f in ("base-emoji-nomic.npy", "nomic-image-pca128.npy",
               "nomic-text-pca128.npy", "nomic-pca128-matrix.npy",
               "nomic-pca128-mean.npy"):
        (DATA_DIR / f).unlink(missing_ok=True)
    for f in ("nomic-urls.txt", "nomic-alts.txt"):
        (UI_ASSETS_DIR / f).unlink(missing_ok=True)


def _kill_daemon():
    if not DAEMON_PID.exists():
        return
    try:
        pid = int(DAEMON_PID.read_text().strip())
        os.kill(pid, 9)
    except (ValueError, OSError, ProcessLookupError):
        pass
    DAEMON_PID.unlink(missing_ok=True)
    SOCK_PATH.unlink(missing_ok=True)


def _daemon_alive():
    if not DAEMON_PID.exists():
        return False
    try:
        pid = int(DAEMON_PID.read_text().strip())
    except (ValueError, OSError):
        DAEMON_PID.unlink(missing_ok=True)
        return False
    proc_dir = Path(f"/proc/{pid}")
    if not proc_dir.exists():
        DAEMON_PID.unlink(missing_ok=True)
        return False
    try:
        for line in (proc_dir / "status").read_text().splitlines():
            if line.startswith("State:") and "Z" in line.split(":", 1)[1]:
                DAEMON_PID.unlink(missing_ok=True)
                return False
        if b"emoji-split-daemon" not in (proc_dir / "cmdline").read_bytes():
            DAEMON_PID.unlink(missing_ok=True)
            return False
    except OSError:
        DAEMON_PID.unlink(missing_ok=True)
        return False
    return True


def _spawn_daemon():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(DAEMON_LOG, "wb")
    cmd = [str(DAEMON_BIN)] if DAEMON_BIN.exists() else [_PYTHON, str(DAEMON_PY)]
    proc = subprocess.Popen(cmd,
                             stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True)
    DAEMON_PID.write_text(str(proc.pid))
    return proc


def _wait_for_socket(timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if SOCK_PATH.exists():
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(str(SOCK_PATH))
                s.close()
                return True
            except OSError:
                pass
        time.sleep(0.2)
    return False


def query_daemon(query, limit=MAX_RESULTS):
    if not SOCK_PATH.exists():
        if not _daemon_alive():
            _spawn_daemon()
        if not _wait_for_socket(1):
            return "loading" if _daemon_alive() else None
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(30)
        s.connect(str(SOCK_PATH))
        s.sendall((json.dumps({"query": query, "limit": limit}) + "\n").encode())
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
            if data.endswith(b"\n"):
                break
        s.close()
        results = json.loads(data.decode())
        if isinstance(results, list):
            return [(r["rank"], r["alt"], r["url"], "") for r in results]
    except Exception:
        if SOCK_PATH.exists():
            SOCK_PATH.unlink()
        return "loading" if _daemon_alive() else None
    return None


def load_index():
    entries = []
    with open(SEARCH_INDEX) as f:
        for line in f:
            parts = line.rstrip("\n").split("\t", 2)
            if len(parts) == 3:
                entries.append((parts[0], parts[1], parts[2]))
    return entries


def _score_entry(alt, words, is_single):
    alt_lower  = alt.lower()
    alt_tokens = set(re.split(r'[-_]', alt_lower))
    name_score = sum(1 for w in words if w in alt_tokens)
    square_score = 0
    if is_single:
        parts = alt_lower.split('-', 1)
        if len(parts) == 2 and parts[0] == parts[1]:
            if words[0] in set(parts[0].split('_')):
                square_score = 1
    return square_score, name_score


def search(entries, query, limit=MAX_RESULTS):
    words    = query.lower().split()
    patterns = [re.compile(r'\b' + re.escape(w) + r'\b') for w in words]
    is_single = len(words) == 1
    scored = []
    for url, alt, text in entries:
        text_score = sum(1 for p in patterns if p.search(text.lower()))
        if text_score > 0:
            sq, ns = _score_entry(alt, words, is_single)
            scored.append((sq, ns, text_score, alt, url, text))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2], _keyword_priority(x[3])))
    if scored:
        return [(ts, alt, url, text) for _, _, ts, alt, url, text in scored[:limit]]
    for url, alt, text in entries:
        text_score = sum(1 for w in words if w in text.lower())
        if text_score > 0:
            sq, ns = _score_entry(alt, words, is_single)
            scored.append((sq, ns, text_score, alt, url, text))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2], _keyword_priority(x[3])))
    return [(ts, alt, url, text) for _, _, ts, alt, url, text in scored[:limit]]


def url_to_base_emojis(url):
    m = re.search(r'/u([0-9a-f]+)(?:-u[0-9a-f]+)*_u([0-9a-f]+)(?:-u[0-9a-f]+)*\.png$',
                  url, re.IGNORECASE)
    if m:
        try:
            return chr(int(m.group(1), 16)) + chr(int(m.group(2), 16))
        except (ValueError, OverflowError):
            pass
    return ""


def format_label(alt, url, text):
    base = url_to_base_emojis(url)
    base_str = f"  {base}" if base else ""
    if not text:
        return f"{alt}{base_str}"
    return f"{alt}{base_str}  ({text})"


def build_base_emoji_index(entries):
    seen = {}
    for url, alt, _text in entries:
        m = re.search(r'/u([0-9a-f]+)(?:-u[0-9a-f]+)*_u([0-9a-f]+)(?:-u[0-9a-f]+)*\.png$',
                      url, re.IGNORECASE)
        if not m:
            continue
        hex1, hex2 = m.group(1).lower(), m.group(2).lower()
        parts = alt.split('-', 1)
        name1, name2 = (parts[0], parts[1]) if len(parts) == 2 else (alt, alt)
        for hex_code, name in [(hex1, name1), (hex2, name2)]:
            if hex_code not in seen:
                try:
                    seen[hex_code] = (chr(int(hex_code, 16)), name)
                except (ValueError, OverflowError):
                    pass
    return sorted(seen.items(), key=lambda x: x[1][1])


_THUMB_LIMIT = 200 * 1024 * 1024
_THUMB_DL_SEM = threading.Semaphore(8)  # cap concurrent thumbnail downloads to avoid rate-limiting


def _trim_thumb_cache():
    entries, total = [], 0
    for p in THUMB_DIR.glob("*.png"):
        st = p.stat()
        entries.append((st.st_mtime, st.st_size, p))
        total += st.st_size
    if total <= _THUMB_LIMIT:
        return
    entries.sort()
    for _, size, p in entries:
        if total <= _THUMB_LIMIT:
            break
        p.unlink(missing_ok=True)
        total -= size


def get_thumb(url):
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    name = hashlib.md5(url.encode()).hexdigest() + ".png"
    path = THUMB_DIR / name
    if path.exists() and path.stat().st_size > 0:
        return str(path)
    if path.exists():
        path.unlink(missing_ok=True)
    tmp = path.with_suffix(".png.tmp")
    with _THUMB_DL_SEM:
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "emojikitchen-picker"})
                with urllib.request.urlopen(req, timeout=10) as resp, open(tmp, "wb") as f:
                    shutil.copyfileobj(resp, f)
                if tmp.stat().st_size > 0:
                    tmp.replace(path)
                    return str(path)
            except Exception as e:
                print(f"[thumb fail] attempt={attempt} url={url[:60]} err={e}", flush=True)
            tmp.unlink(missing_ok=True)
            if attempt == 0:
                time.sleep(0.3)
    return None




_PIL_EMOJI_FONT  = None   # PIL ImageFont, loaded once
_PIL_EMOJI_CACHE = {}     # char -> PIL Image (or None on failure)


def _find_emoji_ttf():
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
        "/usr/share/fonts/noto/NotoColorEmoji.ttf",
        "/System/Library/Fonts/Apple Color Emoji.ttc",
        "C:/Windows/Fonts/seguiemj.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    try:
        import subprocess
        out = subprocess.check_output(
            ["fc-list", "Noto Color Emoji", "--format=%{file}\n"],
            text=True, timeout=3)
        path = out.strip().splitlines()[0]
        if path and Path(path).exists():
            return path
    except Exception:
        pass
    return None


def _get_pil_emoji_font():
    global _PIL_EMOJI_FONT
    if _PIL_EMOJI_FONT is not None:
        return _PIL_EMOJI_FONT
    from PIL import ImageFont
    ttf = _find_emoji_ttf()
    if not ttf:
        return None
    try:
        _PIL_EMOJI_FONT = ImageFont.truetype(ttf, 109)
    except Exception:
        return None
    return _PIL_EMOJI_FONT


def render_emoji_pil(char, size=20):
    """Render an emoji char to a PIL Image using the system color emoji font."""
    if char in _PIL_EMOJI_CACHE:
        return _PIL_EMOJI_CACHE[char]
    font = _get_pil_emoji_font()
    if not font:
        _PIL_EMOJI_CACHE[char] = None
        return None
    try:
        from PIL import Image, ImageDraw
        canvas = Image.new("RGBA", (150, 150), (0, 0, 0, 0))
        ImageDraw.Draw(canvas).text((10, 10), char, font=font, embedded_color=True)
        bbox = canvas.getbbox()
        if not bbox:
            _PIL_EMOJI_CACHE[char] = None
            return None
        img = canvas.crop(bbox).resize((size, size), Image.LANCZOS)
        _PIL_EMOJI_CACHE[char] = img
        return img
    except Exception:
        _PIL_EMOJI_CACHE[char] = None
        return None

def _has_semantic_models():
    return (
        all((DATA_DIR / f).exists() for f in (
            "base-emoji-nomic.npy",
            "nomic-image-pca128.npy",
            "nomic-text-pca128.npy",
            "nomic-pca128-matrix.npy",
            "nomic-pca128-mean.npy",
        )) and
        all((UI_ASSETS_DIR / f).exists() for f in (
            "nomic-urls.txt",
            "nomic-alts.txt",
        ))
    )

