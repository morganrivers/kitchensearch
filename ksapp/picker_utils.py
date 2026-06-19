import sys, os, re, json, hashlib, shutil, signal, subprocess
import time, urllib.request, threading, random as _random, getpass
from datetime import datetime as _datetime
from multiprocessing.connection import Client
from pathlib import Path                                                                                                                                                                     
                                                                                                                                                                                             
try:                                                                                                                                                                                       
    from screeninfo import get_monitors as _get_monitors
except ImportError:
    _get_monitors = None

from PIL import Image, ImageDraw, ImageFont as _ImageFont                                                                                                                           


from ksapp.log import _dbg
from ksapp.ssl_ctx import ssl_ctx
from ksapp.data_assets import ensure_data as _ensure_data, _REPO, UI_ASSETS_DIR


from platformdirs import user_cache_dir, user_config_dir

DATA_DIR      = _REPO / "data" / "embeddings"
CACHE_DIR     = Path(user_cache_dir("kitchensearch"))
CONFIG_DIR    = Path(user_config_dir("kitchensearch"))
_VENV_PY = (
    _REPO / ".venv" / "Scripts" / "python.exe"
    if sys.platform == "win32"
    else _REPO / ".venv" / "bin" / "python3"
)
_PYTHON = str(_VENV_PY) if _VENV_PY.exists() else sys.executable
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
DAEMON_PY      = _REPO / "emoji_split_daemon.py"
DAEMON_BIN     = (
    _REPO / ("emoji_split_daemon.exe" if sys.platform == "win32" else "emoji_split_daemon")
    if (_REPO / "emoji_split_daemon").exists() or sys.platform == "win32"
    else Path(shutil.which("emoji_split_daemon") or "emoji_split_daemon")
)
DAEMON_PID     = CACHE_DIR / "split-daemon.pid"
DAEMON_LOG     = CACHE_DIR / "split-daemon.log"

_ensure_data()

TILE_SIZE   = 200
MAX_RESULTS = 5000
SHOW_BROKEN_THUMBS = False
BATCH_SIZE     = 20
LOAD_MORE      = "⬇  load more results..."
HEADER_MARKER  = "__HEADER__"
STORY_PY    = _REPO / "emoji_story.py"
STORY_BIN   = (
    _REPO / ("emoji_story.exe" if sys.platform == "win32" else "emoji_story")
    if (_REPO / "emoji_story").exists() or sys.platform == "win32"
    else Path(shutil.which("emoji_story") or "emoji_story")
)
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
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)
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

    # Windows — use Win32 API directly via ctypes to avoid PowerShell startup lag.
    # CF_DIB (8) is the most compatible image format; paste into any Windows app.
    if sys.platform == "win32":
        try:
            import ctypes, ctypes.wintypes, io
            k32 = ctypes.windll.kernel32
            u32 = ctypes.windll.user32
            k32.GlobalAlloc.restype   = ctypes.c_void_p
            k32.GlobalLock.restype    = ctypes.c_void_p
            k32.GlobalLock.argtypes   = [ctypes.c_void_p]
            k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            k32.GlobalFree.restype    = ctypes.c_void_p
            k32.GlobalFree.argtypes   = [ctypes.c_void_p]
            u32.SetClipboardData.restype  = ctypes.c_void_p
            u32.SetClipboardData.argtypes = [ctypes.wintypes.UINT, ctypes.c_void_p]

            def _make_hglobal(data):
                h = k32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
                if not h:
                    raise OSError("GlobalAlloc failed")
                p = k32.GlobalLock(h)
                if not p:
                    k32.GlobalFree(h)
                    raise OSError("GlobalLock failed")
                ctypes.memmove(p, data, len(data))
                k32.GlobalUnlock(h)
                return h

            img_rgba = Image.open(path).convert("RGBA")

            dib_buf = io.BytesIO()
            img_rgba.convert("RGB").save(dib_buf, "BMP")
            dib = dib_buf.getvalue()[14:]

            png_buf = io.BytesIO()
            img_rgba.save(png_buf, "PNG")
            png_bytes = png_buf.getvalue()

            cf_png = u32.RegisterClipboardFormatW("PNG")

            if not u32.OpenClipboard(None):
                raise OSError("OpenClipboard failed")
            try:
                u32.EmptyClipboard()
                h_dib = _make_hglobal(dib)
                if not u32.SetClipboardData(8, h_dib):  # CF_DIB
                    k32.GlobalFree(h_dib)
                    raise OSError("SetClipboardData CF_DIB failed")
                h_png = _make_hglobal(png_bytes)
                if not u32.SetClipboardData(cf_png, h_png):
                    k32.GlobalFree(h_png)
                    raise OSError("SetClipboardData PNG failed")
            finally:
                u32.CloseClipboard()
        except Exception as e:
            _notify(f"Clipboard copy failed: {e}")
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


def copy_text_to_clipboard(text):
    """Persistently copy text to the system clipboard using OS-native tools.
    Returns True on success."""
    assert isinstance(text, str), "text must be a string"
    data = text.encode("utf-8")

    if sys.platform == "darwin":
        try:
            subprocess.run(["pbcopy"], input=data, check=True)
            return True
        except Exception as e:
            _dbg(f"pbcopy failed: {e}")
            return False

    if sys.platform == "win32":
        try:
            import ctypes
            CF_UNICODETEXT = 13
            u32 = ctypes.windll.user32
            k32 = ctypes.windll.kernel32
            k32.GlobalAlloc.restype = ctypes.c_void_p
            k32.GlobalLock.restype  = ctypes.c_void_p
            k32.GlobalLock.argtypes = [ctypes.c_void_p]
            k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            u32.SetClipboardData.restype = ctypes.c_void_p
            u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
            buf = (text + "\0").encode("utf-16le")
            h = k32.GlobalAlloc(0x0002, len(buf))
            p = k32.GlobalLock(h)
            ctypes.memmove(p, buf, len(buf))
            k32.GlobalUnlock(h)
            if not u32.OpenClipboard(None):
                k32.GlobalFree(h)
                return False
            try:
                u32.EmptyClipboard()
                if not u32.SetClipboardData(CF_UNICODETEXT, h):
                    k32.GlobalFree(h)
                    return False
            finally:
                u32.CloseClipboard()
            return True
        except Exception as e:
            _dbg(f"win32 clipboard text failed: {e}")
            return False

    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        cmd = ["wl-copy"]
    elif shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard"]
    elif shutil.which("xsel"):
        cmd = ["xsel", "--clipboard", "--input"]
    else:
        _dbg("copy_text_to_clipboard: no xclip/xsel/wl-copy available")
        return False
    try:
        subprocess.run(cmd, input=data, check=True)
        return True
    except Exception as e:
        _dbg(f"clipboard text copy failed: {e}")
        return False


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


def _proc_start_time(pid):
    """Opaque process-start identifier, used with the PID to detect reuse."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_t   = wintypes.FILETIME()
            kt       = wintypes.FILETIME()
            ut       = wintypes.FILETIME()
            ok = kernel32.GetProcessTimes(
                h, ctypes.byref(creation), ctypes.byref(exit_t),
                ctypes.byref(kt), ctypes.byref(ut))
        finally:
            kernel32.CloseHandle(h)
        if not ok:
            return None
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "lstart="],
                stderr=subprocess.DEVNULL, text=True,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        if not out:
            return None
        try:
            # ps -o lstart= yields "Thu Jun  6 01:11:45 2026" — collapse runs
            # of whitespace so a single-digit day parses with %d.
            normalised = " ".join(out.split())
            return int(_datetime.strptime(
                normalised, "%a %b %d %H:%M:%S %Y").timestamp())
        except ValueError:
            return None
    try:
        data = Path(f"/proc/{pid}/stat").read_bytes()
    except OSError:
        return None
    try:
        # comm (field 2) is wrapped in parens and may itself contain spaces or
        # parens; everything after the final ')' is space-separated.
        return int(data.rsplit(b")", 1)[1].split()[19])
    except (ValueError, IndexError):
        return None


def _read_pid_record():
    try:
        text = DAEMON_PID.read_text(encoding="utf-8").strip()
    except OSError:
        return (None, None)
    if not text:
        return (None, None)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "pid" in obj:
            return (int(obj["pid"]), obj.get("start_time"))
    except ValueError:
        pass
    try:
        return (int(text), None)
    except ValueError:
        return (None, None)


def _write_pid_record(pid):
    payload = json.dumps({"pid": pid, "start_time": _proc_start_time(pid)})
    tmp = DAEMON_PID.with_suffix(".pid.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(DAEMON_PID)


def _identity_matches(pid, expected_start):
    if expected_start is None:
        return True
    live = _proc_start_time(pid)
    return live is not None and live == expected_start


def _kill_daemon():
    if not DAEMON_PID.exists():
        return
    pid, expected_start = _read_pid_record()
    if pid is not None and _identity_matches(pid, expected_start):
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    DAEMON_PID.unlink(missing_ok=True)
    if not IS_NAMED_PIPE:
        Path(IPC_ADDRESS).unlink(missing_ok=True)


def _daemon_alive():
    if not DAEMON_PID.exists():
        return False
    pid, expected_start = _read_pid_record()
    if pid is None:
        DAEMON_PID.unlink(missing_ok=True)
        return False
    if not _process_exists(pid):
        DAEMON_PID.unlink(missing_ok=True)
        return False
    # PID-reuse detection: the recorded start time must match the live one.
    # Legacy bare-int PID files have no start time and skip this check.
    if not _identity_matches(pid, expected_start):
        DAEMON_PID.unlink(missing_ok=True)
        return False
    # Zombie detection: process is alive, claimed Ready, but socket is gone.
    # Only kill if the status file says the daemon finished loading — during
    # normal startup the socket doesn't exist yet (created after load).
    if not IS_NAMED_PIPE and not Path(IPC_ADDRESS).exists():
        try:
            status = json.loads(DAEMON_STATUS.read_text(encoding="utf-8"))
            if status.get("pct", 0) >= 100:
                os.kill(pid, signal.SIGTERM)
                DAEMON_PID.unlink(missing_ok=True)
                return False
        except Exception as e:
            _dbg(f"_daemon_alive zombie check failed: {e}")
    return True


def _spawn_daemon():
    if os.environ.get("KITCHENSEARCH_KILL_DAEMON") == "1":
        _kill_daemon()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(DAEMON_LOG, "wb")
    cmd = [str(DAEMON_BIN)] if DAEMON_BIN.exists() else [_PYTHON, str(DAEMON_PY)]
    kwargs = {"stdout": log, "stderr": subprocess.STDOUT}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    _write_pid_record(proc.pid)
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
        try:
            conn.send_bytes(json.dumps({"query": query, "limit": limit}).encode())
            results = json.loads(conn.recv_bytes().decode())
        except Exception:
            return "loading" if _daemon_alive() else None
        if isinstance(results, list):
            return [(r["rank"], r["alt"], r["url"], "") for r in results]
        if isinstance(results, dict) and "error" in results:
            raise RuntimeError(results["error"])
        return None
    finally:
        conn.close()


def load_index():
    entries = []
    with open(SEARCH_INDEX, encoding="utf-8") as f:
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

COPY_COUNTS_PATH = CACHE_DIR / "copy_counts.json"
_copy_counts_lock = threading.Lock()


def _load_copy_counts():
    try:
        return json.loads(COPY_COUNTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_copy_counts(d):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = COPY_COUNTS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        tmp.replace(COPY_COUNTS_PATH)
    except Exception as e:
        print(f"[copy_counts save fail] err={e}", flush=True)


def record_copy(url, alt):
    key = hashlib.md5(url.encode()).hexdigest()
    with _copy_counts_lock:
        d = _load_copy_counts()
        rec = d.get(key, {"count": 0, "alt": alt})
        rec["count"] = int(rec.get("count", 0)) + 1
        rec["alt"] = alt or rec.get("alt", "")
        d[key] = rec
        _save_copy_counts(d)
    path = THUMB_DIR / (key + ".png")
    if path.exists():
        try:
            path.touch()
        except OSError:
            pass


def top_copied(n=10):
    d = _load_copy_counts()
    items = [(int(v.get("count", 0)), v.get("alt", "") or "(unnamed)")
             for v in d.values() if int(v.get("count", 0)) > 0]
    items.sort(reverse=True)
    return items[:n]


# ── favorites ──────────────────────────────────────────────────────────────
# Favorites are user data (not a regenerable cache), so they live in CONFIG_DIR
# and survive cache trims. Stored as a list of {"alt", "url", "text"} dicts,
# most-recently-added first, deduplicated by url.
FAVORITES_PATH = CONFIG_DIR / "favorites.json"
_favorites_lock = threading.Lock()


def load_favorites():
    try:
        data = json.loads(FAVORITES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_favorites(items):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = FAVORITES_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
        tmp.replace(FAVORITES_PATH)
    except Exception as e:
        print(f"[favorites save fail] err={e}", flush=True)


def is_favorite(url):
    return any(f.get("url") == url for f in load_favorites())


def add_favorite(alt, url, text=""):
    with _favorites_lock:
        items = [f for f in load_favorites() if f.get("url") != url]
        items.insert(0, {"alt": alt, "url": url, "text": text or ""})
        _save_favorites(items)


def remove_favorite(url):
    with _favorites_lock:
        items = [f for f in load_favorites() if f.get("url") != url]
        _save_favorites(items)


def toggle_favorite(alt, url, text=""):
    """Add the emoji if absent, remove it if present. Returns the new state
    (True = now a favorite)."""
    with _favorites_lock:
        items = load_favorites()
        if any(f.get("url") == url for f in items):
            items = [f for f in items if f.get("url") != url]
            _save_favorites(items)
            return False
        items.insert(0, {"alt": alt, "url": url, "text": text or ""})
        _save_favorites(items)
        return True


def _trim_thumb_cache():
    counts = _load_copy_counts()
    entries, total = [], 0
    for p in THUMB_DIR.glob("*.png"):
        st = p.stat()
        c = int(counts.get(p.stem, {}).get("count", 0))
        entries.append(((c, st.st_mtime), st.st_size, p))
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
                with urllib.request.urlopen(req, timeout=10, context=ssl_ctx()) as resp, open(tmp, "wb") as f:
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




_BMC_BASE_URL                     = "https://www.buymeacoffee.com/morganrivers"
_BMC_BUTTON_PATH                  = _REPO / "data" / "ui_assets" / "buymeacoffee_button.png"
_TIP_MODAL_MIN_HOURS              = 72
_TIP_MODAL_MIN_COPIES             = 14
_TIP_MODAL_DISMISSAL_SECS         = 3 * 86400
_TIP_MODAL_DISMISSAL_MIN_COPIES   = 6


def get_buymeacoffee_url():
    return _BMC_BASE_URL


def get_tip_modal_button_path():
    return str(_BMC_BUTTON_PATH) if _BMC_BUTTON_PATH.exists() else None


def should_show_tip_modal(settings, now):
    assert isinstance(settings, dict), "settings must be a dict"
    if os.environ.get("KITCHENSEARCH_SHOW_BANNER") == "1":
        return True
    if bool(settings.get("hide_ads")):
        return False
    if now < settings.get("snooze_until", 0):
        return False
    install = settings.get("install")
    if install is None:
        return False
    if (now - float(install)) / 3600 < _TIP_MODAL_MIN_HOURS:
        return False
    copy_count = int(settings.get("copy_count", 0))
    if copy_count < _TIP_MODAL_MIN_COPIES:
        return False
    dismissed_at = settings.get("dismissed_at", 0)
    if dismissed_at:
        if (now - dismissed_at) < _TIP_MODAL_DISMISSAL_SECS:
            return False
        dismissed_cc = int(settings.get("dismissed_copy_count", 0))
        if (copy_count - dismissed_cc) < _TIP_MODAL_DISMISSAL_MIN_COPIES:
            return False
    return True




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
    except Exception as e:
        _dbg(f"_find_emoji_ttf fc-list failed: {e}")
    return None


_EMOJI_CANVAS_PX = 300
_EMOJI_SIZES_DARWIN = (160, 128, 96, 64, 48, 40, 32, 20)
_EMOJI_SIZES_OTHER  = (109,)


def _get_pil_emoji_font():
    global _PIL_EMOJI_FONT
    if _PIL_EMOJI_FONT is not None:
        return _PIL_EMOJI_FONT
    from PIL import ImageFont
    ttf = _find_emoji_ttf()
    if not ttf:
        _dbg("_get_pil_emoji_font: no emoji ttf found on this system")
        return None
    sizes = _EMOJI_SIZES_DARWIN if sys.platform == "darwin" else _EMOJI_SIZES_OTHER
    last_err = None
    for pt in sizes:
        try:
            _PIL_EMOJI_FONT = ImageFont.truetype(ttf, pt)
            _dbg(f"_get_pil_emoji_font loaded ttf={ttf!r} pt={pt}")
            return _PIL_EMOJI_FONT
        except Exception as e:
            last_err = e
            _dbg(f"_get_pil_emoji_font tried pt={pt}: {e}")
    _dbg(f"_get_pil_emoji_font: all sizes failed last_err={last_err}")
    return None


def render_emoji_pil(char, size=20):
    """Render an emoji char to a PIL Image using the system color emoji font."""
    if char in _PIL_EMOJI_CACHE:
        return _PIL_EMOJI_CACHE[char]
    font = _get_pil_emoji_font()
    if not font:
        _dbg(f"render_emoji_pil: no font available; returning None for char={char!r}")
        _PIL_EMOJI_CACHE[char] = None
        return None
    try:
        from PIL import Image, ImageDraw
        canvas = Image.new("RGBA", (_EMOJI_CANVAS_PX, _EMOJI_CANVAS_PX), (0, 0, 0, 0))
        ImageDraw.Draw(canvas).text((10, 10), char, font=font, embedded_color=True)
        bbox = canvas.getbbox()
        if not bbox:
            _dbg(f"render_emoji_pil: empty bbox for char={char!r} — font rendered nothing")
            _PIL_EMOJI_CACHE[char] = None
            return None
        img = canvas.crop(bbox).resize((size, size), Image.LANCZOS)
        _PIL_EMOJI_CACHE[char] = img
        return img
    except Exception as e:
        _dbg(f"render_emoji_pil failed char={char!r}: {e}")
        _PIL_EMOJI_CACHE[char] = None
        return None

def _has_semantic_models():
    return all((DATA_DIR / f).exists() for f in (
        "base-emoji-minilm.npy",
        "minilm-pca340.npy",
        "minilm-pca340-matrix.npy",
        "minilm-pca340-mean.npy",
    ))

