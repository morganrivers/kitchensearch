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


_PID_MAX = 4194304
try:
    _PID_MAX = int(Path("/proc/sys/kernel/pid_max").read_text().strip())
except Exception:
    pass


def _find_unused_pid():
    # Windows: os.kill(pid, 0) is not a portable existence check — it raises
    # OSError WinError 87 (ERROR_INVALID_PARAMETER) unconditionally. Use
    # OpenProcess instead: it fails with ERROR_INVALID_PARAMETER only for
    # PIDs the OS has never issued, and with ERROR_ACCESS_DENIED for live
    # PIDs we can't query. We want the "never issued" case.
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_INVALID_PARAMETER = 87
        kernel32 = ctypes.windll.kernel32
        for pid in range(_PID_MAX - 1, _PID_MAX - 2000, -1):
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h:
                kernel32.CloseHandle(h)
                continue
            if kernel32.GetLastError() == ERROR_INVALID_PARAMETER:
                return pid
        raise RuntimeError("could not find an unused pid (windows)")
    for pid in range(_PID_MAX - 1, _PID_MAX - 2000, -1):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            continue
    raise RuntimeError("could not find an unused pid")


class _DaemonStateBase(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache = Path(tmp.name)
        self._pid_path    = cache / "split-daemon.pid"
        self._status_path = cache / "split-daemon-loading.json"
        self._socket_path = cache / "split-daemon.sock"

        for p in (
            mock.patch.object(picker_utils, "DAEMON_PID",    self._pid_path),
            mock.patch.object(picker_utils, "DAEMON_STATUS", self._status_path),
            mock.patch.object(picker_utils, "IPC_ADDRESS",   str(self._socket_path)),
            mock.patch.object(picker_utils, "IS_NAMED_PIPE", False),
        ):
            p.start()
            self.addCleanup(p.stop)

        self._children = []
        self.addCleanup(self._cleanup_children)

    def _cleanup_children(self):
        for p in self._children:
            if p.poll() is None:
                try:
                    p.terminate()
                    p.wait(timeout=2)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass

    def _spawn_dummy(self):
        script = (
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))\n"
            "print('READY', flush=True)\n"
            "time.sleep(60)\n"
        )
        p = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._children.append(p)
        p.stdout.readline()
        p.stdout.close()
        return p

    def _spawn_zombie(self):
        """A child that has exited but not been reaped: state 'Z' (defunct).

        os.kill(pid, 0) still succeeds and /proc/<pid>/stat stays readable, so a
        naive liveness check mistakes the corpse for a running daemon. This is
        exactly the state a daemon leaves behind when it crashes at import time.
        Linux-only (relies on /proc).
        """
        p = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
        self._children.append(p)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                state = (Path(f"/proc/{p.pid}/stat")
                         .read_text().rsplit(")", 1)[1].split()[0])
            except OSError:
                break
            if state == "Z":
                return p
            time.sleep(0.01)
        self.fail("could not create a zombie child")

    def _write_pid(self, value):
        self._pid_path.write_text(str(value), encoding="utf-8")

    def _record_daemon(self, pid, start_time=...):
        if start_time is ...:
            start_time = picker_utils._proc_start_time(pid)
        import json as _json
        self._pid_path.write_text(
            _json.dumps({"pid": pid, "start_time": start_time}),
            encoding="utf-8",
        )


class DaemonAliveTest(_DaemonStateBase):
    def test_no_pid_file(self):
        self.assertFalse(picker_utils._daemon_alive())

    def test_garbage_pid_file_removed(self):
        self._write_pid("not-an-integer")
        self.assertFalse(picker_utils._daemon_alive())
        self.assertFalse(self._pid_path.exists())

    def test_empty_pid_file_removed(self):
        self._pid_path.write_text("", encoding="utf-8")
        self.assertFalse(picker_utils._daemon_alive())
        self.assertFalse(self._pid_path.exists())

    def test_pid_for_dead_process_is_cleaned_up(self):
        self._write_pid(_find_unused_pid())
        self.assertFalse(picker_utils._daemon_alive())
        self.assertFalse(self._pid_path.exists())

    def test_pid_reuse_is_detected_and_cleaned_up(self):
        p = self._spawn_dummy()
        real_start = picker_utils._proc_start_time(p.pid)
        self.assertIsNotNone(real_start)
        self._record_daemon(p.pid, start_time=real_start + 1)
        self.assertFalse(picker_utils._daemon_alive())
        self.assertFalse(self._pid_path.exists())
        self.assertIsNone(p.poll(), "unrelated process must not be signalled")

    def test_genuine_daemon_pid_returns_true(self):
        p = self._spawn_dummy()
        self._record_daemon(p.pid)
        self.assertTrue(picker_utils._daemon_alive())
        self.assertIsNone(p.poll())

    def test_legacy_bare_int_pid_file_still_accepted(self):
        p = self._spawn_dummy()
        self._write_pid(p.pid)
        self.assertTrue(picker_utils._daemon_alive())
        self.assertIsNone(p.poll())

    def test_zombie_loaded_without_socket_is_killed(self):
        p = self._spawn_dummy()
        self._record_daemon(p.pid)
        self._status_path.write_text(json.dumps({"pct": 100}), encoding="utf-8")
        self.assertFalse(self._socket_path.exists())

        self.assertFalse(picker_utils._daemon_alive())
        self.assertFalse(self._pid_path.exists())
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.fail("zombie daemon was not signalled")

    def test_loading_without_socket_is_not_killed(self):
        p = self._spawn_dummy()
        self._record_daemon(p.pid)
        self._status_path.write_text(json.dumps({"pct": 42}), encoding="utf-8")
        self.assertTrue(picker_utils._daemon_alive())
        self.assertTrue(self._pid_path.exists())
        self.assertIsNone(p.poll())

    def test_missing_status_file_falls_through_to_true(self):
        p = self._spawn_dummy()
        self._record_daemon(p.pid)
        self.assertTrue(picker_utils._daemon_alive())
        self.assertIsNone(p.poll())

    def test_corrupt_status_file_falls_through_to_true(self):
        p = self._spawn_dummy()
        self._record_daemon(p.pid)
        self._status_path.write_text("{not json", encoding="utf-8")
        self.assertTrue(picker_utils._daemon_alive())
        self.assertIsNone(p.poll())

    def test_loaded_with_live_socket_stays_alive(self):
        p = self._spawn_dummy()
        self._record_daemon(p.pid)
        self._status_path.write_text(json.dumps({"pct": 100}), encoding="utf-8")
        self._socket_path.touch()
        self.assertTrue(picker_utils._daemon_alive())
        self.assertIsNone(p.poll())

    @unittest.skipUnless(sys.platform.startswith("linux"),
                         "zombie detection via /proc is linux-only")
    def test_zombie_with_stale_socket_is_not_alive(self):
        # Reproduces the production hang: the daemon crashed at import time,
        # leaving a defunct (zombie) process plus a stale socket file and a
        # stale "Ready" status from a prior run. os.kill(pid, 0) succeeds on the
        # zombie, so the old liveness check reported the dead daemon as alive and
        # the loading screen spun forever on a fake "Ready 100%".
        p = self._spawn_zombie()
        self._record_daemon(p.pid)
        self._status_path.write_text(json.dumps({"pct": 100}), encoding="utf-8")
        self._socket_path.touch()
        self.assertFalse(picker_utils._daemon_alive())
        self.assertFalse(self._pid_path.exists())


class ProcIdentityTest(_DaemonStateBase):
    def test_proc_start_time_returns_int_for_live_pid(self):
        p = self._spawn_dummy()
        st = picker_utils._proc_start_time(p.pid)
        self.assertIsInstance(st, int)
        self.assertGreater(st, 0)
        self.assertEqual(st, picker_utils._proc_start_time(p.pid))

    def test_proc_start_time_returns_none_for_dead_pid(self):
        self.assertIsNone(picker_utils._proc_start_time(_find_unused_pid()))

    def test_identity_matches_true_for_correct_start_time(self):
        p = self._spawn_dummy()
        st = picker_utils._proc_start_time(p.pid)
        self.assertTrue(picker_utils._identity_matches(p.pid, st))

    def test_identity_matches_false_for_wrong_start_time(self):
        p = self._spawn_dummy()
        st = picker_utils._proc_start_time(p.pid)
        self.assertFalse(picker_utils._identity_matches(p.pid, st + 1))

    def test_identity_matches_true_when_start_time_unknown(self):
        p = self._spawn_dummy()
        self.assertTrue(picker_utils._identity_matches(p.pid, None))


class PidRecordTest(_DaemonStateBase):
    def test_reads_new_json_format(self):
        self._pid_path.write_text(
            json.dumps({"pid": 12345, "start_time": 999}), encoding="utf-8")
        self.assertEqual(picker_utils._read_pid_record(), (12345, 999))

    def test_reads_legacy_bare_int_format(self):
        self._pid_path.write_text("12345", encoding="utf-8")
        self.assertEqual(picker_utils._read_pid_record(), (12345, None))

    def test_empty_file_returns_none(self):
        self._pid_path.write_text("", encoding="utf-8")
        self.assertEqual(picker_utils._read_pid_record(), (None, None))

    def test_garbage_returns_none(self):
        self._pid_path.write_text("not a pid", encoding="utf-8")
        self.assertEqual(picker_utils._read_pid_record(), (None, None))

    def test_missing_file_returns_none(self):
        self.assertEqual(picker_utils._read_pid_record(), (None, None))

    def test_write_record_round_trips(self):
        p = self._spawn_dummy()
        picker_utils._write_pid_record(p.pid)
        pid, st = picker_utils._read_pid_record()
        self.assertEqual(pid, p.pid)
        self.assertEqual(st, picker_utils._proc_start_time(p.pid))


class StaleArtifactsTest(_DaemonStateBase):
    def test_stale_socket_no_pid_file(self):
        self._socket_path.touch()
        self.assertFalse(picker_utils._daemon_alive())

    def test_half_killed_pid_gone_socket_left(self):
        self._write_pid(_find_unused_pid())
        self._socket_path.touch()
        self.assertFalse(picker_utils._daemon_alive())
        self.assertFalse(self._pid_path.exists())


class QueryDaemonTest(_DaemonStateBase):
    def test_returns_none_when_no_daemon_and_spawn_fails(self):
        with mock.patch.object(picker_utils, "_try_connect", return_value=None), \
             mock.patch.object(picker_utils, "_spawn_daemon"), \
             mock.patch.object(picker_utils, "_wait_for_daemon", return_value=False):
            self.assertIsNone(picker_utils.query_daemon("cat"))

    def test_returns_loading_when_daemon_alive_but_socket_unreachable(self):
        p = self._spawn_dummy()
        self._record_daemon(p.pid)
        self._status_path.write_text(json.dumps({"pct": 50}), encoding="utf-8")
        with mock.patch.object(picker_utils, "_try_connect", return_value=None), \
             mock.patch.object(picker_utils, "_spawn_daemon"), \
             mock.patch.object(picker_utils, "_wait_for_daemon", return_value=False):
            self.assertEqual(picker_utils.query_daemon("cat"), "loading")

    def test_kill_daemon_clears_socket_and_pid(self):
        p = self._spawn_dummy()
        self._record_daemon(p.pid)
        self._socket_path.touch()
        picker_utils._kill_daemon()
        self.assertFalse(self._pid_path.exists())
        self.assertFalse(self._socket_path.exists())
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.fail("_kill_daemon did not signal the process")

    def test_kill_daemon_with_dead_pid_is_noop(self):
        self._write_pid(_find_unused_pid())
        picker_utils._kill_daemon()
        self.assertFalse(self._pid_path.exists())

    def test_kill_daemon_refuses_to_signal_unrelated_process(self):
        p = self._spawn_dummy()
        real_start = picker_utils._proc_start_time(p.pid)
        self._record_daemon(p.pid, start_time=real_start + 1)
        self._socket_path.touch()
        picker_utils._kill_daemon()
        self.assertFalse(self._pid_path.exists())
        self.assertFalse(self._socket_path.exists())
        self.assertIsNone(p.poll(), "unrelated process must not be signalled")


class DaemonScriptImportTest(unittest.TestCase):
    """Entry-point scripts under ksapp/ are spawned by file path (see
    picker_utils.DAEMON_PY / STORY_PY). Invoked that way, the repo root is not
    on sys.path, so `from ksapp import ...` fails with ModuleNotFoundError
    unless the package happens to be pip-installed. The scripts must self-heal
    so they run regardless of how they are launched or whether `pip install -e`
    was ever run."""

    def _ksapp_import_error(self, script):
        env = dict(os.environ)
        env["PYTHONPATH"] = ""  # simulate a launch where ksapp isn't installed
        with TemporaryDirectory() as cwd:
            proc = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=cwd, env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                _, err = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                # Survived the import phase and reached the slow model load /
                # real work — that is the success case for this test.
                proc.terminate()
                try:
                    _, err = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    _, err = proc.communicate()
        return err or ""

    def test_split_daemon_importable_when_run_by_path(self):
        err = self._ksapp_import_error(picker_utils.DAEMON_PY)
        self.assertNotIn("No module named 'ksapp'", err)

    def test_story_importable_when_run_by_path(self):
        err = self._ksapp_import_error(picker_utils.STORY_PY)
        self.assertNotIn("No module named 'ksapp'", err)


class SpawnDaemonHygieneTest(_DaemonStateBase):
    """A fresh spawn must not let a previous daemon's leftovers make the loading
    screen lie. Otherwise a stale 'Ready 100%' status from an earlier run is
    shown while the new daemon is still starting (or has crashed)."""

    def setUp(self):
        super().setUp()
        self._log_path = Path(self._pid_path.parent) / "split-daemon.log"
        p = mock.patch.object(picker_utils, "DAEMON_LOG", self._log_path)
        p.start()
        self.addCleanup(p.stop)

    def test_spawn_clears_stale_loading_status(self):
        self._status_path.write_text(
            json.dumps({"step": "Ready", "pct": 100.0}), encoding="utf-8")

        script = self._log_path.parent / "dummy_daemon.py"
        script.write_text(
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))\n"
            "time.sleep(60)\n",
            encoding="utf-8")
        missing_bin = self._log_path.parent / "no-such-daemon-bin"

        with mock.patch.object(picker_utils, "DAEMON_BIN", missing_bin), \
             mock.patch.object(picker_utils, "DAEMON_PY", script), \
             mock.patch.object(picker_utils, "_PYTHON", sys.executable):
            proc = picker_utils._spawn_daemon()
        self._children.append(proc)

        self.assertFalse(
            self._status_path.exists(),
            "stale loading status must be cleared so the UI shows the new "
            "daemon's real progress, not a previous run's 'Ready'")


if __name__ == "__main__":
    unittest.main()
