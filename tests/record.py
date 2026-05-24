#!/usr/bin/env python3
"""
Record a test session interactively.

Usage:
    python tests/record.py <test_name>

    e.g. python tests/record.py keyword_search

Launches the app, records all your keyboard/mouse actions, then:
  - writes tests/scripts/test_NN_<name>.py
  - reruns the new test and prints the GIF path
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from pynput import keyboard as kb, mouse as ms

_TESTS_DIR  = Path(__file__).parent
_REPO       = _TESTS_DIR.parent
_SCRIPTS    = _TESTS_DIR / "scripts"
_TEST_CFG   = _TESTS_DIR / "_test_config" / "kitchensearch"

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_test_settings():
    # Reuse harness logic so record and replay use identical settings
    sys.path.insert(0, str(Path(__file__).parent))
    from harness import _make_test_settings as _hms
    _hms()


def _find_window(title="Kitchen Search"):
    try:
        out = subprocess.check_output(
            ["xdotool", "search", "--name", title], stderr=subprocess.DEVNULL
        ).decode().strip()
        ids = [l.strip() for l in out.splitlines() if l.strip()]
        return ids[-1] if ids else None
    except subprocess.CalledProcessError:
        return None


def _window_geometry(wid):
    """Return (x, y, w, h) of window in screen coords."""
    out = subprocess.check_output(["xdotool", "getwindowgeometry", wid]).decode()
    x = y = w = h = 0
    for line in out.splitlines():
        if "Position:" in line:
            coords = line.split(":")[1].strip().split(" ")[0]
            x, y = map(int, coords.split(","))
        if "Geometry:" in line:
            dims = line.split(":")[1].strip()
            w, h = map(int, dims.split("x"))
    return x, y, w, h


def _next_test_number():
    existing = sorted(_SCRIPTS.glob("test_*.py"))
    if not existing:
        return 1
    last = existing[-1].stem   # e.g. test_01_main_menu
    m = re.match(r"test_(\d+)", last)
    return (int(m.group(1)) + 1) if m else len(existing) + 1


# ── pynput key name → xdotool key name ───────────────────────────────────────

_SPECIAL = {
    kb.Key.enter:       "Return",
    kb.Key.esc:         "Escape",
    kb.Key.space:       "space",
    kb.Key.backspace:   "BackSpace",
    kb.Key.delete:      "Delete",
    kb.Key.tab:         "Tab",
    kb.Key.up:          "Up",
    kb.Key.down:        "Down",
    kb.Key.left:        "Left",
    kb.Key.right:       "Right",
    kb.Key.home:        "Home",
    kb.Key.end:         "End",
    kb.Key.page_up:     "Prior",
    kb.Key.page_down:   "Next",
    kb.Key.f1:  "F1",  kb.Key.f2:  "F2",  kb.Key.f3:  "F3",  kb.Key.f4:  "F4",
    kb.Key.f5:  "F5",  kb.Key.f6:  "F6",  kb.Key.f7:  "F7",  kb.Key.f8:  "F8",
    kb.Key.f9:  "F9",  kb.Key.f10: "F10", kb.Key.f11: "F11", kb.Key.f12: "F12",
}
_MODIFIERS = {kb.Key.ctrl_l, kb.Key.ctrl_r, kb.Key.shift_l, kb.Key.shift_r,
              kb.Key.alt_l, kb.Key.alt_r, kb.Key.cmd}


# ── recorder ──────────────────────────────────────────────────────────────────

class Recorder:
    SCREENSHOT_DELAY = 0.35   # seconds to wait after an action before screenshotting

    def __init__(self, wid: str):
        self.wid       = wid
        self.wx, self.wy, self.ww, self.wh = _window_geometry(wid)
        self.events: list[dict] = []     # raw event log
        self._held_mods: set   = set()
        self._char_buf: str    = ""      # accumulate typed chars
        self._last_t           = time.monotonic()
        self._shot_counter     = 0
        self._pending_shot     = False
        self._stop             = False

    # ── internals ─────────────────────────────────────────────────────────────

    def _flush_chars(self):
        if self._char_buf:
            self.events.append({"type": "type", "text": self._char_buf})
            self._char_buf = ""

    def _wait_since_last(self):
        now = time.monotonic()
        gap = round(now - self._last_t, 2)
        self._last_t = now
        if gap > 0.15:
            self.events.append({"type": "wait", "secs": min(gap, 3.0)})

    def _queue_screenshot(self):
        self._pending_shot = True

    def _take_screenshot(self, label: str):
        self._shot_counter += 1
        name = f"{self._shot_counter:02d}_{label}"
        path = f"/tmp/ks_record_{name}.png"
        crop = f"{self.ww}x{self.wh}+{self.wx}+{self.wy}"
        subprocess.run(
            ["import", "-window", "root", "-crop", crop, "+repage", path],
            stderr=subprocess.DEVNULL
        )
        self.events.append({"type": "screenshot", "name": name})

    # ── pynput callbacks ──────────────────────────────────────────────────────

    def on_press(self, key):
        if key in _MODIFIERS:
            self._held_mods.add(key)
            return
        self._wait_since_last()

        # Ctrl+F12 = stop recording
        if key == kb.Key.f12 and (kb.Key.ctrl_l in self._held_mods or kb.Key.ctrl_r in self._held_mods):
            self._flush_chars()
            self._stop = True
            return False   # stop listener

        if key in _SPECIAL:
            self._flush_chars()
            keyname = _SPECIAL[key]
            # Prepend active modifiers
            mods = []
            if kb.Key.ctrl_l in self._held_mods or kb.Key.ctrl_r in self._held_mods:
                mods.append("ctrl")
            if kb.Key.alt_l in self._held_mods or kb.Key.alt_r in self._held_mods:
                mods.append("alt")
            if mods:
                keyname = "+".join(mods + [keyname])
            self.events.append({"type": "key", "key": keyname})
            self._queue_screenshot()
        else:
            # Printable char — check for ctrl+char combos
            try:
                ch = key.char
                if ch and (kb.Key.ctrl_l in self._held_mods or kb.Key.ctrl_r in self._held_mods):
                    self._flush_chars()
                    self.events.append({"type": "key", "key": f"ctrl+{ch}"})
                    self._queue_screenshot()
                elif ch:
                    self._char_buf += ch
            except AttributeError:
                pass

    def on_release(self, key):
        self._held_mods.discard(key)
        # After releasing a printable run, schedule a screenshot
        if key not in _MODIFIERS and key not in _SPECIAL:
            self._queue_screenshot()

    def on_click(self, x, y, button, pressed):
        # Use pynput absolute coords minus window origin — consistent with how
        # xdotool getwindowgeometry reports the content-area position, which is
        # what xdotool mousemove --window uses during replay.
        rx, ry = int(x) - self.wx, int(y) - self.wy

        # Ignore clicks that land outside the app window
        if not (0 <= rx < self.ww and 0 <= ry < self.wh):
            return

        btn = 1 if button == ms.Button.left else (3 if button == ms.Button.right else 2)

        if not pressed:
            if btn == 3:
                self._wait_since_last()
                self.events.append({"type": "mouseup", "x": rx, "y": ry, "button": btn})
            return

        self._flush_chars()
        self._wait_since_last()
        if btn == 3:
            # Right-click: take screenshot synchronously so it lands in events
            # between mousedown and mouseup (async queuing would place it after mouseup).
            self.events.append({"type": "mousedown", "x": rx, "y": ry, "button": btn})
            time.sleep(self.SCREENSHOT_DELAY)
            self._take_screenshot(self._next_label())
        else:
            self.events.append({"type": "click", "x": rx, "y": ry, "button": btn})
            self._queue_screenshot()

    # ── main loop ─────────────────────────────────────────────────────────────

    def run(self, proc):
        """Record until the app process exits or Ctrl+F12 is pressed."""
        kb_listener = kb.Listener(on_press=self.on_press, on_release=self.on_release)
        ms_listener = ms.Listener(on_click=self.on_click)
        kb_listener.start()
        ms_listener.start()

        print("  Recording… interact with the app.  Press Ctrl+F12 or close the app to stop.")

        # Take initial screenshot
        time.sleep(0.5)
        self._take_screenshot("initial")

        while proc.poll() is None and not self._stop:
            if self._pending_shot:
                self._pending_shot = False
                time.sleep(self.SCREENSHOT_DELAY)
                if proc.poll() is None:
                    # Flush any accumulated chars before screenshotting
                    label = self._next_label()
                    self._take_screenshot(label)
            time.sleep(0.05)

        kb_listener.stop()
        ms_listener.stop()
        self._flush_chars()

    def _next_label(self):
        # Derive a label from the last event
        if self.events:
            last = self.events[-1]
            t = last.get("type", "")
            if t == "key":
                return last["key"].replace("+", "_").replace(" ", "_")
            if t == "type":
                return "typed_" + re.sub(r"[^a-z0-9]", "", last["text"].lower())[:12]
            if t == "click":
                return f"click_{last['x']}_{last['y']}"
        return "step"


# ── code generator ────────────────────────────────────────────────────────────

def _generate_script(name: str, events: list[dict]) -> str:
    # Track which screenshot events fall inside a button-3 mousedown/mouseup span
    in_b3 = False
    unstable_shots: set[int] = set()
    for i, ev in enumerate(events):
        if ev["type"] == "mousedown" and ev.get("button") == 3:
            in_b3 = True
        elif ev["type"] == "mouseup" and ev.get("button") == 3:
            in_b3 = False
        elif ev["type"] == "screenshot" and in_b3:
            unstable_shots.add(i)

    lines = [f'"""\nRecorded test: {name}\n"""\n\n\ndef run(h):']
    for i, ev in enumerate(events):
        t = ev["type"]
        if t == "screenshot":
            stable_kwarg = "" if i not in unstable_shots else ", stable=False"
            lines.append(f'    h.screenshot("{ev["name"]}"{stable_kwarg})')
        elif t == "type":
            text = ev["text"].replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'    h.type("{text}")')
        elif t == "key":
            lines.append(f'    h.key("{ev["key"]}")')
        elif t == "click":
            lines.append(f'    h.click({ev["x"]}, {ev["y"]}, button={ev["button"]})')
        elif t == "mousedown":
            lines.append(f'    h.mousedown({ev["x"]}, {ev["y"]}, button={ev["button"]})')
        elif t == "mouseup":
            lines.append(f'    h.mouseup({ev["x"]}, {ev["y"]}, button={ev["button"]})')
        elif t == "wait":
            lines.append(f'    h.wait({ev["secs"]})')
    return "\n".join(lines) + "\n"


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python tests/record.py <test_name>")
        sys.exit(1)

    raw_name  = sys.argv[1].lower().replace(" ", "_")
    num       = _next_test_number()
    full_name = f"test_{num:02d}_{raw_name}"
    out_path  = _SCRIPTS / f"{full_name}.py"

    print(f"\n  Test name : {full_name}")
    print(f"  Output    : {out_path}")
    print(f"\n  Launching app…")

    _make_test_settings()
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(_TEST_CFG.parent)
    env["KITCHENSEARCH_NO_GRAB"] = "1"
    if os.environ.get("KITCHENSEARCH_SHOW_BANNER") == "1":
        env["KITCHENSEARCH_SHOW_BANNER"] = "1"
    if os.environ.get("KITCHENSEARCH_CACHE_DIR"):
        env["XDG_CACHE_HOME"] = os.environ["KITCHENSEARCH_CACHE_DIR"]

    proc = subprocess.Popen(
        [str(_REPO / ".venv" / "bin" / "python3"), str(_REPO / "emoji-picker-tk.py")],
        env=env, cwd=str(_REPO),
    )

    # Wait for window
    wid = None
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        time.sleep(0.25)
        wid = _find_window()
        if wid:
            break

    if not wid:
        print("ERROR: app window did not appear.")
        proc.terminate()
        sys.exit(1)

    recorder = Recorder(wid)
    recorder.run(proc)

    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=5)

    # Write test script
    script = _generate_script(raw_name, recorder.events)
    out_path.write_text(script)
    print(f"\n  Wrote {out_path}")

    # Write companion settings JSON (captures the settings used during recording)
    from harness import _DEFAULT_TEST_SETTINGS
    recorded_settings = dict(_DEFAULT_TEST_SETTINGS)
    if os.environ.get("KITCHENSEARCH_SHOW_BANNER") == "1":
        recorded_settings["hide_ads"] = False
        recorded_settings["_show_banner"] = True
    if os.environ.get("KITCHENSEARCH_CACHE_DIR"):
        recorded_settings["_cache_dir"] = os.environ["KITCHENSEARCH_CACHE_DIR"]
    companion_path = _SCRIPTS / f"{full_name}.json"
    companion_path.write_text(json.dumps(recorded_settings, indent=2))
    print(f"  Wrote {companion_path}")

    # Re-run the new test to verify + produce GIF
    print(f"\n  Re-running {full_name} to verify…\n")
    result = subprocess.run(
        ["xvfb-run", "-a",
         str(_REPO / ".venv" / "bin" / "python3"),
         str(_TESTS_DIR / "run_tests.py"),
         "--update-baseline",
         "--test", full_name],
        cwd=str(_REPO),
    )

    # Find and print the GIF path
    gif = _TESTS_DIR / "baseline_unapproved" / full_name / "recording.gif"
    if gif.exists():
        print(f"\n  GIF → {gif}")
        print(f"\n  View with:  firefox {gif}")
        print(f"\n  Approve with:  python tests/approve.py {full_name}")
    else:
        print("  (no GIF found — re-run may have failed)")


if __name__ == "__main__":
    main()
