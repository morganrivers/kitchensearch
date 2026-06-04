import sys, threading, time, traceback, tempfile
from pathlib import Path

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
