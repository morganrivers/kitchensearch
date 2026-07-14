"""Windows-only tests for the mutex-based daemon liveness mechanism.

The daemon acquires a per-machine named mutex (``Global\\KitchenSearchSplitDaemon``)
at startup; the picker checks that mutex to know if the daemon is alive. The
mutex is a kernel object — Windows auto-releases it on process death — so
these tests do NOT need to clean up any file, unlike the POSIX pid-file path.

Each test uses a UNIQUE test-scoped mutex name (via mock.patch of
``_WIN_SPLIT_DAEMON_MUTEX``) so it doesn't collide with a real running
Kitchen Search install on the developer's machine. That makes the tests
reliable on machines that happen to have the app running.

What each test pins down:
  * ``_win_split_daemon_running()`` reflects the OS mutex state accurately.
  * The mutex is auto-released when its owning process dies (no stale state
    survives, unlike the pid file that motivated this refactor).
  * ``_daemon_alive()`` uses the mutex on Windows instead of the pid file.
  * The daemon enforces its own singleton — a second daemon exits cleanly.
  * ``_spawn_daemon()`` waits briefly for the mutex to appear so back-to-back
    ``_daemon_alive()`` checks after spawn don't briefly return False and
    trigger a double-spawn.
  * ``_spawn_daemon()`` does not write the pid file on Windows.
"""
import os
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ksapp import picker_utils  # noqa: E402


def _unique_test_mutex_name():
    """Per-test mutex name that can't collide with a running production
    daemon or another concurrent test. Uses the Local\\ namespace so it
    doesn't require SeCreateGlobalPrivilege for the child processes.
    """
    return r"Local\KitchenSearchTest-" + uuid.uuid4().hex


def _mutex_holder_script(name):
    """A tiny standalone script that acquires the given mutex, prints
    'HELD' when it has it (or 'BUSY' if another holder beat it), and then
    blocks. The parent test kills it when done."""
    return (
        "import ctypes, sys, time\n"
        f'name = r"{name}"\n'
        "k32 = ctypes.windll.kernel32\n"
        "h = k32.CreateMutexW(None, True, name)\n"
        "ERROR_ALREADY_EXISTS = 183\n"
        "if k32.GetLastError() == ERROR_ALREADY_EXISTS:\n"
        "    print('BUSY', flush=True); sys.exit(0)\n"
        "print('HELD', flush=True)\n"
        "time.sleep(60)\n"
    )


class _MutexTestBase(unittest.TestCase):
    """Common setup: unique mutex name, child-process bookkeeping."""

    def setUp(self):
        self._mutex_name = _unique_test_mutex_name()
        p = mock.patch.object(picker_utils, "_WIN_SPLIT_DAEMON_MUTEX",
                              self._mutex_name)
        p.start()
        self.addCleanup(p.stop)

        self._children = []
        self.addCleanup(self._cleanup_children)

    def _cleanup_children(self):
        for p in self._children:
            if p.poll() is None:
                try:
                    p.terminate()
                    p.wait(timeout=5)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass

    def _spawn_holder(self):
        p = subprocess.Popen(
            [sys.executable, "-c", _mutex_holder_script(self._mutex_name)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self._children.append(p)
        line = p.stdout.readline().decode().strip()
        p.stdout.close()
        return p, line


@unittest.skipUnless(sys.platform == "win32", "Windows-only mutex mechanism")
class WinMutexHelperTest(_MutexTestBase):
    """Direct tests of _win_split_daemon_running() against the OS state."""

    def test_returns_false_when_no_holder(self):
        self.assertFalse(picker_utils._win_split_daemon_running())

    def test_returns_true_while_subprocess_holds_mutex(self):
        p, line = self._spawn_holder()
        self.assertEqual(line, "HELD",
                         "helper failed to acquire the mutex")
        self.assertTrue(picker_utils._win_split_daemon_running(),
                        "picker didn't see the mutex that our subprocess holds")

    def test_mutex_auto_releases_on_process_death(self):
        """This is the core bug-prevention: pid files leaked on death and
        caused PermissionError on the next cleanup attempt. Mutexes cannot
        leak — the kernel enforces release."""
        p, _ = self._spawn_holder()
        self.assertTrue(picker_utils._win_split_daemon_running())
        p.terminate()
        p.wait(timeout=5)
        # Windows releases kernel objects promptly on process exit. Allow
        # a brief window for the kernel to finalise the handle table.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not picker_utils._win_split_daemon_running():
                return
            time.sleep(0.05)
        self.fail("mutex still reported held 2 s after holder died")


@unittest.skipUnless(sys.platform == "win32", "Windows-only mutex mechanism")
class WinDaemonAliveTest(_MutexTestBase):
    """_daemon_alive() must use the mutex on Windows (never the pid file)."""

    def setUp(self):
        super().setUp()
        # Force the Windows branch even if IS_NAMED_PIPE were somehow False.
        p = mock.patch.object(picker_utils, "IS_NAMED_PIPE", True)
        p.start()
        self.addCleanup(p.stop)

    def test_alive_returns_false_without_mutex(self):
        with mock.patch.object(picker_utils, "_try_connect", return_value=None), \
             mock.patch.object(picker_utils, "_daemon_status_loaded",
                               return_value=False):
            self.assertFalse(picker_utils._daemon_alive())

    def test_alive_returns_true_when_mutex_held_and_pipe_serving(self):
        self._spawn_holder()
        # Simulate the pipe being served (post-load) so the zombie check
        # doesn't kick in.
        with mock.patch.object(picker_utils, "_try_connect",
                               return_value=object()), \
             mock.patch.object(picker_utils, "_daemon_status_loaded",
                               return_value=True):
            self.assertTrue(picker_utils._daemon_alive())

    def test_alive_returns_true_during_loading(self):
        """Mutex held but pipe not yet accepting (status pct < 100) → still alive."""
        self._spawn_holder()
        with mock.patch.object(picker_utils, "_try_connect", return_value=None), \
             mock.patch.object(picker_utils, "_daemon_status_loaded",
                               return_value=False):
            self.assertTrue(picker_utils._daemon_alive())

    def test_alive_never_touches_pid_file_on_windows(self):
        """Regression: the original bug was PermissionError unlinking the pid
        file on the Windows path of _daemon_alive. The mutex-based branch
        must not read or write it at all."""
        self._spawn_holder()
        # Point DAEMON_PID at a path that would blow up if touched.
        blow_up = Path(r"C:\pretend\this\does\not\exist\pid")
        with mock.patch.object(picker_utils, "DAEMON_PID", blow_up), \
             mock.patch.object(picker_utils, "_try_connect",
                               return_value=object()), \
             mock.patch.object(picker_utils, "_daemon_status_loaded",
                               return_value=True):
            # Must not raise, must not create the fake path.
            self.assertTrue(picker_utils._daemon_alive())
        self.assertFalse(blow_up.parent.exists())

    def test_alive_zombie_kill_triggers_when_status_loaded_but_pipe_gone(self):
        """Mutex held + status says pct=100 + pipe unreachable = zombie.
        _daemon_alive should attempt to kill and return False. We mock
        _kill_daemon so we don't actually taskkill anything."""
        self._spawn_holder()
        with mock.patch.object(picker_utils, "_try_connect", return_value=None), \
             mock.patch.object(picker_utils, "_daemon_status_loaded",
                               return_value=True), \
             mock.patch.object(picker_utils, "_kill_daemon") as kill:
            self.assertFalse(picker_utils._daemon_alive())
            kill.assert_called_once()


@unittest.skipUnless(sys.platform == "win32", "Windows-only mutex mechanism")
class WinDaemonSingletonTest(_MutexTestBase):
    """A second daemon must detect the first via mutex and exit cleanly.

    This test uses the REAL daemon script but hijacks its mutex name via
    KITCHENSEARCH_TEST_MUTEX_OVERRIDE, so it doesn't clash with a running
    production install.
    """

    DAEMON_SRC = _REPO_ROOT / "ksapp" / "emoji_split_daemon.py"

    def _spawn_daemon_with_mutex_override(self, cache_dir):
        # Wrap the daemon so it uses our test mutex name. We can't pass the
        # name as an argv (the daemon doesn't accept one); instead we run a
        # small bootstrap that monkeypatches ksapp.emoji_split_daemon's
        # module-level mutex constant before calling main().
        bootstrap = (
            "import ksapp.emoji_split_daemon as d\n"
            f'd._WIN_SINGLETON_MUTEX = r"{self._mutex_name}"\n'
            "d.main()\n"
        )
        env = {**os.environ,
               "PYTHONPATH": str(_REPO_ROOT),
               "KITCHENSEARCH_CACHE_DIR": str(cache_dir)}
        return subprocess.Popen(
            [sys.executable, "-c", bootstrap],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=(subprocess.CREATE_NO_WINDOW
                           | subprocess.CREATE_NEW_PROCESS_GROUP),
        )

    def test_second_daemon_exits_when_mutex_already_held(self):
        # First "daemon" is a lightweight mutex holder (avoids waiting on
        # the ~5 s model load just to prove the singleton check).
        holder, line = self._spawn_holder()
        self.assertEqual(line, "HELD")
        self.assertTrue(picker_utils._win_split_daemon_running())

        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            proc = self._spawn_daemon_with_mutex_override(Path(tmp))
            self._children.append(proc)
            try:
                # Must exit within a couple of seconds — well before the
                # ~5 s model-load window that would indicate the singleton
                # check failed and slow init started.
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                self.fail("second daemon did NOT exit — singleton check "
                          "failed; it started full initialisation instead")
            self.assertEqual(proc.returncode, 0,
                             f"second daemon exited non-zero: {proc.returncode}")
            output = proc.stdout.read().decode(errors="replace")
            proc.stdout.close()
            self.assertIn("Daemon already running", output,
                          f"expected singleton message, got: {output!r}")


@unittest.skipUnless(sys.platform == "win32", "Windows-only mutex mechanism")
class WinSpawnDaemonTest(_MutexTestBase):
    """_spawn_daemon on Windows must (a) not write DAEMON_PID, (b) wait for
    the mutex to appear so a follow-up _daemon_alive() call doesn't briefly
    return False and trigger a double-spawn."""

    def setUp(self):
        super().setUp()
        tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self._cache = Path(tmp.name)
        self._pid_path = self._cache / "split-daemon.pid"

        for p in (
            mock.patch.object(picker_utils, "IS_NAMED_PIPE", True),
            mock.patch.object(picker_utils, "DAEMON_PID", self._pid_path),
            mock.patch.object(picker_utils, "CACHE_DIR", self._cache),
            mock.patch.object(picker_utils, "DAEMON_LOG",
                              self._cache / "split-daemon.log"),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_does_not_write_pid_file_on_windows(self):
        with mock.patch.object(picker_utils.subprocess, "Popen") as popen, \
             mock.patch.object(picker_utils, "_win_split_daemon_running",
                               return_value=True):
            popen.return_value = mock.Mock(pid=99999)
            picker_utils._spawn_daemon()
        self.assertFalse(self._pid_path.exists(),
                         "Windows spawn path must not write DAEMON_PID; the "
                         "mutex is the source of truth")

    def test_waits_briefly_for_mutex_after_popen(self):
        # Simulate mutex appearing on the 3rd poll (~150 ms in).
        call_count = {"n": 0}
        def fake_running():
            call_count["n"] += 1
            return call_count["n"] >= 3

        with mock.patch.object(picker_utils.subprocess, "Popen") as popen, \
             mock.patch.object(picker_utils, "_win_split_daemon_running",
                               side_effect=fake_running):
            popen.return_value = mock.Mock(pid=99999)
            picker_utils._spawn_daemon()
        self.assertGreaterEqual(call_count["n"], 3,
                                "spawn returned before mutex became visible")


if __name__ == "__main__":
    unittest.main()
