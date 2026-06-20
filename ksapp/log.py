import os, sys, threading, time, traceback, tempfile
from pathlib import Path

# Test observability: when KITCHENSEARCH_TEST_EVENTS is set, emit structured
# one-line events to stderr (which the test harnesses drain in real time). This
# lets a test both *synchronise* on app readiness and *see* what the app did —
# e.g. which row was selected and whether a copy fired — across platforms.
_EVENTS_ENABLED = bool(os.environ.get("KITCHENSEARCH_TEST_EVENTS"))


def _event(msg):
    if not _EVENTS_ENABLED:
        return
    try:
        sys.stderr.write(f"KSEVENT {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


_DBG_ENABLED  = "--logging" in sys.argv
_DBG_LOG_PATH = Path(tempfile.gettempdir()) / "kitchensearch-debug.log"
if _DBG_ENABLED:
    _DBG_LOG_PATH.write_text("", encoding="utf-8")  # truncate on each launch
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
        with open(_DBG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
