import sys, os, re, json, hashlib, shutil, signal, subprocess
import time, urllib.request, threading, random as _random, traceback, getpass
from multiprocessing.connection import Client
from pathlib import Path                                                                                                                                                                     
                                                                                                                                                                                             
try:                                                                                                                                                                                       
    from screeninfo import get_monitors as _get_monitors
except ImportError:
    _get_monitors = None

from PIL import Image, ImageDraw, ImageFont as _ImageFont                                                                                                                           


# ── debug log ─────────────────────────────────────────────────────────────────
_DBG_ENABLED  = "--logging" in sys.argv
_DBG_LOG_PATH = Path("/tmp/emojipicker-debug.log")
if _DBG_ENABLED:
    _DBG_LOG_PATH.write_text("")  # truncate on each launch
_dbg_lock = threading.Lock()

def _dbg(msg, include_tb=False):
    if not _DBG_ENABLED:
        return
    ts = time.strftime("%H:%M:%S") + f".{int(time.time()*1000)%1000:03d}"
    tid = threading.get_ident()
    lines = [f"[{ts}][tid={tid}] {msg}"]
    if include_tb:
        tb_lines = traceback.format_stack(limit=8)
        lines.append("  STACK: " + " | ".join(l.strip() for l in tb_lines[:-1]))
    with _dbg_lock:
        with open(_DBG_LOG_PATH, "a") as f:
            f.write("\n".join(lines) + "\n")


from platformdirs import user_cache_dir, user_config_dir

_REPO         = Path(sys.argv[0]).resolve().parent
DATA_DIR      = _REPO / "data" / "embeddings"
UI_ASSETS_DIR = _REPO / "data" / "ui_assets"
CACHE_DIR     = Path(user_cache_dir("kitchensearch"))
CONFIG_DIR    = Path(user_config_dir("kitchensearch"))
_VENV_PY      = _REPO / ".venv" / "bin" / "python3"
_PYTHON       = str(_VENV_PY) if _VENV_PY.exists() else sys.executable
SEARCH_INDEX  = UI_ASSETS_DIR / "search-index.tsv"
THUMB_DIR     = CACHE_DIR / "thumbs"
WALLPAPER_PATH    = CACHE_DIR / "wallpaper.png"
def _ipc_address() -> str:
    """Cross-platform IPC endpoint: Unix socket on POSIX, named pipe on Windows.

    Named pipes are global to the machine, so namespace by username to avoid
    collisions on shared systems.
    """
    if sys.platform == "win32":
        return r"\\.\pipe\kitchensearch-" + getpass.getuser()
    return str(CACHE_DIR / "split-daemon.sock")


IPC_ADDRESS    = _ipc_address()
IS_NAMED_PIPE  = IPC_ADDRESS.startswith(r"\\.\pipe")
DAEMON_STATUS  = CACHE_DIR / "split-daemon-loading.json"
DAEMON_PY      = _REPO / "emoji-split-daemon.py"
DAEMON_BIN     = _REPO / "emoji-split-daemon"
DAEMON_PID     = CACHE_DIR / "split-daemon.pid"
DAEMON_LOG     = CACHE_DIR / "split-daemon.log"

def _ensure_data():
    if SEARCH_INDEX.exists():
        return
    tarball = _REPO / "data" / "app_assets.tar.gz"
    if not tarball.exists():
        sys.exit(f"Data files missing and {tarball} not found. Please re-download the app.")
    import tarfile
    UI_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball) as tf:
        tf.extractall(_REPO)

_ensure_data()

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
    if sys.platform == "darwin":
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{msg}" with title "Emoji Kitchen"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif sys.platform == "win32":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$b = New-Object System.Windows.Forms.NotifyIcon;"
            "$b.Icon = [System.Drawing.SystemIcons]::Information;"
            "$b.BalloonTipTitle = 'Emoji Kitchen';"
            f"$b.BalloonTipText = '{msg}';"
            "$b.Visible = $true;"
            "$b.ShowBalloonTip(3000)"
        )
        subprocess.run(["powershell", "-c", ps],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif shutil.which("notify-send"):
        subprocess.run(["notify-send", "-t", "3000", "Emoji Kitchen", msg],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print(msg, file=sys.stderr)


def _copy_image_xlib(png_data):
    """Own the X11 CLIPBOARD selection, served from a detached grandchild process."""
    import select as _select
    from Xlib import display as _Xdisplay, X as _X, Xatom as _Xatom
    from Xlib.protocol import event as _Xevent

    # Double-fork so the grandchild is fully detached (reparented to init).
    # The parent returns immediately; the grandchild serves clipboard requests
    # until another app takes ownership (SelectionClear).
    pid = os.fork()
    if pid != 0:
        os.waitpid(pid, 0)
        return

    # --- intermediate child ---
    try:
        pid2 = os.fork()
        if pid2 != 0:
            os._exit(0)

        # --- grandchild: own and serve the clipboard ---
        disp   = _Xdisplay.Display()
        screen = disp.screen()
        win    = screen.root.create_window(0, 0, 1, 1, 0, screen.root_depth)

        CLIPBOARD = disp.intern_atom("CLIPBOARD")
        TARGETS   = disp.intern_atom("TARGETS")
        PNG       = disp.intern_atom("image/png")

        win.set_selection_owner(CLIPBOARD, _X.CurrentTime)
        disp.flush()
        if disp.get_selection_owner(CLIPBOARD) != win:
            os._exit(1)

        while True:
            r, _, _ = _select.select([disp.fileno()], [], [], 0.5)
            if not r:
                continue
            done = False
            while disp.pending_events():
                ev = disp.next_event()
                if ev.type == _X.SelectionRequest:
                    prop = ev.property if ev.property != _X.NONE else ev.target
                    if ev.target == TARGETS:
                        ev.requestor.change_property(prop, _Xatom.ATOM, 32,
                                                     [TARGETS, PNG])
                    elif ev.target == PNG:
                        ev.requestor.change_property(prop, PNG, 8, png_data)
                    else:
                        prop = _X.NONE
                    notify = _Xevent.SelectionNotify(
                        time=ev.time, requestor=ev.requestor,
                        selection=ev.selection, target=ev.target, property=prop)
                    ev.requestor.send_event(notify)
                    disp.flush()
                elif ev.type == _X.SelectionClear:
                    done = True
            if done:
                break
        disp.close()
    except Exception:
        pass
    finally:
        os._exit(0)


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


def _cleanup_incomplete_data():
    """Remove any partially extracted npy files so the next download starts clean."""
    if _has_semantic_models():
        return
    for f in ("base-emoji-minilm.npy", "minilm-pca340.npy",
               "minilm-pca340-matrix.npy", "minilm-pca340-mean.npy"):
        (DATA_DIR / f).unlink(missing_ok=True)


def _process_exists(pid: int) -> bool:
    """Portable existence check: True if a process with this PID is running."""
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return bool(ok) and exit_code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _kill_daemon():
    if not DAEMON_PID.exists():
        return
    try:
        pid = int(DAEMON_PID.read_text().strip())
        os.kill(pid, signal.SIGTERM)
    except (ValueError, OSError, ProcessLookupError):
        pass
    DAEMON_PID.unlink(missing_ok=True)
    if not IS_NAMED_PIPE:
        Path(IPC_ADDRESS).unlink(missing_ok=True)


def _daemon_alive():
    if not DAEMON_PID.exists():
        return False
    try:
        pid = int(DAEMON_PID.read_text().strip())
    except (ValueError, OSError):
        DAEMON_PID.unlink(missing_ok=True)
        return False
    if not _process_exists(pid):
        DAEMON_PID.unlink(missing_ok=True)
        return False
    # Zombie detection: process is alive, claimed Ready, but socket is gone.
    # Only kill if the status file says the daemon finished loading — during
    # normal startup the socket doesn't exist yet (created after load).
    if not IS_NAMED_PIPE and not Path(IPC_ADDRESS).exists():
        try:
            status = json.loads(DAEMON_STATUS.read_text())
            if status.get("pct", 0) >= 100:
                os.kill(pid, signal.SIGTERM)
                DAEMON_PID.unlink(missing_ok=True)
                return False
        except Exception:
            pass
    return True


def _spawn_daemon():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(DAEMON_LOG, "wb")
    cmd = [str(DAEMON_BIN)] if DAEMON_BIN.exists() else [_PYTHON, str(DAEMON_PY)]
    kwargs = {"stdout": log, "stderr": subprocess.STDOUT}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    DAEMON_PID.write_text(str(proc.pid))
    return proc


def _try_connect():
    """Open a Client to the daemon, or None if it isn't accepting connections."""
    try:
        return Client(IPC_ADDRESS)
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        return None


def _daemon_ready():
    """True if the daemon is accepting connections (i.e. finished loading)."""
    conn = _try_connect()
    if conn is None:
        return False
    conn.close()
    return True


def _wait_for_daemon(timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        conn = _try_connect()
        if conn is not None:
            conn.close()
            return True
        time.sleep(0.2)
    return False


def query_daemon(query, limit=MAX_RESULTS):
    conn = _try_connect()
    if conn is None:
        if not _daemon_alive():
            _spawn_daemon()
        if not _wait_for_daemon(1):
            return "loading" if _daemon_alive() else None
        conn = _try_connect()
        if conn is None:
            return "loading" if _daemon_alive() else None
    try:
        conn.send_bytes(json.dumps({"query": query, "limit": limit}).encode())
        results = json.loads(conn.recv_bytes().decode())
        if isinstance(results, list):
            return [(r["rank"], r["alt"], r["url"], "") for r in results]
        if isinstance(results, dict) and "error" in results:
            raise RuntimeError(results["error"])
    except Exception:
        return "loading" if _daemon_alive() else None
    finally:
        conn.close()
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
    _dbg(f"SEARCH start query={query!r} n_entries={len(entries)}")
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
        result = [(ts, alt, url, text) for _, _, ts, alt, url, text in scored[:limit]]
        _dbg(f"SEARCH done (exact pass) n_results={len(result)}")
        return result
    for url, alt, text in entries:
        text_score = sum(1 for w in words if w in text.lower())
        if text_score > 0:
            sq, ns = _score_entry(alt, words, is_single)
            scored.append((sq, ns, text_score, alt, url, text))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2], _keyword_priority(x[3])))
    result = [(ts, alt, url, text) for _, _, ts, alt, url, text in scored[:limit]]
    _dbg(f"SEARCH done (fuzzy pass) n_results={len(result)}")
    return result


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
    _dbg(f"BUILD_BASE_EMOJI_INDEX start n_entries={len(entries)}")
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
    result = sorted(seen.items(), key=lambda x: x[1][1])
    _dbg(f"BUILD_BASE_EMOJI_INDEX done n_unique={len(result)}")
    return result


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




_AB_BASE_URL      = "https://www.buymeacoffee.com/morganrivers"
_AB_BUTTON_PATH   = _REPO / "data" / "ui_assets" / "buymeacoffee_button.png"
_AB_TIMING_HOURS  = {"A": 36, "B": 48, "C": 72}


def _next_tuesday_ts():
    """Timestamp of midnight at the start of the next Tuesday (local time)."""
    today = date.today()
    days  = (1 - today.weekday()) % 7  # days until Tuesday; 0 means today is Tuesday. 
    if days == 0:
        days = 7
    next_tue = today + timedelta(days=days)
    return _datetime(next_tue.year, next_tue.month, next_tue.day).timestamp()


def _ab_mtime_ms():
    """Return THUMB_DIR mtime as integer milliseconds, or None if not yet created."""
    override = os.environ.get("KITCHENSEARCH_AB_MTIME_MS")
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    try:
        return int(THUMB_DIR.stat().st_mtime * 1000)
    except OSError:
        return None


def _ab_bucket():
    mt = _ab_mtime_ms()
    return mt % 6 if mt is not None else 0


def _ab_version():
    mt = _ab_mtime_ms()
    return f"{(mt & 0xFFFFFFF):07x}" if mt is not None else "0000000"


def _ab_hours_elapsed():
    try:
        return (time.time() - THUMB_DIR.stat().st_mtime) / 3600
    except OSError:
        return 0


def get_buymeacoffee_url():
    return f"{_AB_BASE_URL}?version={_ab_version()}"


def should_show_banner():
    bucket   = _ab_bucket()
    timing_v = ("A", "B", "C")[bucket % 3]
    return _ab_hours_elapsed() >= _AB_TIMING_HOURS[timing_v]


def get_banner_config():
    if not should_show_banner() and os.environ.get("KITCHENSEARCH_SHOW_BANNER") != "1":
        return None
    copy_v = "indie" if _ab_bucket() >= 3 else "simple"
    return {
        "headline": "🥹 Support an indie developer!" if copy_v == "indie" else None,
        "image":    str(_AB_BUTTON_PATH) if _AB_BUTTON_PATH.exists() else None,
        "url":      get_buymeacoffee_url(),
        "variant":  copy_v,
    }




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
    return all((DATA_DIR / f).exists() for f in (
        "base-emoji-minilm.npy",
        "minilm-pca340.npy",
        "minilm-pca340-matrix.npy",
        "minilm-pca340-mean.npy",
    ))

