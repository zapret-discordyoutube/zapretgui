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

marker = sys.argv[1]
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


_CHILD_PYQT_CLEANUP = """
import sys
from PyQt6.QtCore import QCoreApplication

app = QCoreApplication([])
from main.process_exit import unregister_pyqt_exit_cleanup
open(sys.argv[1], "a").write(f"first={unregister_pyqt_exit_cleanup()} second={unregister_pyqt_exit_cleanup()}\\n")
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
            patch.object(process_exit.threading, "_shutdown", side_effect=_record("python threads")),
            patch("settings.store.close_settings_database", side_effect=_record("settings database")),
            patch.object(process_exit, "unregister_pyqt_exit_cleanup", side_effect=_record("pyqt cleanup off", True)),
            patch.object(process_exit.atexit, "_run_exitfuncs", side_effect=_record("atexit")),
            patch.object(process_exit.os, "_exit", side_effect=_hard_exit),
            patch.object(process_exit, "log") as log_mock,
        ):
            with self.assertRaises(_HardExit):
                process_exit.finish_process(exit_code)
        return calls, log_mock

    def test_exit_steps_run_in_order_before_hard_exit(self) -> None:
        calls, log_mock = self._run_finish(unfinished=[])

        # Журналы и мьютекс освобождают обработчики atexit, поэтому они идут
        # последними перед os._exit, а уборка PyQt к этому моменту уже снята.
        self.assertEqual(
            calls,
            [
                "qt threads",
                "python threads",
                "settings database",
                "pyqt cleanup off",
                "atexit",
                "os._exit(5)",
            ],
        )
        log_mock.assert_not_called()

    def test_unfinished_threads_are_named_in_log_and_exit_still_completes(self) -> None:
        thread = Mock()
        thread.objectName.return_value = "probe"

        calls, log_mock = self._run_finish(unfinished=[thread])

        self.assertEqual(calls[-2:], ["atexit", "os._exit(5)"])
        message = log_mock.call_args.args[0]
        self.assertIn("не остановились фоновые потоки", message)
        self.assertIn("«probe»", message)

    def test_pyqt_exit_cleanup_is_found_and_unregistered(self) -> None:
        # В отдельном процессе: снятие уборки действует на весь процесс.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.txt"
            result = _run_child(_CHILD_PYQT_CLEANUP, marker)
            written = marker.read_text() if marker.exists() else ""

        self.assertEqual(result.returncode, 0, result.stderr)
        # Второй поиск ничего не находит: обработчик действительно снят.
        self.assertEqual(written, "first=True second=False\n")

    def test_process_with_stuck_thread_exits_cleanly_after_exit_handlers(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.txt"
            result = _run_child(_CHILD_STUCK, marker)
            written = marker.read_text() if marker.exists() else ""

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(written, "atexit\n")

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
        # Порядок и есть суть исправления: сначала поток закончил свой
        # Python-код, и только потом началось завершение интерпретатора
        # (atexit — его первый шаг). При обычном sys.exit() поток продолжал
        # работать во время завершения.
        self.assertEqual(written, "thread finished\natexit\n")


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

    def test_logs_and_single_instance_mutex_are_released_by_exit_handlers(self) -> None:
        # exit_without_interpreter_teardown запускает именно atexit-обработчики,
        # поэтому закрытие журналов и мьютекс должны быть зарегистрированы там.
        expected = {
            "log/log.py": "atexit.register(global_logger.shutdown)",
            "log/run_log_sessions.py": "atexit.register(run_log_sessions.close_all)",
            "log/crash_handler.py": "atexit.register(_mark_session_ended)",
            "main/shell.py": "atexit.register(lambda: release_mutex(mutex_handle))",
        }
        for relative, registration in expected.items():
            with self.subTest(relative):
                self.assertIn(registration, (SRC / relative).read_text(encoding="utf-8"))

    def test_settings_and_dpi_stop_happen_before_event_loop_quits(self) -> None:
        import main.application_lifecycle as lifecycle_module

        calls: list[str] = []
        window_port = Mock()
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
