"""Single-instance: the Windows backend (mutex + event) and how the shell start uses it."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

import platforms.windows.single_instance as win_single
from platforms.base import InstanceLock, Platform
from platforms.windows.single_instance import WindowsSingleInstance


class MutexTests(unittest.TestCase):
    def test_second_copy_sees_already_exists(self) -> None:
        kernel32 = Mock()
        kernel32.CreateMutexW.return_value = 7
        kernel32.GetLastError.return_value = win_single.ERROR_ALREADY_EXISTS

        with patch.object(win_single, "_kernel32", return_value=kernel32):
            lock = WindowsSingleInstance().acquire("Name")

        self.assertTrue(lock.already_running)
        kernel32.CreateMutexW.assert_called_once_with(None, False, "Name")

    def test_first_copy_is_not_reported_as_running_and_release_closes_the_handle(self) -> None:
        kernel32 = Mock()
        kernel32.CreateMutexW.return_value = 7
        kernel32.GetLastError.return_value = 0

        with patch.object(win_single, "_kernel32", return_value=kernel32):
            lock = WindowsSingleInstance().acquire("Name")
            lock.release()

        self.assertFalse(lock.already_running)
        kernel32.ReleaseMutex.assert_called_once_with(7)
        kernel32.CloseHandle.assert_called_once_with(7)

    def test_refused_mutex_means_not_running(self) -> None:
        kernel32 = Mock()
        kernel32.CreateMutexW.return_value = 0
        kernel32.GetLastError.return_value = win_single.ERROR_ALREADY_EXISTS

        with patch.object(win_single, "_kernel32", return_value=kernel32):
            lock = WindowsSingleInstance().acquire("Name")
            lock.release()  # nothing to release, must not raise

        self.assertFalse(lock.already_running)
        kernel32.ReleaseMutex.assert_not_called()


class ShowEventTests(unittest.TestCase):
    def test_create_show_signal_stores_handle_and_is_idempotent(self) -> None:
        kernel32 = Mock()
        kernel32.CreateEventW.return_value = 42
        instance = WindowsSingleInstance()

        with patch.object(win_single, "_kernel32", return_value=kernel32):
            self.assertTrue(instance.create_show_signal())
            self.assertTrue(instance.create_show_signal())

        kernel32.CreateEventW.assert_called_once_with(None, False, False, win_single.SHOW_EVENT_NAME)
        self.assertEqual(instance._show_event_handle, 42)

    def test_create_show_signal_returns_false_on_failure(self) -> None:
        kernel32 = Mock()
        kernel32.CreateEventW.return_value = 0
        instance = WindowsSingleInstance()

        with patch.object(win_single, "_kernel32", return_value=kernel32):
            self.assertFalse(instance.create_show_signal())

        self.assertIsNone(instance._show_event_handle)

    def test_signal_returns_false_when_event_missing(self) -> None:
        kernel32 = Mock()
        kernel32.OpenEventW.return_value = 0

        with patch.object(win_single, "_kernel32", return_value=kernel32):
            self.assertFalse(WindowsSingleInstance().signal_running_instance())

        kernel32.SetEvent.assert_not_called()

    def test_signal_sets_and_closes_handle(self) -> None:
        kernel32 = Mock()
        kernel32.OpenEventW.return_value = 5
        kernel32.SetEvent.return_value = 1

        with patch.object(win_single, "_kernel32", return_value=kernel32):
            self.assertTrue(WindowsSingleInstance().signal_running_instance())

        kernel32.OpenEventW.assert_called_once_with(
            win_single.EVENT_MODIFY_STATE, False, win_single.SHOW_EVENT_NAME
        )
        kernel32.SetEvent.assert_called_once_with(5)
        kernel32.CloseHandle.assert_called_once_with(5)

    def test_signal_closes_handle_even_when_set_fails(self) -> None:
        kernel32 = Mock()
        kernel32.OpenEventW.return_value = 5
        kernel32.SetEvent.return_value = 0

        with patch.object(win_single, "_kernel32", return_value=kernel32):
            self.assertFalse(WindowsSingleInstance().signal_running_instance())

        kernel32.CloseHandle.assert_called_once_with(5)


class ShowEventWatcherTests(unittest.TestCase):
    def test_watcher_invokes_callback_per_signal_and_stops_on_failure(self) -> None:
        results = iter([win_single.WAIT_OBJECT_0, win_single.WAIT_OBJECT_0, 0xFFFFFFFF])  # last: WAIT_FAILED
        received: list[str] = []

        thread = WindowsSingleInstance().start_show_watcher(
            lambda: received.append("show"),
            wait_fn=lambda: next(results),
        )
        self.assertIsNotNone(thread)
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(received, ["show", "show"])

    def test_watcher_stops_on_abandoned_wait(self) -> None:
        WAIT_ABANDONED = 0x80
        thread = WindowsSingleInstance().start_show_watcher(
            lambda: self.fail("callback must not be called"),
            wait_fn=lambda: WAIT_ABANDONED,
        )
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def test_watcher_requires_created_event(self) -> None:
        self.assertIsNone(WindowsSingleInstance().start_show_watcher(lambda: None))


def _fake_platform(*, already_running: bool, signal_delivered: bool = True):
    """A platform whose single-instance answers are scripted; returns it with its parts."""
    messages = MagicMock()
    privileges = MagicMock(gui_needs_elevation=True)
    privileges.is_elevated.return_value = True
    single = MagicMock()
    release = Mock()
    single.acquire.return_value = InstanceLock(already_running=already_running, _release=release)
    single.signal_running_instance.return_value = signal_delivered
    platform = Platform(name="fake", messages=messages, privileges=privileges, single_instance=single)
    return platform, messages, single, release


class ShellSecondInstanceTests(unittest.TestCase):
    """The already-running branch of shell_bootstrap: a message box appears only for a
    manual start whose signal was not delivered."""

    def _run_bootstrap(self, argv: list[str], *, signal_delivered: bool):
        from main import shell

        platform, messages, _single, _release = _fake_platform(
            already_running=True, signal_delivered=signal_delivered
        )
        with patch.object(shell, "current_platform", return_value=platform):
            with self.assertRaises(SystemExit) as ctx:
                shell.shell_bootstrap(argv)

        self.assertEqual(ctx.exception.code, 0)
        return messages.show

    def test_no_messagebox_when_signal_delivered(self) -> None:
        self._run_bootstrap(["zapret.exe"], signal_delivered=True).assert_not_called()

    def test_no_messagebox_for_tray_launch_even_without_signal(self) -> None:
        self._run_bootstrap(["zapret.exe", "--tray"], signal_delivered=False).assert_not_called()

    def test_messagebox_for_manual_launch_without_signal(self) -> None:
        self._run_bootstrap(["zapret.exe"], signal_delivered=False).assert_called_once()

    def test_first_instance_creates_show_signal_right_after_the_lock(self) -> None:
        from main import shell

        platform, _messages, single, release = _fake_platform(already_running=False)
        calls: list[str] = []
        single.acquire.side_effect = lambda name: calls.append("lock") or InstanceLock(
            already_running=False, _release=release
        )
        single.create_show_signal.side_effect = lambda: calls.append("signal") or True

        with (
            patch.object(shell, "current_platform", return_value=platform),
            patch.object(shell, "register_exit_step") as register_exit_step,
        ):
            start_in_tray = shell.shell_bootstrap(["zapret.exe", "--tray"])
            # The lock is freed by an exit step: otherwise after os._exit at the end
            # the program would still count as running.
            register_exit_step.assert_called_once()
            register_exit_step.call_args.args[1]()

        release.assert_called_once_with()
        self.assertTrue(start_in_tray)
        self.assertEqual(calls, ["lock", "signal"])


if __name__ == "__main__":
    unittest.main()
