"""
Widget geometry dump: walks the live tkinter widget tree and serialises
position/size/text/color for every widget into a JSON snapshot.

Two halves:
  - Server side (embedded in the app when KITCHENSEARCH_NO_GRAB=1):
        start_test_server(root)   — starts a background thread
  - Client side (called from the test harness):
        fetch_dump(sock_path)     → dict
        diff_dumps(old, new)      → list[str]  (human-readable change lines)
"""

import json
import os
import socket
import threading
import tkinter as tk
from pathlib import Path

SOCK_PATH = Path("/tmp/kitchensearch-test.sock")

# ── server (runs inside the app process) ──────────────────────────────────────

def _walk(widget: tk.Widget) -> dict:
    """Recursively dump a widget's geometry, text, and children."""
    d: dict = {
        "class":   widget.winfo_class(),
        "mapped":  bool(widget.winfo_ismapped()),
        "x":       widget.winfo_x(),
        "y":       widget.winfo_y(),
        "w":       widget.winfo_width(),
        "h":       widget.winfo_height(),
    }

    for attr in ("text", "bg", "fg", "font", "relief", "state"):
        try:
            val = widget.cget(attr)
            if val not in ("", None):
                d[attr] = str(val)
        except (tk.TclError, ValueError):
            pass

    # Entry / Text content
    if d["class"] == "Entry":
        try:
            d["value"] = widget.get()
        except Exception:
            pass
    elif d["class"] == "Text":
        try:
            d["value"] = widget.get("1.0", "end-1c")[:200]
        except Exception:
            pass

    # Canvas items (count + bounding boxes for first few)
    if d["class"] == "Canvas":
        try:
            ids = widget.find_all()
            d["canvas_items"] = len(ids)
            sample = []
            for iid in ids[:10]:
                try:
                    sample.append({
                        "type":   widget.type(iid),
                        "coords": widget.coords(iid),
                    })
                except Exception:
                    pass
            d["canvas_sample"] = sample
        except Exception:
            pass

    children: dict[str, dict] = {}
    for child in widget.winfo_children():
        key = str(child).split(".")[-1] or str(child)
        children[key] = _walk(child)
    if children:
        d["children"] = children

    return d


def _handle_client(conn: socket.socket, root: tk.Widget):
    try:
        data = conn.recv(64).decode().strip()
        if data == "DUMP":
            dump = root.after(0, lambda: None)   # ensure we're on main thread
            # We can't call tkinter from a thread; schedule and wait
            result_holder: list = []
            event = threading.Event()

            def _do_dump():
                result_holder.append(_walk(root))
                event.set()

            root.after(0, _do_dump)
            event.wait(timeout=3.0)
            payload = json.dumps(result_holder[0] if result_holder else {})
            conn.sendall((payload + "\n").encode())
    finally:
        conn.close()


def start_test_server(root: tk.Widget):
    """Start a Unix-socket server in a daemon thread.  Call once after mainloop starts."""
    sock_path = str(SOCK_PATH)
    if os.path.exists(sock_path):
        os.unlink(sock_path)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(4)

    def _serve():
        while True:
            try:
                conn, _ = srv.accept()
                threading.Thread(
                    target=_handle_client, args=(conn, root), daemon=True
                ).start()
            except OSError:
                break

    t = threading.Thread(target=_serve, daemon=True)
    t.start()


# ── client (called from test harness) ─────────────────────────────────────────

def fetch_dump(timeout: float = 3.0) -> dict:
    sock_path = str(SOCK_PATH)
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(timeout)
    conn.connect(sock_path)
    conn.sendall(b"DUMP\n")
    buf = b""
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
        if b"\n" in buf:
            break
    conn.close()
    return json.loads(buf.decode().strip())


# ── diff ──────────────────────────────────────────────────────────────────────

def _flatten(tree: dict, prefix: str = "") -> dict[str, str]:
    """Flatten widget tree to {path: repr} for line-level comparison."""
    out: dict[str, str] = {}
    children = tree.pop("children", {})
    # Represent this node (without children) as a sorted key=value string
    attrs = "  ".join(f"{k}={v}" for k, v in sorted(tree.items()) if k != "children")
    out[prefix or "(root)"] = attrs
    for name, subtree in children.items():
        out.update(_flatten(subtree, f"{prefix}/{name}" if prefix else name))
    return out


def diff_dumps(baseline: dict, current: dict) -> list[str]:
    """
    Return human-readable lines describing what changed between two widget dumps.
    Each line starts with '+' (added), '-' (removed), or '~' (modified).
    """
    import copy
    base_flat = _flatten(copy.deepcopy(baseline))
    curr_flat = _flatten(copy.deepcopy(current))

    lines: list[str] = []
    all_keys = sorted(set(base_flat) | set(curr_flat))
    for key in all_keys:
        b = base_flat.get(key)
        c = curr_flat.get(key)
        if b is None:
            lines.append(f"+ {key}  →  {c}")
        elif c is None:
            lines.append(f"- {key}")
        elif b != c:
            # Show only changed fields
            b_parts = dict(kv.split("=", 1) for kv in b.split("  ") if "=" in kv)
            c_parts = dict(kv.split("=", 1) for kv in c.split("  ") if "=" in kv)
            diffs = []
            for k in sorted(set(b_parts) | set(c_parts)):
                bv, cv = b_parts.get(k, "∅"), c_parts.get(k, "∅")
                if bv != cv:
                    diffs.append(f"{k}: {bv} → {cv}")
            if diffs:
                lines.append(f"~ {key}  |  " + "  |  ".join(diffs))
    return lines
