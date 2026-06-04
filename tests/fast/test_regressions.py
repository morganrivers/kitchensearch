"""Tests that would have failed against the bugs documented in commit history.

Each test pins a specific regression so the bug can't silently come back:
  * picker_ui called _kill_daemon without importing it (Cancel button crashed).
  * _write_pid_record wrote the PID file in place (concurrent readers saw
    partial JSON during a daemon spawn).
  * The daemon advertised pct=100 BEFORE binding the IPC socket, so the
    picker's zombie-killer SIGTERM'd a healthy daemon mid-startup.
"""
import ast
import builtins
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.argv[0] = str(_REPO_ROOT / "emoji-picker-tk.py")
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import picker_utils  # noqa: E402


class PickerUiImportTest(unittest.TestCase):
    def test_kill_daemon_is_resolvable_in_picker_ui(self):
        import picker_ui
        self.assertTrue(callable(getattr(picker_ui, "_kill_daemon", None)),
                        "picker_ui calls _kill_daemon but never imported it; "
                        "the Cancel button on the loading dialog crashes with NameError")

    def test_every_underscore_call_in_picker_ui_is_bound(self):
        src = (_REPO_ROOT / "picker_ui.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        defined = set(dir(builtins))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for n in node.names:
                    defined.add(n.asname or n.name)
            elif isinstance(node, ast.Import):
                for n in node.names:
                    defined.add((n.asname or n.name).split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                    ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        defined.add(t.id)
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        missing = sorted(n for n in called
                         if n.startswith("_") and n not in defined)
        self.assertEqual(missing, [],
                         f"picker_ui calls {missing} but does not import them")


class AtomicPidWriteTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._pid_path = Path(tmp.name) / "split-daemon.pid"
        p = mock.patch.object(picker_utils, "DAEMON_PID", self._pid_path)
        p.start()
        self.addCleanup(p.stop)

    def test_write_uses_tmp_path_then_rename(self):
        write_targets = []
        orig_write = Path.write_text

        def trace(self_, *a, **kw):
            write_targets.append(Path(self_))
            return orig_write(self_, *a, **kw)

        with mock.patch.object(Path, "write_text", trace):
            picker_utils._write_pid_record(99999)

        self.assertNotIn(self._pid_path, write_targets,
                         "_write_pid_record wrote directly to DAEMON_PID — "
                         "must write to a tmp path then rename to be atomic")
        self.assertTrue(any(p.suffix == ".tmp" for p in write_targets),
                        "_write_pid_record must stage the write in a .tmp file")

    def test_old_content_is_never_truncated_to_empty_before_rename(self):
        good = json.dumps({"pid": 11111, "start_time": 22222})
        self._pid_path.write_text(good, encoding="utf-8")

        observed = []
        orig_write = Path.write_text

        def write_then_snapshot(self_, *a, **kw):
            r = orig_write(self_, *a, **kw)
            try:
                observed.append(self._pid_path.read_text(encoding="utf-8"))
            except OSError:
                observed.append(None)
            return r

        with mock.patch.object(Path, "write_text", write_then_snapshot):
            picker_utils._write_pid_record(99999)

        for snap in observed:
            self.assertEqual(
                snap, good,
                "DAEMON_PID content changed mid-call; concurrent readers "
                "could see a partial or empty file")
        final_pid, _ = picker_utils._read_pid_record()
        self.assertEqual(final_pid, 99999)


class DaemonReadyOrderingTest(unittest.TestCase):
    """The daemon must not advertise pct=100 until the IPC socket is bound.

    Tested two ways: source inspection (deterministic) and a real spawn
    (catches future implementations that still race).
    """

    DAEMON_SRC = _REPO_ROOT / "emoji-split-daemon.py"

    def test_listener_bind_precedes_ready_status_in_source(self):
        tree = ast.parse(self.DAEMON_SRC.read_text(encoding="utf-8"))
        main = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "main")

        listener_lines, ready_lines = [], []
        for n in ast.walk(main):
            if not isinstance(n, ast.Call):
                continue
            if isinstance(n.func, ast.Name) and n.func.id == "Listener":
                listener_lines.append(n.lineno)
            if isinstance(n.func, ast.Name) and n.func.id == "_write_status":
                args = n.args
                if (len(args) >= 2
                        and isinstance(args[0], ast.Constant)
                        and args[0].value == "Ready"
                        and isinstance(args[1], ast.Constant)
                        and args[1].value == 100):
                    ready_lines.append(n.lineno)

        self.assertTrue(listener_lines, "Listener(...) not found in main()")
        self.assertTrue(ready_lines, '_write_status("Ready", 100) not found in main()')
        self.assertLess(
            min(listener_lines), min(ready_lines),
            'Daemon advertises pct=100 BEFORE binding the IPC socket; the '
            'picker zombie-killer can SIGTERM a healthy daemon in this window.')

    def test_real_daemon_socket_accepts_whenever_status_says_ready(self):
        from multiprocessing.connection import Client

        with TemporaryDirectory() as tmp:
            cache_root = Path(tmp)
            daemon_cache = cache_root / "kitchensearch"
            daemon_cache.mkdir(parents=True)
            status_path = daemon_cache / "split-daemon-loading.json"
            sock_path   = daemon_cache / "split-daemon.sock"
            log_path    = daemon_cache / "daemon.log"

            env = {**os.environ, "XDG_CACHE_HOME": str(cache_root)}
            with open(log_path, "wb") as log:
                proc = subprocess.Popen(
                    [sys.executable, str(self.DAEMON_SRC)],
                    env=env, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        s = json.loads(status_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        s = {}
                    if float(s.get("pct", 0)) >= 100:
                        break
                    if proc.poll() is not None:
                        self.fail(f"daemon exited early; log:\n"
                                  f"{log_path.read_text(errors='replace')}")
                    time.sleep(0.01)
                else:
                    self.fail("daemon never reached pct=100 within 15s")

                try:
                    conn = Client(str(sock_path))
                    conn.close()
                except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
                    self.fail(
                        f"daemon advertised pct=100 but IPC socket is not "
                        f"accepting connections: {e!r}")
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    unittest.main()
