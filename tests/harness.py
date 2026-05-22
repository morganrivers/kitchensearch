"""
Core test harness: launch the app, capture screenshots, send keyboard/mouse input.

Usage:
    with TestHarness(run_dir="tests/runs/001") as h:
        h.screenshot("01_initial")
        h.type("fire")
        h.screenshot("02_typed_fire")
        h.key("Return")
        h.screenshot("03_result")
    h.make_gif("tests/runs/001/recording.gif")
"""

import os
import subprocess
import sys
import time
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Add tests/ dir to path so widget_dump is importable
sys.path.insert(0, str(Path(__file__).parent))
from widget_dump import fetch_dump

_REPO = Path(__file__).parent.parent
_TEST_CONFIG_DIR = Path(__file__).parent / "_test_config" / "kitchensearch"
_SCRIPTS_DIR = Path(__file__).parent / "scripts"

# Stable defaults for all tests — never touches the user's real settings
_DEFAULT_TEST_SETTINGS = {
    "notify_on_copy": False,
    "exit_on_select": True,
    "show_keyword":   True,
    "show_semantic":  True,
    "show_combo":     True,
    "show_story":     True,
    "floating":       True,
    "frameless":      False,
    "dark_mode":      False,
    "hide_ads":       True,   # banner hidden by default; set false in companion JSON or KITCHENSEARCH_SHOW_BANNER=1
}


def _make_test_settings(companion: Path | None = None):
    """Merge defaults + per-test companion JSON + env overrides, write to test config dir."""
    _TEST_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    settings = dict(_DEFAULT_TEST_SETTINGS)

    if companion and companion.exists():
        overrides = json.loads(companion.read_text())
        settings.update(overrides)

    if os.environ.get("KITCHENSEARCH_SHOW_BANNER") == "1":
        settings["hide_ads"] = False

    (_TEST_CONFIG_DIR / "picker-settings.json").write_text(json.dumps(settings, indent=2))


class TestHarness:
    WINDOW_TITLE = "Kitchen Search"
    STARTUP_TIMEOUT = 12.0
    STARTUP_SETTLE = 1.5

    def __init__(self, run_dir: str | Path, settings_path: Path | None = None):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._settings_path = settings_path
        self._proc = None
        self._wid = None
        self._shots: list[tuple[str, Path]] = []
        self._widget_server_ready = False
        self._clipboard_baseline: bytes | None = self._read_clipboard_raw()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def launch(self):
        _make_test_settings(self._settings_path)
        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(_TEST_CONFIG_DIR.parent)
        env["KITCHENSEARCH_NO_GRAB"] = "1"
        env["KITCHENSEARCH_NO_DAEMON"] = "1"
        env["KITCHENSEARCH_NO_BLINK"] = "1"
        # Pin the cache/thumbs dir for reproducibility (AB testing)
        # Priority: env var > companion JSON "_cache_dir" key
        cache_dir = os.environ.get("KITCHENSEARCH_CACHE_DIR")
        if not cache_dir and self._settings_path and self._settings_path.exists():
            companion = json.loads(self._settings_path.read_text())
            cache_dir = companion.get("_cache_dir")
        if cache_dir:
            env["XDG_CACHE_HOME"] = cache_dir

        cmd = [sys.executable, str(_REPO / "emoji-picker-tk.py")]
        self._proc = subprocess.Popen(cmd, env=env, cwd=str(_REPO))

        deadline = time.monotonic() + self.STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(0.25)
            wid = self._find_window()
            if wid:
                self._wid = wid
                time.sleep(self.STARTUP_SETTLE)
                # Probe widget-dump server
                from widget_dump import SOCK_PATH
                for _ in range(10):
                    if SOCK_PATH.exists():
                        self._widget_server_ready = True
                        break
                    time.sleep(0.3)
                return self

        raise RuntimeError(
            f"Window '{self.WINDOW_TITLE}' did not appear within {self.STARTUP_TIMEOUT}s"
        )

    def close(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._wid = None

    def __enter__(self):
        return self.launch()

    def __exit__(self, *_):
        self.close()

    # ── window utilities ──────────────────────────────────────────────────────

    def _find_window(self) -> str | None:
        try:
            out = subprocess.check_output(
                ["xdotool", "search", "--name", self.WINDOW_TITLE],
                stderr=subprocess.DEVNULL,
            ).decode().strip()
            ids = [l.strip() for l in out.splitlines() if l.strip()]
            return ids[-1] if ids else None
        except subprocess.CalledProcessError:
            return None

    def _window_geometry(self) -> tuple[int, int, int, int]:
        """Return (x, y, width, height) of the window."""
        out = subprocess.check_output(
            ["xdotool", "getwindowgeometry", self._wid]
        ).decode()
        for line in out.splitlines():
            if "Position:" in line:
                coords = line.split(":")[1].strip().split(" ")[0]
                x, y = map(int, coords.split(","))
            if "Geometry:" in line:
                dims = line.split(":")[1].strip()
                w, h = map(int, dims.split("x"))
        return x, y, w, h

    # ── actions ───────────────────────────────────────────────────────────────

    def _capture(self, path: Path):
        subprocess.run(
            ["import", "-window", self._wid, str(path)],
            check=True, stderr=subprocess.DEVNULL,
            timeout=5,
        )

    def _capture_stable(self, path: Path, stable_for: float = 0.3, timeout: float = 6.0):
        """Capture once the window has stopped changing for stable_for seconds."""
        from PIL import Image
        import numpy as np

        tmp_a = self.run_dir / "_stable_a.png"
        tmp_b = self.run_dir / "_stable_b.png"
        deadline     = time.monotonic() + timeout
        stable_since = None
        t0           = time.monotonic()
        iters        = 0

        window_gone = False
        try:
            self._capture(tmp_a)
        except subprocess.TimeoutExpired:
            window_gone = True

        if not window_gone:
            while time.monotonic() < deadline:
                time.sleep(0.1)
                try:
                    self._capture(tmp_b)
                except subprocess.TimeoutExpired:
                    window_gone = True
                    break
                a = np.asarray(Image.open(tmp_a))
                b = np.asarray(Image.open(tmp_b))
                iters += 1
                if np.array_equal(a, b):
                    if stable_since is None:
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= stable_for:
                        break
                else:
                    stable_since = None
                tmp_a.write_bytes(tmp_b.read_bytes())

        elapsed = time.monotonic() - t0
        timed_out = not window_gone and time.monotonic() >= deadline
        suffix = " WINDOW GONE" if window_gone else (" TIMED OUT" if timed_out else "")
        print(f"    [stable] {path.name}: {elapsed:.2f}s, {iters} iters{suffix}", flush=True)

        if tmp_b.exists():
            tmp_b.replace(path)
        elif tmp_a.exists():
            tmp_a.replace(path)
        for f in (tmp_a, tmp_b):
            f.unlink(missing_ok=True)

    def screenshot(self, name: str) -> Path:
        """Capture a stable (settled) window screenshot + clipboard + widget dump."""
        path = self.run_dir / f"{name}.png"
        self._capture_stable(path)
        self._shots.append((name, path))

        # Clipboard snapshot
        self._capture_clipboard(name)

        # Widget geometry dump
        if self._widget_server_ready:
            try:
                dump = fetch_dump()
                (self.run_dir / f"{name}.json").write_text(json.dumps(dump, indent=2))
            except Exception as exc:
                print(f"  [warn] widget dump failed for {name}: {exc}")

        return path

    def _read_clipboard_raw(self) -> bytes | None:
        """Return raw clipboard bytes (image preferred, then text), or None if empty."""
        r = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
            capture_output=True,
        )
        if r.returncode == 0 and r.stdout:
            return b"img:" + r.stdout
        r = subprocess.run(
            ["xclip", "-selection", "clipboard", "-o"],
            capture_output=True,
        )
        if r.returncode == 0 and r.stdout:
            return b"txt:" + r.stdout
        return None

    def _capture_clipboard(self, name: str):
        """Save clipboard only if it changed since harness launch."""
        current = self._read_clipboard_raw()
        if current is None or current == self._clipboard_baseline:
            return
        data = current[4:]  # strip the img:/txt: prefix
        if current.startswith(b"img:"):
            (self.run_dir / f"{name}_clipboard.png").write_bytes(data)
        else:
            (self.run_dir / f"{name}_clipboard.txt").write_bytes(data)

    def wait(self, seconds: float):
        time.sleep(seconds)

    def key(self, key_name: str):
        """Send a key press (e.g. 'Return', 'Up', 'Down', 'Escape', 'ctrl+a')."""
        result = subprocess.run(
            ["xdotool", "key", "--window", self._wid, "--clearmodifiers", key_name],
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            # Window may have closed (e.g. Escape dismissed it) — update wid
            wid = self._find_window()
            if wid:
                self._wid = wid

    def type(self, text: str, delay_ms: int = 40):
        """Type a string into the focused widget."""
        subprocess.run(
            [
                "xdotool", "type", "--window", self._wid,
                "--clearmodifiers", f"--delay={delay_ms}", "--", text,
            ],
            check=True,
        )

    def click(self, x: int, y: int, button: int = 1):
        """Click at pixel (x, y) relative to the window's top-left corner."""
        subprocess.run(
            ["xdotool", "mousemove", "--window", self._wid, "--sync", str(x), str(y)],
            check=True,
        )
        time.sleep(0.05)
        subprocess.run(["xdotool", "click", str(button)], check=True)

    def focus(self):
        subprocess.run(
            ["xdotool", "windowfocus", "--sync", self._wid], check=True
        )

    # ── GIF export ────────────────────────────────────────────────────────────

    def make_gif(self, output_path: str | Path, frame_duration_ms: int = 800):
        """Stitch all captured screenshots into an animated GIF."""
        output_path = Path(output_path)
        frames: list[Image.Image] = []

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        except OSError:
            font = ImageFont.load_default()

        for name, path in self._shots:
            if not path.exists():
                continue
            img = Image.open(path).convert("RGBA")
            label_h = 22
            labeled = Image.new("RGBA", (img.width, img.height + label_h), (20, 20, 20, 255))
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
