"""Завершение процесса после цикла событий Qt (main/process_exit.py).

Причина падения «Ошибка приложения … память не может быть read» при выходе:
уборка PyQt при завершении интерпретатора удаляла объекты Qt, обработчики
программы на Python в ответ заводили новые, и PyQt читала освобождённую
память. Теперь объекты Qt при выходе не разрушаются, а полезные шаги
завершения выполняются явно — это здесь и доказывается.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication, QThread

SRC = Path(__file__).resolve().parents[1] / "src"


def _ensure_app() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


class _ImmediateExitScreen:
    """Прощальный экран в тестах выхода: «дальше» вызывается сразу."""

    def finish(self, on_done) -> None:
        on_done()


class _LoopThread(QThread):
    """Поток, который послушно выходит по requestInterruption()."""

    def run(self) -> None:
        while not self.isInterruptionRequested():
            self.msleep(5)


class _StuckThread(QThread):
    """Поток, который не реагирует на просьбы остановиться."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def run(self) -> None:
        self.release.wait()


_CHILD_PRELUDE = """
import atexit, sys, threading
from PyQt6.QtCore import QCoreApplication, QThread
from utils.exit_steps import register_exit_step

marker = sys.argv[1]
register_exit_step("метка", lambda: open(marker, "a").write("exit step\\n"))
# Обычный atexit при выходе программы не выполняется: там же стоит уборка PyQt.
atexit.register(lambda: open(marker, "a").write("atexit\\n"))
app = QCoreApplication([])
started = threading.Event()
"""

_CHILD_STUCK = _CHILD_PRELUDE + """
class Stuck(QThread):
    def run(self):
        started.set()
        threading.Event().wait()

thread = Stuck()
thread.start()
started.wait(5)
from main.process_exit import finish_process
finish_process(7, timeout_ms=200)
"""

_CHILD_COOPERATIVE = _CHILD_PRELUDE + """
class Loop(QThread):
    def run(self):
        started.set()
        while not self.isInterruptionRequested():
            self.msleep(5)
        open(marker, "a").write("thread finished\\n")

thread = Loop()
thread.start()
started.wait(5)
from main.process_exit import finish_process
finish_process(7)
"""


# Причина падения при выходе в чистом виде. При завершении интерпретатора
# PyQt идёт по таблице своих объектов и удаляет их; удаление окна вызывает
# обычный фильтр событий на Python, тот заводит новые объекты, таблица
# переезжает в другую память, а PyQt продолжает читать старую. Шестнадцать
# окон нужны, чтобы порядок обхода таблицы не спасал от падения случайно.
_CHILD_EVENT_FILTER = """
import sys
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication, QWidget

app = QApplication([])
keep = [QObject() for _ in range(40000)]
armed = False


class Filter(QObject):
    def eventFilter(self, watched, event):
        global armed
        if armed:
            armed = False
            keep.extend(QObject() for _ in range(200000))
        return False


windows = []
for _ in range(16):
    window = QWidget()
    child = QWidget(window)
    event_filter = Filter()
    child.installEventFilter(event_filter)
    windows.append((window, child, event_filter))
    window.show()
app.processEvents()
for window, _child, _filter in windows:
    window.close()
armed = True
from main.process_exit import finish_process
finish_process(7)
"""


def _run_child(script: str, marker: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["QT_QPA_PLATFORM"] = "offscreen"
    return subprocess.run(
        [sys.executable, "-c", script, str(marker)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class RunningThreadDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_app()

    def test_finds_running_thread_and_ignores_main_thread(self) -> None:
        from main.process_exit import running_qt_threads

        main_thread = QThread.currentThread()  # оболочка главного потока жива
        thread = _LoopThread()
        thread.start()
        try:
            found = running_qt_threads()
            self.assertIn(thread, found)
            self.assertNotIn(main_thread, found)
        finally:
            thread.requestInterruption()
            self.assertTrue(thread.wait(5000))
        self.assertNotIn(thread, running_qt_threads())

    def test_cooperative_thread_is_stopped_and_waited(self) -> None:
        from main.process_exit import stop_running_qt_threads

        thread = _LoopThread()
        thread.start()
        self.assertNotIn(thread, stop_running_qt_threads(5000))
        self.assertTrue(thread.isFinished())

    def test_stuck_thread_is_reported(self) -> None:
        from main.process_exit import stop_running_qt_threads

        thread = _StuckThread()
        thread.start()
        try:
            self.assertIn(thread, stop_running_qt_threads(100))
        finally:
            thread.release.set()
            self.assertTrue(thread.wait(5000))


class PythonThreadWaitTests(unittest.TestCase):
    def test_non_daemon_thread_is_waited_but_not_forever(self) -> None:
        from main.process_exit import wait_for_python_threads

        release = threading.Event()
        quick = threading.Thread(target=lambda: None, name="quick")
        stuck = threading.Thread(target=release.wait, name="stuck")
        background = threading.Thread(target=release.wait, name="background", daemon=True)
        for thread in (quick, stuck, background):
            thread.start()
        try:
            unfinished = wait_for_python_threads(100)
        finally:
            release.set()
            stuck.join(5)
            background.join(5)

        # Фоновый поток не ждут вовсе, зависший — только отведённое время.
        self.assertEqual([thread.name for thread in unfinished], ["stuck"])


class _HardExit(Exception):
    """Вместо настоящего os._exit в тестах порядка."""


class FinishProcessTests(unittest.TestCase):
    def _run_finish(self, *, unfinished, exit_code: int = 5):
        import main.process_exit as process_exit

        calls: list[str] = []

        def _hard_exit(code):
            calls.append(f"os._exit({code})")
            raise _HardExit

        def _record(name, result=None):
            def _step(*_args, **_kwargs):
                calls.append(name)
                return result

            return _step

        with (
            patch.object(process_exit, "stop_running_qt_threads", side_effect=_record("qt threads", unfinished)),
            patch.object(process_exit, "wait_for_python_threads", side_effect=_record("python threads", [])),
            patch.object(process_exit, "run_exit_steps", side_effect=_record("exit steps")),
            patch.object(process_exit.os, "_exit", side_effect=_hard_exit),
            patch.object(process_exit, "log") as log_mock,
        ):
            with self.assertRaises(_HardExit):
                process_exit.finish_process(exit_code)
        return calls, log_mock

    def test_threads_then_exit_steps_then_hard_exit(self) -> None:
        calls, log_mock = self._run_finish(unfinished=[])

        self.assertEqual(calls, ["qt threads", "python threads", "exit steps", "os._exit(5)"])
        log_mock.assert_not_called()

    def test_unfinished_threads_are_named_in_log_and_exit_still_completes(self) -> None:
        thread = Mock()
        thread.objectName.return_value = "probe"

        calls, log_mock = self._run_finish(unfinished=[thread])

        self.assertEqual(calls[-2:], ["exit steps", "os._exit(5)"])
        message = log_mock.call_args.args[0]
        self.assertIn("не остановились фоновые потоки", message)
        self.assertIn("«probe»", message)

    def test_process_with_stuck_thread_exits_cleanly_after_exit_steps(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.txt"
            result = _run_child(_CHILD_STUCK, marker)
            written = marker.read_text() if marker.exists() else ""

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(written, "exit step\n")

    def test_python_event_filter_does_not_crash_exit(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            result = _run_child(_CHILD_EVENT_FILTER, Path(tmp) / "unused.txt")

        # Со старым sys.exit() процесс здесь падал с нарушением доступа
        # (на Linux — код -11), а не выходил со своим кодом.
        self.assertEqual(result.returncode, 7, result.stderr)

    def test_thread_is_finished_before_interpreter_shutdown_starts(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.txt"
            result = _run_child(_CHILD_COOPERATIVE, marker)
            written = marker.read_text() if marker.exists() else ""

        self.assertEqual(result.returncode, 7, result.stderr)
        # Сначала поток закончил свой Python-код, и только потом пошли шаги
        # выхода. При обычном sys.exit() поток продолжал работать во время
        # завершения.
        self.assertEqual(written, "thread finished\nexit step\n")


class ExitOrderContractTests(unittest.TestCase):
    """Что обязано отработать до конца процесса и где это закреплено."""

    def test_entry_waits_for_threads_after_event_loop(self) -> None:
        source = (SRC / "main" / "entry.py").read_text(encoding="utf-8")
        self.assertNotIn("sys.exit(app.exec())", source)

        main_fn = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        tail = [ast.unparse(node) for node in main_fn.body[-3:]]
        self.assertEqual(tail[0], "exit_code = app.exec()")
        self.assertEqual(tail[2], "finish_process(exit_code)")

    def test_required_exit_work_is_registered_as_exit_steps(self) -> None:
        # finish_process выполняет именно шаги выхода, поэтому журналы, база
        # настроек и мьютекс должны быть записаны в этот список.
        expected = {
            "log/log.py": 'register_exit_step("закрытие журнала программы", global_logger.shutdown)',
            "log/run_log_sessions.py": 'register_exit_step("закрытие журналов запусков winws", run_log_sessions.close_all)',
            "log/crash_handler.py": 'register_exit_step("отметка конца сеанса в журнале падений", _mark_session_ended)',
            "settings/store.py": 'register_exit_step("закрытие базы настроек", close_settings_database)',
            "main/shell.py": "lambda: release_mutex(mutex_handle),",
        }
        for relative, registration in expected.items():
            with self.subTest(relative):
                self.assertIn(registration, (SRC / relative).read_text(encoding="utf-8"))

    def test_settings_and_dpi_stop_happen_before_event_loop_quits(self) -> None:
        import main.application_lifecycle as lifecycle_module

        calls: list[str] = []
        window_port = Mock()
        window_port.begin_exit_screen.return_value = _ImmediateExitScreen()
        window_port.persist_geometry.side_effect = lambda **_: calls.append("persist_geometry")
        window_port.persist_sidebar_state.side_effect = lambda **_: calls.append("persist_sidebar_state")
        runtime_feature = Mock()
        runtime_feature.stop_and_exit.return_value = False
        runtime_feature.shutdown_sync.side_effect = lambda **_: calls.append("stop_dpi")
        qapplication = Mock()
        qapplication.closeAllWindows.side_effect = lambda: calls.append("closeAllWindows")
        qapplication.quit.side_effect = lambda: calls.append("quit")

        lifecycle = lifecycle_module.ApplicationLifecycle(
            window_port=window_port,
            close_state=Mock(),
            runtime_feature=runtime_feature,
            premium_feature=Mock(),
            telegram_proxy_feature=Mock(),
            tray_feature=Mock(),
        )
        with patch.object(lifecycle_module, "QApplication", qapplication):
            lifecycle.exit_stop_dpi()

        self.assertEqual(
            calls,
            ["persist_geometry", "persist_sidebar_state", "stop_dpi", "closeAllWindows", "quit"],
        )

    def test_async_dpi_stop_returns_control_to_exit_owner(self) -> None:
        import main.application_lifecycle as lifecycle_module

        calls: list[str] = []
        handed_over: list = []
        runtime_feature = Mock()

        def _stop_and_exit(*, on_stopped):
            calls.append("stop started")
            handed_over.append(on_stopped)
            return True

        runtime_feature.stop_and_exit.side_effect = _stop_and_exit
        qapplication = Mock()
        qapplication.closeAllWindows.side_effect = lambda: calls.append("closeAllWindows")
        qapplication.quit.side_effect = lambda: calls.append("quit")

        window_port = Mock()
        window_port.begin_exit_screen.return_value = _ImmediateExitScreen()
        lifecycle = lifecycle_module.ApplicationLifecycle(
            window_port=window_port,
            close_state=Mock(),
            runtime_feature=runtime_feature,
            premium_feature=Mock(),
            telegram_proxy_feature=Mock(),
            tray_feature=Mock(),
        )
        with patch.object(lifecycle_module, "QApplication", qapplication):
            lifecycle.exit_stop_dpi()
            # Пока DPI останавливается в фоне, программа ещё не закрывается.
            self.assertEqual(calls, ["stop started"])
            runtime_feature.shutdown_sync.assert_not_called()

            handed_over[0]()

        self.assertEqual(calls, ["stop started", "closeAllWindows", "quit"])

    def test_runtime_reports_stop_to_exit_owner_instead_of_quitting(self) -> None:
        from winws_runtime.runtime import lifecycle_feedback

        after_stop = Mock()
        runtime_owner = Mock()
        runtime_owner._after_stop_and_exit = after_stop

        with patch.object(lifecycle_feedback, "set_runtime_owner_status"):
            lifecycle_feedback.on_stop_and_exit_finished(runtime_owner)
            lifecycle_feedback.on_stop_and_exit_finished(runtime_owner)

        after_stop.assert_called_once_with()
        self.assertIsNone(runtime_owner._after_stop_and_exit)

    def test_final_close_cleanup_persists_state_and_stops_process_monitor(self) -> None:
        import main.application_lifecycle as lifecycle_module

        window_port = Mock()
        runtime_feature = Mock()
        lifecycle = lifecycle_module.ApplicationLifecycle(
            window_port=window_port,
            close_state=Mock(),
            runtime_feature=runtime_feature,
            premium_feature=Mock(),
            telegram_proxy_feature=Mock(),
            tray_feature=Mock(),
        )
        lifecycle.run_final_close_cleanup()

        window_port.persist_geometry.assert_called_once()
        window_port.persist_sidebar_state.assert_called_once()
        runtime_feature.cleanup_process_monitor.assert_called_once()


class CrashLogSessionEndTests(unittest.TestCase):
    def test_session_end_mark_keeps_crash_log_open(self) -> None:
        import tempfile

        from log import crash_handler

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "faulthandler.log"
            handle = open(path, "a", encoding="utf-8")
            try:
                with patch.object(crash_handler, "_faulthandler_file", handle):
                    crash_handler._mark_session_ended()
                # faulthandler пишет в номер файла: закрытый файл делал бы его
                # слепым на всё время завершения интерпретатора.
                self.assertFalse(handle.closed)
                self.assertIn("Session ended:", path.read_text(encoding="utf-8"))
            finally:
                handle.close()


if __name__ == "__main__":
    unittest.main()
