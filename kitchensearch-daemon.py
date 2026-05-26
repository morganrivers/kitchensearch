#!/usr/bin/env python3
"""
KitchenSearch Windows daemon.

Registers a global hotkey (default Ctrl+Alt+K) and spawns the picker each
time it fires.  Runs as a Windows system-tray application.  On first run it
writes itself to the Windows registry so it restarts automatically with your
login session.

Usage
-----
  First run (interactive, sets up auto-start):
    python kitchensearch-daemon.py

  Open the settings window to change the hotkey:
    python kitchensearch-daemon.py --settings

  Remove auto-start and exit:
    python kitchensearch-daemon.py --uninstall

  Refresh auto-start path without waiting for hotkey:
    python kitchensearch-daemon.py --setup
"""
import ctypes
import ctypes.wintypes
import io
import json
import os
import subprocess
import sys
import threading
import urllib.request
import winreg
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────────────

_frozen  = getattr(sys, "frozen", False)
_HERE    = Path(sys.executable if _frozen else __file__).resolve().parent
_SELF    = Path(sys.executable).resolve() if _frozen else Path(__file__).resolve()
_PYTHON  = Path(sys.executable).resolve()
_PYTHONW = _PYTHON.parent / "pythonw.exe"

_PICKER_EXE = _HERE / "emoji-picker-tk.exe"
_PICKER_PY  = _HERE / "emoji-picker-tk.py"
_DAEMON_EXE = _HERE / "kitchensearch-daemon.exe"

APP_NAME       = "KitchenSearch"
DEFAULT_HOTKEY = "Ctrl+Alt+K"

_TRAY_ICON_URL = (
    "https://www.gstatic.com/android/keyboard/emojikitchen/20230418"
    "/u1f37d-ufe0f/u1f37d-ufe0f_u1f50e.png"
)

# ── config ────────────────────────────────────────────────────────────────────
# Shares picker-settings.json with the main app so all settings live in one file.

try:
    from platformdirs import user_config_dir as _user_config_dir
    CONFIG_DIR = Path(_user_config_dir("kitchensearch"))
except ImportError:
    CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "kitchensearch"

CONFIG_FILE = CONFIG_DIR / "picker-settings.json"

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ── hotkey parsing ────────────────────────────────────────────────────────────

# Virtual-key codes for special keys (Windows)
_VK_SPECIAL = {
    "SPACE": 0x20, "RETURN": 0x0D, "BACK_SPACE": 0x08, "BACKSPACE": 0x08,
    "TAB": 0x09, "ESCAPE": 0x1B, "DELETE": 0x2E, "INSERT": 0x2D,
    "HOME": 0x24, "END": 0x23, "PRIOR": 0x21, "PAGE_UP": 0x21,
    "NEXT": 0x22, "PAGE_DOWN": 0x22,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    **{f"F{i}": 0x6F + i for i in range(1, 13)},
}

def _parse_hotkey_modifiers_vk(hotkey_str):
    """Return (win32_modifiers, vk_code) for use with RegisterHotKey."""
    _mod_map = {
        "CTRL": _MOD_CONTROL, "CONTROL": _MOD_CONTROL,
        "ALT": _MOD_ALT,
        "SHIFT": _MOD_SHIFT,
        "WIN": _MOD_WIN, "SUPER": _MOD_WIN,
    }
    parts = [p.strip().upper() for p in hotkey_str.split("+")]
    mods = _MOD_NOREPEAT
    vk = None
    for part in parts:
        if part in _mod_map:
            mods |= _mod_map[part]
        elif part in _VK_SPECIAL:
            vk = _VK_SPECIAL[part]
        elif len(part) == 1 and (part.isalpha() or part.isdigit()):
            vk = ord(part)
        else:
            raise ValueError(f"Unknown key: {part!r}")
    if vk is None:
        raise ValueError(f"No main key in: {hotkey_str!r}")
    return mods, vk

# ── registry auto-start ───────────────────────────────────────────────────────

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

def _autostart_cmd():
    if _frozen:
        return f'"{_SELF}"'
    exe = str(_PYTHONW) if _PYTHONW.exists() else str(_PYTHON)
    return f'"{exe}" "{_SELF}"'

def setup_autostart():
    cmd = _autostart_cmd()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                        0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
    print(f"[{APP_NAME}] Auto-start registered.")
    print(f"             {cmd}")

def remove_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
        print(f"[{APP_NAME}] Auto-start entry removed.")
    except FileNotFoundError:
        print(f"[{APP_NAME}] No auto-start entry found.")

def _autostart_current():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                            0, winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
            return str(_SELF) in val
    except Exception:
        return False

# ── spawn picker ──────────────────────────────────────────────────────────────

_picker_proc = [None]

def _spawn():
    # Don't open a second window if one is already running.
    if _picker_proc[0] is not None and _picker_proc[0].poll() is None:
        return
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception:
        pass
    if _PICKER_EXE.exists() and _PICKER_EXE != _SELF:
        cmd = [str(_PICKER_EXE)]
    else:
        cmd = [str(_PYTHON), str(_PICKER_PY)]
    _picker_proc[0] = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)

# ── tray icon — Win32 via ctypes ──────────────────────────────────────────────

_WM_DESTROY = 0x0002
_WM_TRAY    = 0x8001   # WM_APP+1, tray callback message

_NIM_ADD    = 0x0000
_NIM_DELETE = 0x0002
_NIF_MESSAGE = 0x0001
_NIF_ICON    = 0x0002
_NIF_TIP     = 0x0004

_MF_STRING    = 0x0000
_MF_SEPARATOR = 0x0800
_TPM_RETURNCMD = 0x0100
_TPM_NONOTIFY  = 0x0080

_ID_OPEN     = 1001
_ID_SETTINGS = 1002
_ID_QUIT     = 1003

_WM_RBUTTONUP     = 0x0205
_WM_LBUTTONDBLCLK = 0x0203

_MOD_ALT      = 0x0001
_MOD_CONTROL  = 0x0002
_MOD_SHIFT    = 0x0004
_MOD_WIN      = 0x0008
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY    = 0x0312
_HOTKEY_ID    = 1

# On 64-bit Windows WPARAM/LPARAM/LRESULT are pointer-sized (LONG_PTR / UINT_PTR),
# but ctypes.wintypes defines them as 32-bit.  Use c_ssize_t / c_size_t instead.
_WNDPROCTYPE = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,        # LRESULT
    ctypes.wintypes.HWND,
    ctypes.wintypes.UINT,
    ctypes.c_size_t,         # WPARAM
    ctypes.c_ssize_t,        # LPARAM
)


class _WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize",        ctypes.wintypes.UINT),
        ("style",         ctypes.wintypes.UINT),
        ("lpfnWndProc",   _WNDPROCTYPE),
        ("cbClsExtra",    ctypes.c_int),
        ("cbWndExtra",    ctypes.c_int),
        ("hInstance",     ctypes.wintypes.HINSTANCE),
        ("hIcon",         ctypes.wintypes.HICON),
        ("hCursor",       ctypes.wintypes.HANDLE),
        ("hbrBackground", ctypes.wintypes.HBRUSH),
        ("lpszMenuName",  ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
        ("hIconSm",       ctypes.wintypes.HICON),
    ]


class _NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize",           ctypes.wintypes.DWORD),
        ("hWnd",             ctypes.wintypes.HWND),
        ("uID",              ctypes.wintypes.UINT),
        ("uFlags",           ctypes.wintypes.UINT),
        ("uCallbackMessage", ctypes.wintypes.UINT),
        ("hIcon",            ctypes.wintypes.HICON),
        ("szTip",            ctypes.c_wchar * 128),
        ("dwState",          ctypes.wintypes.DWORD),
        ("dwStateMask",      ctypes.wintypes.DWORD),
        ("szInfo",           ctypes.c_wchar * 256),
        ("uVersion",         ctypes.wintypes.UINT),
        ("szInfoTitle",      ctypes.c_wchar * 64),
        ("dwInfoFlags",      ctypes.wintypes.DWORD),
        ("guidItem",         ctypes.c_byte * 16),
        ("hBalloonIcon",     ctypes.wintypes.HICON),
    ]


# Must be kept alive for the entire lifetime of the window to prevent GC crash.
_wnd_proc_ref = None


def _get_tray_hicon():
    """Return a Windows HICON from the emoji-mashup PNG, with fallback."""
    candidates = [
        _HERE / "data" / "ui_assets" / "tray-icon.png",
        CONFIG_DIR / "tray-icon.png",
    ]
    try:
        from PIL import Image
        img = None
        for p in candidates:
            if p.exists():
                try:
                    img = Image.open(str(p)).convert("RGBA")
                    break
                except Exception:
                    pass
        if img is None:
            req = urllib.request.Request(
                _TRAY_ICON_URL, headers={"User-Agent": "kitchensearch/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                img = Image.open(io.BytesIO(resp.read())).convert("RGBA")
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            img.save(str(CONFIG_DIR / "tray-icon.png"))
        img = img.resize((32, 32), Image.LANCZOS)
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".ico", delete=False)
        tmp.close()
        img.save(tmp.name, format="ICO", sizes=[(32, 32)])
        hicon = ctypes.windll.user32.LoadImageW(None, tmp.name, 1, 32, 32, 0x0010)
        os.unlink(tmp.name)
        if hicon:
            return hicon
    except Exception as e:
        print(f"[{APP_NAME}] Icon load failed: {e}")
    return ctypes.windll.user32.LoadIconW(None, 32512)   # IDI_APPLICATION fallback


def _add_tray_icon(hwnd, hicon):
    nid = _NOTIFYICONDATA()
    nid.cbSize = ctypes.sizeof(_NOTIFYICONDATA)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = _NIF_MESSAGE | _NIF_ICON | _NIF_TIP
    nid.uCallbackMessage = _WM_TRAY
    nid.hIcon = hicon
    nid.szTip = APP_NAME
    ctypes.windll.shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(nid))


def _remove_tray_icon(hwnd):
    nid = _NOTIFYICONDATA()
    nid.cbSize = ctypes.sizeof(_NOTIFYICONDATA)
    nid.hWnd = hwnd
    nid.uID = 1
    ctypes.windll.shell32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(nid))


def _show_menu(hwnd):
    user32 = ctypes.windll.user32
    hmenu = user32.CreatePopupMenu()
    user32.AppendMenuW(hmenu, _MF_STRING,    _ID_OPEN,     "Open KitchenSearch")
    user32.AppendMenuW(hmenu, _MF_STRING,    _ID_SETTINGS, "Settings")
    user32.AppendMenuW(hmenu, _MF_SEPARATOR, 0,            None)
    user32.AppendMenuW(hmenu, _MF_STRING,    _ID_QUIT,     "Quit")
    pt = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    user32.SetForegroundWindow(hwnd)
    cmd = user32.TrackPopupMenu(
        hmenu, _TPM_RETURNCMD | _TPM_NONOTIFY,
        pt.x, pt.y, 0, hwnd, None,
    )
    user32.DestroyMenu(hmenu)
    if cmd == _ID_OPEN:
        _spawn()
    elif cmd == _ID_SETTINGS:
        _launch_settings()
    elif cmd == _ID_QUIT:
        user32.DestroyWindow(hwnd)


def _launch_settings():
    threading.Thread(target=_open_settings, daemon=True).start()

# hwnd of the tray window — set once _run() creates it; used by _restart_daemon().
_tray_hwnd = [None]

# ── hotkey + tray message loop ────────────────────────────────────────────────

def _run():
    global _wnd_proc_ref

    config = load_config()
    hotkey = config.get("hotkey", DEFAULT_HOTKEY)
    try:
        hotkey_mods, hotkey_vk = _parse_hotkey_modifiers_vk(hotkey)
    except ValueError:
        print(f"[{APP_NAME}] Invalid hotkey {hotkey!r}, reverting to {DEFAULT_HOTKEY}")
        hotkey = DEFAULT_HOTKEY
        hotkey_mods, hotkey_vk = _parse_hotkey_modifiers_vk(hotkey)

    user32    = ctypes.windll.user32
    kernel32  = ctypes.windll.kernel32
    hinstance = kernel32.GetModuleHandleW(None)
    class_name = "KitchenSearchTray"

    # Fix 64-bit argtypes so DefWindowProcW can receive pointer-sized WPARAM/LPARAM.
    user32.DefWindowProcW.argtypes = [
        ctypes.wintypes.HWND, ctypes.wintypes.UINT,
        ctypes.c_size_t, ctypes.c_ssize_t,
    ]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t

    wm_taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
    state = {"hicon": None}

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == _WM_HOTKEY and wparam == _HOTKEY_ID:
            print(f"[{APP_NAME}] hotkey fired → spawning picker")
            _spawn()
        elif msg == _WM_TRAY:
            if lparam == _WM_RBUTTONUP:
                _show_menu(hwnd)
            elif lparam == _WM_LBUTTONDBLCLK:
                _spawn()
        elif msg == wm_taskbar_created and state["hicon"]:
            _add_tray_icon(hwnd, state["hicon"])
        elif msg == 0x0010:  # WM_CLOSE — destroy window and exit message loop
            print(f"[{APP_NAME}] WM_CLOSE received, shutting down tray")
            user32.DestroyWindow(hwnd)
        elif msg == _WM_DESTROY:
            user32.UnregisterHotKey(hwnd, _HOTKEY_ID)
            _remove_tray_icon(hwnd)
            user32.PostQuitMessage(0)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    _wnd_proc_ref = _WNDPROCTYPE(wnd_proc)

    wc = _WNDCLASSEX()
    wc.cbSize = ctypes.sizeof(_WNDCLASSEX)
    wc.lpfnWndProc = _wnd_proc_ref
    wc.hInstance = hinstance
    wc.lpszClassName = class_name
    user32.RegisterClassExW(ctypes.byref(wc))

    hwnd = user32.CreateWindowExW(
        0, class_name, APP_NAME, 0,
        0, 0, 0, 0,
        ctypes.c_void_p(-3),   # HWND_MESSAGE — invisible message-only window
        None, hinstance, None,
    )
    if not hwnd:
        sys.exit(
            f"[{APP_NAME}] CreateWindowEx failed (err {kernel32.GetLastError()})"
        )

    _tray_hwnd[0] = hwnd

    if not user32.RegisterHotKey(hwnd, _HOTKEY_ID, hotkey_mods, hotkey_vk):
        err = kernel32.GetLastError()
        print(f"[{APP_NAME}] RegisterHotKey failed (err {err}) — hotkey may be in use by another app")
    else:
        print(f"[{APP_NAME}] Hotkey registered: {hotkey}")

    state["hicon"] = _get_tray_hicon()
    _add_tray_icon(hwnd, state["hicon"])
    print(f"[{APP_NAME}] Running in system tray.")

    try:
        msg = ctypes.wintypes.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0:
                break
            if ret == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        print(f"[{APP_NAME}] tray message loop exited (PID {os.getpid()})")

# ── settings GUI ──────────────────────────────────────────────────────────────

def _open_settings():
    import tkinter as tk
    from tkinter import messagebox

    config  = load_config()
    current = config.get("hotkey", DEFAULT_HOTKEY)
    captured = {"hotkey": current, "valid": True}

    root = tk.Tk()
    root.title("KitchenSearch Settings")
    root.resizable(False, False)

    tk.Label(root, text="Keyboard shortcut", font=("Segoe UI", 9, "bold")).pack(
        anchor="w", padx=12, pady=(12, 2))

    frame = tk.Frame(root, bd=1, relief="sunken", bg="white")
    frame.pack(fill="x", padx=12, pady=0)

    hotkey_var = tk.StringVar(value=current)
    display = tk.Label(frame, textvariable=hotkey_var, font=("Segoe UI", 11),
                       bg="white", fg="#1a1a1a", anchor="w", padx=8, pady=6)
    display.pack(fill="x")

    tk.Label(root, text="Click the box above, then press your shortcut.",
             font=("Segoe UI", 8), fg="#666666").pack(anchor="w", padx=12, pady=(3, 2))
    tk.Label(root, text="The daemon will restart automatically when you save.",
             font=("Segoe UI", 8), fg="#999999").pack(anchor="w", padx=12, pady=(0, 8))

    # tkinter on Windows reports Alt via 0x20000; also check 0x0008 as fallback
    _CTRL  = 0x0004
    _SHIFT = 0x0001
    _ALT   = 0x20000 | 0x0008

    def on_key(event):
        sym = event.keysym
        if sym in ("Control_L", "Control_R", "Alt_L", "Alt_R",
                   "Shift_L", "Shift_R", "Super_L", "Super_R", "Meta_L", "Meta_R"):
            return "break"

        parts = []
        if event.state & _CTRL:
            parts.append("Ctrl")
        if event.state & _ALT:
            parts.append("Alt")
        if event.state & _SHIFT:
            parts.append("Shift")

        if not parts:
            return "break"

        key = sym.upper().replace("-", "_")
        if len(key) == 1 and key.isalpha():
            parts.append(key)
        else:
            parts.append(key)

        hotkey_str = "+".join(parts)
        try:
            _parse_hotkey_modifiers_vk(hotkey_str)
            captured["hotkey"] = hotkey_str
            captured["valid"]  = True
            hotkey_var.set(hotkey_str)
            display.config(fg="#1a1a1a")
        except ValueError:
            captured["valid"] = False
            hotkey_var.set(f"{hotkey_str}  — unsupported key")
            display.config(fg="#cc0000")
        return "break"

    display.bind("<Button-1>", lambda e: display.focus_set())
    display.bind("<FocusIn>",  lambda e: display.master.config(bg="#e8f0fe"))
    display.bind("<FocusOut>", lambda e: display.master.config(bg="white"))
    display.bind("<KeyPress>", on_key)
    display.config(cursor="ibeam")

    btn_frame = tk.Frame(root)
    btn_frame.pack(fill="x", padx=12, pady=(0, 12))

    hotkey_changed = [False]

    def save():
        if not captured["valid"]:
            messagebox.showerror("Invalid shortcut",
                                 "The key combination you entered isn't supported.\n"
                                 "Try something like Ctrl+Alt+K or Ctrl+Shift+F2.",
                                 parent=root)
            return
        hotkey_changed[0] = captured["hotkey"] != current
        config["hotkey"] = captured["hotkey"]
        save_config(config)
        root.destroy()

    tk.Button(btn_frame, text="Cancel", width=10, command=root.destroy).pack(side="right", padx=(4, 0))
    tk.Button(btn_frame, text="Save",   width=10, command=save).pack(side="right")

    root.mainloop()

    if hotkey_changed[0]:
        _restart_daemon()

_PID_FILE = CONFIG_DIR / "daemon.pid"

def _write_pid():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))

def _restart_daemon():
    print(f"[{APP_NAME}] restarting daemon (PID {os.getpid()}) …")
    _flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    if _frozen:
        subprocess.Popen([str(_SELF)], creationflags=_flags)
    elif _DAEMON_EXE.exists():
        subprocess.Popen([str(_DAEMON_EXE)], creationflags=_flags)
    else:
        subprocess.Popen([str(_PYTHON), str(_SELF)], creationflags=_flags)
    # Find the tray window — works whether we're the tray process or a --settings subprocess.
    hwnd = _tray_hwnd[0] or ctypes.windll.user32.FindWindowW("KitchenSearchTray", None)
    if hwnd:
        print(f"[{APP_NAME}] posting WM_CLOSE to tray hwnd {hwnd}")
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
    else:
        print(f"[{APP_NAME}] WARNING: could not find tray window to signal")

# ── entry point ───────────────────────────────────────────────────────────────

def main():
    if sys.platform != "win32":
        sys.exit(
            "This daemon is Windows-only.\n"
            "On Linux/macOS assign a shortcut in your DE or WM settings;\n"
            "the .desktop file (Linux) or app bundle (macOS) is the entry point."
        )

    args = set(sys.argv[1:])

    if "--uninstall" in args:
        remove_autostart()
        return

    if "--settings" in args:
        _open_settings()
        return

    setup_autostart()

    if "--setup" in args:
        return

    # Named mutex — one tray instance at a time.
    # Retry for up to 2 s so a restarted instance can wait for the old one to exit.
    import time as _time
    for _attempt in range(20):
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\KitchenSearchDaemon")
        if ctypes.windll.kernel32.GetLastError() != 183:  # not ERROR_ALREADY_EXISTS
            break
        ctypes.windll.kernel32.CloseHandle(_mutex)
        _time.sleep(0.1)
    else:
        sys.exit(0)

    _write_pid()
    try:
        _run()
    finally:
        ctypes.windll.kernel32.CloseHandle(_mutex)


if __name__ == "__main__":
    main()
