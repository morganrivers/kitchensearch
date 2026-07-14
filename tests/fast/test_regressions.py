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
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ksapp import picker_utils  # noqa: E402


class PickerUiImportTest(unittest.TestCase):
    def test_kill_daemon_is_resolvable_in_picker_ui(self):
        from ksapp import picker_ui
        self.assertTrue(callable(getattr(picker_ui, "_kill_daemon", None)),
                        "picker_ui calls _kill_daemon but never imported it; "
                        "the Cancel button on the loading dialog crashes with NameError")

    def test_every_underscore_call_in_picker_ui_is_bound(self):
        src = (_REPO_ROOT / "ksapp" / "picker_ui.py").read_text(encoding="utf-8")
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

    DAEMON_SRC = _REPO_ROOT / "ksapp" / "emoji_split_daemon.py"

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
        import uuid

        # ignore_cleanup_errors: on Windows the subprocess holds daemon.log
        # briefly after terminate() while the kernel finalises the handle;
        # let TemporaryDirectory swallow that PermissionError instead of
        # failing the test with a spurious teardown error.
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            daemon_cache = Path(tmp)
            # KITCHENSEARCH_CACHE_DIR is honoured by both emoji_split_daemon.py
            # and picker_utils.py. Redirecting cache via LOCALAPPDATA/
            # XDG_CACHE_HOME doesn't work on Windows because platformdirs
            # reads CSIDL_LOCAL_APPDATA via Win32 API directly.
            env = {**os.environ,
                   "PYTHONPATH": str(_REPO_ROOT),
                   "KITCHENSEARCH_CACHE_DIR": str(daemon_cache)}
            status_path = daemon_cache / "split-daemon-loading.json"
            log_path    = daemon_cache / "daemon.log"

            # IPC endpoint the daemon will bind — named pipe on Windows,
            # Unix socket on POSIX. Use a per-test-run suffix so we don't
            # collide with a real running kitchensearch install on the same
            # machine (which would otherwise hijack our pipe/socket).
            slug = uuid.uuid4().hex[:8]
            if sys.platform == "win32":
                import getpass
                ipc_address = (r"\\.\pipe\kitchensearch-"
                               + getpass.getuser() + "-" + slug)
                # Also override the Windows singleton mutex so the test
                # doesn't collide with a running production daemon (which
                # would make our subprocess exit immediately as "already
                # running").
                mutex_name = r"Local\KitchenSearchSplitDaemonTest-" + slug
            else:
                ipc_address = str(daemon_cache / "split-daemon.sock")
                mutex_name = None

            popen_kwargs = {"env": env, "stderr": subprocess.STDOUT}
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = (
                    subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                popen_kwargs["start_new_session"] = True

            # Open the log, hand its fd to the child, then close our copy so
            # the parent isn't holding a lock during rmtree on teardown.
            log = open(log_path, "wb")
            try:
                popen_kwargs["stdout"] = log
                if sys.platform == "win32":
                    # Bootstrap script that patches the daemon's mutex and
                    # IPC address (so it uses our per-test-run names) then
                    # runs main(). Can't pass via argv — daemon doesn't
                    # accept any.
                    bootstrap = (
                        "import ksapp.emoji_split_daemon as d\n"
                        f'd._WIN_SINGLETON_MUTEX = r"{mutex_name}"\n'
                        f'd.IPC_ADDRESS = r"{ipc_address}"\n'
                        "d.main()\n"
                    )
                    proc = subprocess.Popen(
                        [sys.executable, "-c", bootstrap], **popen_kwargs)
                else:
                    proc = subprocess.Popen(
                        [sys.executable, str(self.DAEMON_SRC)], **popen_kwargs)
            finally:
                log.close()

            try:
                # Windows model load with a cold OS file cache can exceed
                # 15 s on the first run; give the daemon 60 s.
                timeout_s = 60 if sys.platform == "win32" else 15
                deadline = time.monotonic() + timeout_s
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
                    time.sleep(0.05)
                else:
                    self.fail(
                        f"daemon never reached pct=100 within {timeout_s}s; log:\n"
                        f"{log_path.read_text(errors='replace')}")

                try:
                    conn = Client(ipc_address)
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
                    proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
