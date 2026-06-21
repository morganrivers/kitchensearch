"""Windows test harness — independent from the X11 ``harness.py`` and the
macOS ``mac_harness.py``.

Mirrors the *public* methods used by recorded test scripts (``screenshot``,
``wait``, ``key``, ``type``, ``make_gif``, plus the context-manager protocol)
using only Windows-native facilities:

  * ``PIL.ImageGrab``         for screenshots (Pillow is already a dependency)
  * Win32 ``SendInput``       for keystrokes (via ctypes)
  * Win32 ``EnumWindows`` …   to locate / focus the Tk window by title
  * PowerShell clipboard      for clipboard PNG / text extraction

This file intentionally duplicates the GIF stitching logic so that edits here
cannot regress the Linux or macOS harnesses, and vice-versa.
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageGrab


_REPO            = Path(__file__).parent.parent
_TEST_CONFIG_DIR = Path(__file__).parent / "_test_config" / "kitchensearch"

_DEFAULT_TEST_SETTINGS = {
    "notify_on_copy": False,
    "exit_on_select": False,
    "semantic_first": True,
    "show_keyword":   True,
    "show_semantic":  True,
    "show_combo":     True,
    "show_story":     True,
    "floating":       True,
    "frameless":      False,
    "dark_mode":      False,
    "copy_count":     130,
    "hide_ads":       True,
}

# ── Win32 plumbing ───────────────────────────────────────────────────────────

user32   = ctypes.WinDLL("user32",   use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t

INPUT_KEYBOARD        = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP       = 0x0002
KEYEVENTF_UNICODE     = 0x0004
SW_RESTORE            = 9


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# Pin argtypes/restype so 64-bit window/thread handles are not truncated to int.
user32.EnumWindows.argtypes              = [_WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype               = wintypes.BOOL
user32.IsWindowVisible.argtypes          = [wintypes.HWND]
user32.IsWindowVisible.restype           = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes     = [wintypes.HWND]
user32.GetWindowTextLengthW.restype      = ctypes.c_int
user32.GetWindowTextW.argtypes           = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype            = ctypes.c_int
user32.ShowWindow.argtypes               = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype                = wintypes.BOOL
user32.GetForegroundWindow.argtypes      = []
user32.GetForegroundWindow.restype       = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype  = wintypes.DWORD
user32.AttachThreadInput.argtypes        = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype         = wintypes.BOOL
user32.BringWindowToTop.argtypes         = [wintypes.HWND]
user32.BringWindowToTop.restype          = wintypes.BOOL
user32.SetForegroundWindow.argtypes      = [wintypes.HWND]
user32.SetForegroundWindow.restype       = wintypes.BOOL
user32.SendInput.argtypes                = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
user32.SendInput.restype                 = wintypes.UINT
user32.ClientToScreen.argtypes           = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype            = wintypes.BOOL
user32.SetCursorPos.argtypes             = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype              = wintypes.BOOL
user32.mouse_event.argtypes              = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                            wintypes.DWORD, ULONG_PTR]
user32.mouse_event.restype               = None
kernel32.GetCurrentThreadId.argtypes     = []
kernel32.GetCurrentThreadId.restype      = wintypes.DWORD

_MOUSEEVENTF = {
    1: (0x0002, 0x0004),   # left  down, up
    3: (0x0008, 0x0010),   # right down, up
}


# X11 key names (as used by recorded scripts) → (virtual-key code, is_extended)
_KEY_CODES = {
    "Return":    (0x0D, False),
    "Escape":    (0x1B, False),
    "space":     (0x20, False),
    "Tab":       (0x09, False),
    "Down":      (0x28, True),
    "Up":        (0x26, True),
    "Left":      (0x25, True),
    "Right":     (0x27, True),
    "BackSpace": (0x08, False),
    "Delete":    (0x2E, True),
    "Home":      (0x24, True),
    "End":       (0x23, True),
    "Page_Up":   (0x21, True),
    "Page_Down": (0x22, True),
    "F1":  (0x70, False), "F2":  (0x71, False), "F3":  (0x72, False),
    "F4":  (0x73, False), "F5":  (0x74, False), "F6":  (0x75, False),
    "F7":  (0x76, False), "F8":  (0x77, False), "F9":  (0x78, False),
    "F10": (0x79, False), "F11": (0x7A, False), "F12": (0x7B, False),
}

_MOD_VK = {
    "ctrl":  0x11,
    "shift": 0x10,
    "alt":   0x12,
}


def _send_vk(vk: int, up: bool = False, extended: bool = False):
    flags = KEYEVENTF_KEYUP if up else 0
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = _INPUT(type=INPUT_KEYBOARD)
    inp.ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _send_unicode(ch: str):
    code = ord(ch)
    for up in (False, True):
        flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
        inp = _INPUT(type=INPUT_KEYBOARD)
        inp.ki = _KEYBDINPUT(wVk=0, wScan=code, dwFlags=flags, time=0, dwExtraInfo=0)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _write_settings(settings: dict):
    _TEST_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (_TEST_CONFIG_DIR / "picker-settings.json").write_text(
        json.dumps(settings, indent=2), encoding="utf-8"
    )


def _powershell(script: str):
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True,
    )


class WinTestHarness:
    WINDOW_TITLE    = "Kitchen Search"
    STARTUP_TIMEOUT = 45.0   # per attempt
    STARTUP_ATTEMPTS = 3     # relaunch if the window doesn't appear in time
    STARTUP_SETTLE  = 1.5

    def __init__(self, run_dir, settings_path: Path | None = None,
                 saved_context: dict | None = None,
                 app_cmd: list[str] | None = None):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._settings_path = settings_path
        self._saved_context = saved_context
        # KITCHENSEARCH_BIN lets the workflow run the *installed* entry point;
        # default falls back to the source tree.
        env_bin = os.environ.get("KITCHENSEARCH_BIN")
        if app_cmd is not None:
            self._app_cmd = list(app_cmd)
        elif env_bin:
            self._app_cmd = [env_bin, "--logging"]
        else:
            self._app_cmd = [sys.executable, str(_REPO / "kitchensearch.py"), "--logging"]
        self._proc                          = None
        self._hwnd                          = None
        self._shots: list[tuple[str, Path]] = []
        self.effective_settings: dict       = {}
        self.effective_env:      dict       = {}
        self.stderr_output:      str        = ""
        self._stderr_buf:    list[bytes]              = []
        self._stderr_thread: threading.Thread | None = None
        self._stderr_log_path: Path = self.run_dir / "stderr.log"
        self._stderr_log_fh = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def __enter__(self):
        try:
            return self.launch()
        except Exception:
            self.close()
            raise

    def __exit__(self, *_):
        self.close()

    def launch(self):
        settings = dict(_DEFAULT_TEST_SETTINGS)
        if self._settings_path and self._settings_path.exists():
            overrides = json.loads(self._settings_path.read_text(encoding="utf-8"))
            settings.update({k: v for k, v in overrides.items() if not k.startswith("_")})
        if self._saved_context is not None:
            settings = dict(self._saved_context.get("settings", settings))
        _write_settings(settings)
        self.effective_settings = settings
        self.effective_env      = {}

        env = os.environ.copy()
        env["XDG_CONFIG_HOME"]         = str(_TEST_CONFIG_DIR.parent)
        env["KITCHENSEARCH_CONFIG_DIR"] = str(_TEST_CONFIG_DIR)
        env["KITCHENSEARCH_NO_BLINK"]  = "1"
        env["KITCHENSEARCH_NO_DAEMON"] = "1"   # run the GUI without background daemons
        copy_log = self.run_dir / "copied-images.log"
        copy_log.unlink(missing_ok=True)
        env["KITCHENSEARCH_COPY_LOG"] = str(copy_log)
        env["KITCHENSEARCH_TEST_EVENTS"] = "1"

        # Clear the clipboard so a post-test read distinguishes "the app copied
        # something" from "stale clipboard content".
        _powershell("Set-Clipboard -Value ([string]::Empty)")

        # The GUI occasionally needs a second try to come up under CI load
        # (e.g. a slow first window paint), so relaunch rather than fail outright.
        last_err: Exception | None = None
        for attempt in range(1, self.STARTUP_ATTEMPTS + 1):
            try:
                return self._spawn_and_wait(env)
            except RuntimeError as exc:
                last_err = exc
                print(f"    [launch] attempt {attempt}/{self.STARTUP_ATTEMPTS} "
                      f"failed: {exc}", file=sys.stderr, flush=True)
                self._terminate_proc()
        assert last_err is not None
        raise last_err

    def _spawn_and_wait(self, env):
        self._stderr_buf = []
        if self._stderr_log_fh is None:
            self._stderr_log_fh = open(self._stderr_log_path, "wb")
        self._proc = subprocess.Popen(
            self._app_cmd, env=env, cwd=str(_REPO),
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

        deadline = time.monotonic() + self.STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(0.5)
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"App exited before window appeared (rc={self._proc.returncode}). "
                    f"stderr:\n{b''.join(self._stderr_buf).decode(errors='replace')}"
                )
            hwnd = self._find_window()
            if hwnd:
                self._hwnd = hwnd
                time.sleep(self.STARTUP_SETTLE)
                self._activate()
                return self

        titles = self._all_window_titles()
        raise RuntimeError(
            f"Window '{self.WINDOW_TITLE}' did not appear within "
            f"{self.STARTUP_TIMEOUT}s. Visible top-level windows: {titles!r}"
        )

    def _terminate_proc(self):
        """Kill the current app process tree so a retry starts clean."""
        if self._proc:
            subprocess.run(
                ["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                capture_output=True,
            )
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if self._stderr_thread:
            self._stderr_thread.join(timeout=3)
            self._stderr_thread = None
        self._proc = None
        self._hwnd = None

    def _drain_stderr(self):
        try:
            for line in self._proc.stderr:
                self._stderr_buf.append(line)
                fh = self._stderr_log_fh
                if fh is not None:
                    try:
                        fh.write(line)
                        fh.flush()
                    except Exception:
                        pass
                try:
                    sys.stderr.write("    [app] " + line.decode(errors="replace"))
                    sys.stderr.flush()
                except Exception:
                    pass
        except Exception:
            pass

    def wait_for_event(self, substr: str, timeout: float = 15.0):
        """Block until the app emits a new ``KSEVENT`` line containing ``substr``.

        Event-driven screen sync (see TestHarness.wait_for_event). Polls the
        already-drained stderr; snapshots at call time so it waits for an event
        *after* this call. Returns the matching line, or None on timeout.
        """
        start = len(self._stderr_buf)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for raw in self._stderr_buf[start:]:
                line = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
                if "KSEVENT" in line and substr in line:
                    return line.strip()
            time.sleep(0.03)
        return None

    def close(self):
        if self._proc:
            # Kill the whole tree so any daemon children spawned by the app die too.
            subprocess.run(
                ["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                capture_output=True,
            )
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if self._stderr_thread:
            self._stderr_thread.join(timeout=3)
        if self._stderr_log_fh is not None:
            try:
                self._stderr_log_fh.close()
            except Exception:
                pass
            self._stderr_log_fh = None
        self.stderr_output = b"".join(self._stderr_buf).decode(errors="replace")

    @property
    def meaningful_stderr(self) -> str:
        return self.stderr_output.strip()

    # ── window helpers ───────────────────────────────────────────────────────

    def _find_window(self):
        # Prefer the title window owned by the app *we* launched (so a stray
        # window from a previous test isn't picked up), but fall back to any
        # title match. Visibility is not required: a freshly mapped Tk window
        # may not yet report visible, and we activate it before capturing.
        want_pid = self._proc.pid if self._proc else None
        pid_matches:   list[int] = []
        title_matches: list[int] = []

        def _cb(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if self.WINDOW_TITLE not in buf.value:
                return True
            title_matches.append(hwnd)
            if want_pid is not None:
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == want_pid:
                    pid_matches.append(hwnd)
            return True

        user32.EnumWindows(_WNDENUMPROC(_cb), 0)
        if pid_matches:
            return pid_matches[-1]
        return title_matches[-1] if title_matches else None

    def _all_window_titles(self) -> list[str]:
        """Visible top-level window titles — diagnostic for startup failures."""
        titles: list[str] = []

        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            titles.append(buf.value)
            return True

        user32.EnumWindows(_WNDENUMPROC(_cb), 0)
        return titles

    def _activate(self):
        hwnd = self._find_window() or self._hwnd
        if not hwnd:
            return
        self._hwnd = hwnd
        user32.ShowWindow(hwnd, SW_RESTORE)
        # AttachThreadInput so SetForegroundWindow is honoured from a background
        # automation context (the standard CI focus-stealing workaround).
        fg = user32.GetForegroundWindow()
        cur_thread    = kernel32.GetCurrentThreadId()
        target_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if target_thread and target_thread != cur_thread:
            attached = bool(user32.AttachThreadInput(target_thread, cur_thread, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if attached:
            user32.AttachThreadInput(target_thread, cur_thread, False)
        time.sleep(0.25)

    # ── actions ──────────────────────────────────────────────────────────────

    def screenshot(self, name: str, stable: bool = True) -> Path:
        """Full-screen PNG capture. No window cropping on Windows for the MVP."""
        path = self.run_dir / f"{name}.png"
        self._activate()
        time.sleep(0.4 if stable else 0.05)
        img = ImageGrab.grab()
        img.save(path)
        self._shots.append((name, path))
        print(f"    [shot] {name}", flush=True)
        return path

    def wait(self, seconds: float):
        time.sleep(seconds)

    def key(self, key_name: str):
        """Send a key press. Supports modifier+key syntax: 'ctrl+Right'."""
        self._activate()
        parts = key_name.split("+")
        key   = parts[-1]
        mods  = [m for m in parts[:-1] if m in _MOD_VK]

        for m in mods:
            _send_vk(_MOD_VK[m], up=False)
        try:
            if key in _KEY_CODES:
                vk, ext = _KEY_CODES[key]
                _send_vk(vk, up=False, extended=ext)
                _send_vk(vk, up=True, extended=ext)
            elif len(key) == 1 and (key.isalnum()):
                # Letters/digits have VK == ord(uppercase); needed so modifiers
                # (e.g. ctrl+a) compose correctly. Unicode injection ignores them.
                vk = ord(key.upper())
                _send_vk(vk, up=False)
                _send_vk(vk, up=True)
            elif len(key) == 1:
                _send_unicode(key)
            else:
                raise ValueError(f"unmapped key: {key!r}")
        finally:
            for m in reversed(mods):
                _send_vk(_MOD_VK[m], up=True)

    def type(self, text: str, delay_ms: int = 40):
        self._activate()
        for ch in text:
            _send_unicode(ch)
            if delay_ms:
                time.sleep(delay_ms / 1000.0)

    def focus(self):
        self._activate()

    def _to_screen(self, x: int, y: int):
        """Map window client-area coords (as recorded on Linux) to screen px."""
        hwnd = self._find_window() or self._hwnd
        pt = wintypes.POINT(int(x), int(y))
        if hwnd:
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
        return pt.x, pt.y

    def click(self, x: int, y: int, button: int = 1):
        self._activate()
        sx, sy = self._to_screen(x, y)
        user32.SetCursorPos(sx, sy)
        time.sleep(0.05)
        down, up = _MOUSEEVENTF.get(button, _MOUSEEVENTF[1])
        user32.mouse_event(down, 0, 0, 0, 0)
        user32.mouse_event(up, 0, 0, 0, 0)

    def mousedown(self, x: int, y: int, button: int = 1):
        self._activate()
        sx, sy = self._to_screen(x, y)
        user32.SetCursorPos(sx, sy)
        time.sleep(0.05)
        down, _ = _MOUSEEVENTF.get(button, _MOUSEEVENTF[1])
        user32.mouse_event(down, 0, 0, 0, 0)
        if button == 3:
            time.sleep(0.35)

    def mouseup(self, x: int, y: int, button: int = 1):
        _, up = _MOUSEEVENTF.get(button, _MOUSEEVENTF[1])
        user32.mouse_event(up, 0, 0, 0, 0)

    # ── GIF export (duplicated from harness.py on purpose) ───────────────────

    def make_gif(self, output_path, frame_duration_ms: int = 800):
        output_path = Path(output_path)
        frames: list[Image.Image] = []

        try:
            font = ImageFont.truetype("arialbd.ttf", 14)
        except OSError:
            font = ImageFont.load_default()

        for name, path in self._shots:
            if not path.exists():
                continue
            img = Image.open(path).convert("RGBA")
            label_h = 22
            labeled = Image.new("RGBA", (img.width, img.height + label_h),
                                (20, 20, 20, 255))
            labeled.paste(img, (0, label_h))
            draw = ImageDraw.Draw(labeled)
            draw.rectangle([0, 0, img.width, label_h], fill=(30, 30, 30, 255))
            draw.text((6, 4), name, fill=(220, 220, 220, 255), font=font)
            frames.append(labeled.convert("P", palette=Image.ADAPTIVE))

        if not frames:
            return

        frames[0].save(
            output_path,
            save_all=True,
            append_images=frames[1:],
            duration=frame_duration_ms,
            loop=0,
            optimize=False,
        )
        print(f"GIF saved → {output_path}")


def dump_clipboard(out_dir: Path) -> Path | None:
    """Save the current clipboard as PNG (preferred) or text.

    Returns the written path, or None if the clipboard is empty.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    png = out_dir / "clipboard.png"
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$img = [System.Windows.Forms.Clipboard]::GetImage();"
        f"if ($img -ne $null) {{ $img.Save('{png}'); 'png' }} else {{ 'no-png' }}"
    )
    r = _powershell(script)
    if (r.returncode == 0 and "png" in r.stdout and "no-png" not in r.stdout
            and png.exists() and png.stat().st_size > 0):
        return png
    if png.exists():
        png.unlink()

    r = _powershell("Get-Clipboard -Raw")
    if r.returncode == 0 and r.stdout.strip():
        txt = out_dir / "clipboard.txt"
        txt.write_text(r.stdout, encoding="utf-8")
        return txt
    return None
