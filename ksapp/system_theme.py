"""OS light/dark scheme detection and live watching.

Single source of truth for "what colour scheme does the OS want".
`current_scheme()` returns "dark" or "light" on any platform.
`SystemThemeWatcher` pushes changes to a callback: it subscribes to the
XDG desktop portal SettingChanged D-Bus signal on Linux, and polls on
macOS/Windows where no dependency-free push mechanism exists.
"""

import re
import sys
import threading
import subprocess

THEME_PREFS = ("light", "dark", "system")

_TIMEOUT = 2.0


def _no_window_flags():
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    return 0


def _run(cmd):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=_TIMEOUT,
        creationflags=_no_window_flags(),
    )


def _parse_portal_uint(out):
    """Portal returns e.g. "(<<uint32 1>>,)"; the value is the last integer
    (the "32" in "uint32" is part of the type name, not the value)."""
    nums = re.findall(r"\d+", out)
    return int(nums[-1]) if nums else None


def _linux_scheme():
    # Prefer the cross-desktop XDG portal (spec: 0=no pref, 1=dark, 2=light).
    try:
        r = _run(["gdbus", "call", "--session",
                  "--dest", "org.freedesktop.portal.Desktop",
                  "--object-path", "/org/freedesktop/portal/desktop",
                  "--method", "org.freedesktop.portal.Settings.Read",
                  "org.freedesktop.appearance", "color-scheme"])
        if r.returncode == 0:
            v = _parse_portal_uint(r.stdout)
            if v == 1:
                return "dark"
            if v == 2:
                return "light"
    except Exception:
        pass
    # Fallback: GNOME gsettings ("'prefer-dark'", "'default'", "'prefer-light'").
    try:
        r = _run(["gsettings", "get", "org.gnome.desktop.interface",
                  "color-scheme"])
        if r.returncode == 0:
            return "dark" if "dark" in r.stdout.lower() else "light"
    except Exception:
        pass
    return "light"


def _macos_scheme():
    try:
        r = _run(["defaults", "read", "-g", "AppleInterfaceStyle"])
        # Key exists and equals "Dark" only when dark; absent (rc!=0) => light.
        if r.returncode == 0 and "dark" in r.stdout.strip().lower():
            return "dark"
    except Exception:
        pass
    return "light"


def _windows_scheme():
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        try:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        finally:
            winreg.CloseKey(key)
        return "light" if val else "dark"
    except Exception:
        return "light"


def current_scheme():
    if sys.platform == "darwin":
        return _macos_scheme()
    if sys.platform == "win32":
        return _windows_scheme()
    return _linux_scheme()


class SystemThemeWatcher:
    """Calls on_change("dark"|"light") when the OS scheme changes.

    on_change runs on a background thread; the caller is responsible for
    marshalling onto its UI thread.
    """

    def __init__(self, on_change):
        self._on_change = on_change
        self._stop = threading.Event()
        self._thread = None
        self._proc = None
        self._last = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._last = current_scheme()
        linux = sys.platform not in ("darwin", "win32")
        target = self._run_signal if linux else self._run_poll
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _emit_if_changed(self):
        scheme = current_scheme()
        if scheme != self._last:
            self._last = scheme
            try:
                self._on_change(scheme)
            except Exception:
                pass

    def _run_signal(self):
        """Subscribe to portal SettingChanged; re-read scheme on any signal."""
        try:
            self._proc = subprocess.Popen(
                ["gdbus", "monitor", "--session",
                 "--dest", "org.freedesktop.portal.Desktop",
                 "--object-path", "/org/freedesktop/portal/desktop"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                creationflags=_no_window_flags())
        except Exception:
            self._run_poll()
            return
        try:
            for line in self._proc.stdout:
                if self._stop.is_set():
                    break
                if "color-scheme" in line or "SettingChanged" in line:
                    self._emit_if_changed()
        except Exception:
            pass
        finally:
            # If the monitor died on its own (not a stop()), fall back to poll.
            if not self._stop.is_set():
                self._run_poll()

    def _run_poll(self):
        while not self._stop.is_set():
            self._emit_if_changed()
            self._stop.wait(2.0)
