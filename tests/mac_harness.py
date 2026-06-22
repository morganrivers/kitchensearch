"""macOS test harness — independent from the X11 `harness.py`.

Mirrors the *public* methods used by recorded test scripts
(`screenshot`, `wait`, `key`, `type`, `make_gif`, plus context-manager
protocol) using only macOS-native tools:

  * `screencapture`            for screenshots
  * `osascript` System Events  for keystrokes
  * AppleScript «class PNGf»   for clipboard PNG extraction
  * `pbpaste`                  for clipboard text fallback

This file intentionally duplicates the GIF stitching logic so that any
edits here cannot regress the Linux harness, and vice-versa.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    import Quartz  # pyobjc-framework-Quartz: window bounds + synthetic mouse events
    _HAS_QUARTZ = True
except Exception:
    _HAS_QUARTZ = False


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

# AppleScript key codes for non-printable keys (X11 names → macOS virtual codes)
_KEY_CODES = {
    "Return":    36,
    "Escape":    53,
    "space":     49,
    "Tab":       48,
    "Down":      125,
    "Up":        126,
    "Left":      123,
    "Right":     124,
    "BackSpace": 51,
    "Delete":    117,
    "Home":      115,
    "End":       119,
    "Page_Up":   116,
    "Page_Down": 121,
}

_MOD_NAMES = {
    "ctrl":  "control down",
    "shift": "shift down",
    "alt":   "option down",
    "cmd":   "command down",
}


def _osa(script: str, check: bool = True) -> str:
    r = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True,
    )
    if check and r.returncode != 0:
        raise RuntimeError(
            f"osascript failed:\nscript: {script}\nstderr: {r.stderr.strip()}"
        )
    return r.stdout.strip()


def _write_settings(settings: dict):
    _TEST_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (_TEST_CONFIG_DIR / "picker-settings.json").write_text(
        json.dumps(settings, indent=2), encoding="utf-8"
    )


class MacTestHarness:
    WINDOW_TITLE    = "Kitchen Search"
    STARTUP_TIMEOUT = 30.0
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
        self._proc                  = None
        self._shots: list[tuple[str, Path]] = []
        self.effective_settings: dict       = {}
        self.effective_env:      dict       = {}
        self.stderr_output:      str        = ""
        self._stderr_buf:   list[bytes]              = []
        self._stderr_thread: threading.Thread | None = None
        self._stderr_log_path: Path = self.run_dir / "stderr.log"
        self._stderr_log_fh = None
        self._debug_log_src: Path = Path(tempfile.gettempdir()) / "kitchensearch-debug.log"
        self._debug_log_dst: Path = self.run_dir / "debug.log"
        self._daemon_ready_seen = False
        self._daemon_gave_up = False
        self._daemon_free = False

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
        env["KITCHENSEARCH_NO_BLINK"]  = "1"
        copy_log = self.run_dir / "copied-images.log"
        copy_log.unlink(missing_ok=True)
        env["KITCHENSEARCH_COPY_LOG"] = str(copy_log)

        # Clear the clipboard so a post-test read distinguishes "the app copied
        # something" from "stale clipboard content".
        subprocess.run(["pbcopy"], input=b"", check=False)

        try:
            if self._debug_log_src.exists():
                self._debug_log_src.unlink()
        except OSError:
            pass

        self._stderr_buf = []
        self._stderr_log_fh = open(self._stderr_log_path, "wb")
        self._proc = subprocess.Popen(
            self._app_cmd, env=env, cwd=str(_REPO),
            stderr=subprocess.PIPE,
            start_new_session=True,
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
            if self._window_exists():
                time.sleep(self.STARTUP_SETTLE)
                self._activate()
                return self

        raise RuntimeError(
            f"Window '{self.WINDOW_TITLE}' did not appear within "
            f"{self.STARTUP_TIMEOUT}s"
        )

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

    def _killpg(self, sig):
        try:
            os.killpg(os.getpgid(self._proc.pid), sig)
        except (ProcessLookupError, OSError):
            pass

    def close(self):
        if self._proc:
            if self._proc.poll() is None:
                self._killpg(signal.SIGTERM)
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            if self._proc.poll() is None:
                self._killpg(signal.SIGKILL)
                try:
                    self._proc.wait(timeout=2)
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
        if self._debug_log_src.exists():
            try:
                shutil.copyfile(self._debug_log_src, self._debug_log_dst)
                print(f"    [dbg] debug log → {self._debug_log_dst}", flush=True)
            except OSError as e:
                print(f"    [dbg] failed to copy debug log: {e}", flush=True)

    @property
    def meaningful_stderr(self) -> str:
        return self.stderr_output.strip()

    # ── window helpers ───────────────────────────────────────────────────────

    def _window_exists(self) -> bool:
        pid = self._proc.pid
        script = f'''
            tell application "System Events"
                try
                    set p to (first process whose unix id is {pid})
                    repeat with w in (windows of p)
                        if name of w contains "{self.WINDOW_TITLE}" then return "yes"
                    end repeat
                end try
            end tell
            return "no"
        '''
        try:
            return _osa(script, check=False) == "yes"
        except Exception:
            return False

    def _activate(self):
        pid = self._proc.pid
        script = f'''
            tell application "System Events"
                try
                    set frontmost of (first process whose unix id is {pid}) to true
                end try
            end tell
        '''
        _osa(script, check=False)
        time.sleep(0.25)

    # ── actions ──────────────────────────────────────────────────────────────

    def screenshot(self, name: str, stable: bool = True) -> Path:
        """Full-screen PNG capture. No window cropping on macOS for the MVP."""
        path = self.run_dir / f"{name}.png"
        if stable:
            self._wait_daemon_ready()
        self._activate()
        time.sleep(0.4 if stable else 0.05)
        subprocess.run(["screencapture", "-x", str(path)],
                       check=True, timeout=10)
        self._shots.append((name, path))
        print(f"    [shot] {name}", flush=True)
        return path

    def _wait_daemon_ready(self):
        """Don't capture while the model is still loading. Skipped when the GUI
        runs daemon-free, and never blocks more than once: on timeout it gives
        up so it can't stall every screenshot. See readiness.py."""
        if self._daemon_free or self._daemon_ready_seen or self._daemon_gave_up:
            return
        from readiness import wait_for_daemon_ready
        if wait_for_daemon_ready(timeout=10):
            self._daemon_ready_seen = True
        else:
            self._daemon_gave_up = True

    def wait(self, seconds: float):
        time.sleep(seconds)

    def key(self, key_name: str):
        """Send a key press. Supports modifier+key syntax: 'ctrl+Right'."""
        self._activate()
        parts = key_name.split("+")
        key   = parts[-1]
        mods  = parts[:-1]
        using = ""
        if mods:
            mapped = [_MOD_NAMES[m] for m in mods if m in _MOD_NAMES]
            if mapped:
                using = " using {" + ", ".join(mapped) + "}"
        code = _KEY_CODES.get(key)
        if code is not None:
            script = f'tell application "System Events" to key code {code}{using}'
        else:
            esc = key.replace("\\", "\\\\").replace('"', '\\"')
            script = f'tell application "System Events" to keystroke "{esc}"{using}'
        _osa(script)

    def type(self, text: str, delay_ms: int = 40):
        self._activate()
        esc = text.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "System Events" to keystroke "{esc}"'
        _osa(script)

    def focus(self):
        self._activate()

    # Recorded coordinates are relative to the window content origin (the Linux
    # window is undecorated). If the macOS window carries a title bar, bump this
    # to (0, title_bar_height); 0 assumes the floating window is borderless.
    MAC_CONTENT_OFFSET = (0, 0)

    def _window_bounds(self):
        """(x, y, w, h) of the app window in screen pixels, via Quartz."""
        if not _HAS_QUARTZ:
            return None
        pid = self._proc.pid if self._proc else None
        info = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        best = None
        for w in info:
            if pid is not None and w.get("kCGWindowOwnerPID") != pid:
                continue
            b = w.get("kCGWindowBounds")
            if not b:
                continue
            cand = (b["X"], b["Y"], b["Width"], b["Height"])
            if self.WINDOW_TITLE in (w.get("kCGWindowName") or ""):
                return cand
            if best is None or cand[2] * cand[3] > best[2] * best[3]:
                best = cand
        return best

    def _to_screen(self, x: int, y: int) -> tuple[float, float]:
        b = self._window_bounds()
        if b is None:
            raise RuntimeError(
                "could not locate the app window for a click "
                "(pyobjc-framework-Quartz missing?)"
            )
        ox, oy = self.MAC_CONTENT_OFFSET
        return b[0] + ox + int(x), b[1] + oy + int(y)

    def _cg_codes(self, button: int):
        return {
            1: (Quartz.kCGEventLeftMouseDown,  Quartz.kCGEventLeftMouseUp,  Quartz.kCGMouseButtonLeft),
            2: (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp, Quartz.kCGMouseButtonCenter),
            3: (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp, Quartz.kCGMouseButtonRight),
        }[button]

    def _cg_post(self, evtype, sx: float, sy: float, btn):
        ev = Quartz.CGEventCreateMouseEvent(None, evtype, Quartz.CGPointMake(sx, sy), btn)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    def click(self, x: int, y: int, button: int = 1):
        self._activate()
        sx, sy = self._to_screen(x, y)
        down, up, btn = self._cg_codes(button)
        self._cg_post(down, sx, sy, btn)
        self._cg_post(up, sx, sy, btn)

    def mousedown(self, x: int, y: int, button: int = 1):
        """Press (but do not release) — used before a screenshot to capture a
        transient menu (e.g. a right-click popup)."""
        self._activate()
        sx, sy = self._to_screen(x, y)
        down, _up, btn = self._cg_codes(button)
        self._cg_post(down, sx, sy, btn)
        if button == 3:
            time.sleep(0.35)   # let the popup post (mirrors the Linux harness)

    def mouseup(self, x: int, y: int, button: int = 1):
        sx, sy = self._to_screen(x, y)
        _down, up, btn = self._cg_codes(button)
        self._cg_post(up, sx, sy, btn)

    # ── GIF export (duplicated from harness.py on purpose) ───────────────────

    def make_gif(self, output_path, frame_duration_ms: int = 800):
        output_path = Path(output_path)
        frames: list[Image.Image] = []

        try:
            font = ImageFont.truetype(
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf", 14)
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
    script = f'''
        try
            set imgData to (the clipboard as «class PNGf»)
            set outFile to open for access POSIX file "{png}" with write permission
            set eof outFile to 0
            write imgData to outFile
            close access outFile
            return "png"
        on error errMsg
            try
                close access POSIX file "{png}"
            end try
            return "no-png"
        end try
    '''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if (r.returncode == 0 and r.stdout.strip() == "png"
            and png.exists() and png.stat().st_size > 0):
        return png
    if png.exists():
        png.unlink()

    r = subprocess.run(["pbpaste"], capture_output=True)
    if r.returncode == 0 and r.stdout:
        txt = out_dir / "clipboard.txt"
        txt.write_bytes(r.stdout)
        return txt
    return None
