"""Shared readiness logic for the test harnesses.

The single thing every OS must do *consistently* before capturing a screen is
wait for the search daemon (the model) to finish loading. That load is the one
variable-latency step that differs by machine and OS, so capturing before it's
ready is the main source of cross-platform divergence — one runner shows the
loading bar while another already shows results.

We reuse the app's own readiness check: connecting to the daemon's IPC address.
That check is cross-platform by construction (a Unix socket on Linux/macOS, a
named pipe on Windows, both via multiprocessing) and is reachable from this
separate harness process, so Linux, Windows and macOS all gate on it identically.

Deliberately *not* per-keystroke: typing doesn't wait on the daemon, and the
tests don't screenshot every letter. The daemon gate is cheap once the model is
loaded (a connect/close), so calling it before every screenshot is effectively
free after the first wait.
"""
import time


def daemon_ready() -> bool | None:
    """True/False once we can tell, or None if the app isn't importable here
    (caller should then fall back to its timing heuristic)."""
    try:
        from ksapp.picker_utils import _daemon_ready
    except Exception:
        return None
    try:
        return _daemon_ready()
    except Exception:
        return None


def wait_for_daemon_ready(timeout: float = 30.0) -> bool:
    """Block until the daemon reports ready. Returns True if it became ready,
    False on timeout or if readiness can't be determined here."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = daemon_ready()
        if r is None:
            return False
        if r:
            return True
        time.sleep(0.2)
    return False
