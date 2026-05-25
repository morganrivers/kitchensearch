#!/usr/bin/env python3
"""
KitchenSearch Windows daemon.

Registers Ctrl+Alt+K as a global hotkey and spawns the picker each time
it fires. On first run it writes itself to the Windows registry so it
restarts automatically with your login session.

No extra dependencies — uses only Python stdlib + ctypes.

Usage
-----
  First run (interactive, sets up auto-start):
    python kitchensearch-daemon.py

  Remove auto-start and exit:
    python kitchensearch-daemon.py --uninstall

  Refresh auto-start path without waiting for hotkey:
    python kitchensearch-daemon.py --setup
"""
import ctypes
import ctypes.wintypes
import subprocess
import sys
import winreg
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────────────

_HERE    = Path(__file__).resolve().parent
_SELF    = Path(__file__).resolve()
_PYTHON  = Path(sys.executable).resolve()
# pythonw.exe suppresses the console window when launched at login
_PYTHONW = _PYTHON.parent / "pythonw.exe"

# Compiled install has emoji-picker-tk.exe alongside this exe;
# dev mode falls back to running the Python script.
_PICKER_EXE = _HERE / "emoji-picker-tk.exe"
_PICKER_PY  = _HERE / "emoji-picker-tk.py"

APP_NAME  = "KitchenSearch"
HOTKEY_HR = "Ctrl+Alt+K"

# Windows virtual-key / modifier constants
_MOD_ALT      = 0x0001
_MOD_CONTROL  = 0x0002
_VK_K         = 0x4B
_WM_HOTKEY    = 0x0312
_HOTKEY_ID    = 1

# ── registry auto-start ───────────────────────────────────────────────────────

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

def _autostart_cmd():
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
    """Return True if the registry entry already points to this script."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                            0, winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
            return str(_SELF) in val
    except Exception:
        return False

# ── spawn picker ──────────────────────────────────────────────────────────────

def _spawn():
    # Grant the new process permission to take the foreground window.
    # The daemon owns the hotkey event so Windows accepts this call.
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception:
        pass
    # Prefer the compiled exe (installed build); fall back to Python script (dev).
    if _PICKER_EXE.exists() and _PICKER_EXE != _SELF:
        cmd = [str(_PICKER_EXE)]
    else:
        cmd = [str(_PYTHON), str(_PICKER_PY)]
    subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)

# ── hotkey message loop ───────────────────────────────────────────────────────

def _run():
    user32 = ctypes.windll.user32

    if not user32.RegisterHotKey(None, _HOTKEY_ID,
                                  _MOD_CONTROL | _MOD_ALT, _VK_K):
        sys.exit(
            f"[{APP_NAME}] Could not register {HOTKEY_HR}.\n"
            "              Is another instance already running?"
        )

    print(f"[{APP_NAME}] Listening for {HOTKEY_HR}. "
          f"Run with --uninstall to remove auto-start.")

    try:
        msg = ctypes.wintypes.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0:   # WM_QUIT
                break
            if ret == -1:  # error
                break
            if msg.message == _WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                _spawn()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        user32.UnregisterHotKey(None, _HOTKEY_ID)

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

    # Always refresh the auto-start entry so the path stays current
    setup_autostart()

    if "--setup" in args:
        return

    _run()


if __name__ == "__main__":
    main()
