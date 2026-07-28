"""Снимки стеков для разбора зависаний: формат и сторожевой таймер без GIL."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from log.thread_dump import (
    DISABLE_ENV_VAR,
    FaulthandlerHangTimer,
    format_thread_dump,
    is_thread_dump_disabled,
    save_thread_dump,
)


def _current_frame():
    import inspect

    return inspect.currentframe()


class ThreadDumpFormatTests(unittest.TestCase):
    def test_lists_threads_and_marks_gui_thread(self) -> None:
        gui_ident = threading.get_ident()

        report = format_thread_dump(stalled_seconds=7.5, gui_thread_ident=gui_ident)

        self.assertIn("UI FREEZE REPORT", report)
        self.assertIn("7.5с", report)
        self.assertIn(f"Thread {gui_ident}", report)
        self.assertIn("[GUI]", report)
        # Стек текущего потока обязан быть в отчёте: иначе дамп бесполезен.
        self.assertIn("test_lists_threads_and_marks_gui_thread", report)

    def test_includes_background_thread_stack(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def _worker() -> None:
            started.set()
            release.wait(5.0)

        worker = threading.Thread(target=_worker, name="DumpProbeThread", daemon=True)
        worker.start()
        self.addCleanup(worker.join, 5.0)
        self.addCleanup(release.set)
        started.wait(5.0)

        report = format_thread_dump(stalled_seconds=9.0, gui_thread_ident=None)

        # Виновник блокировки интерфейса обычно в чужом потоке — он и нужен.
        self.assertIn("DumpProbeThread", report)
        self.assertIn("_worker", report)

    def test_survives_thread_without_name_entry(self) -> None:
        report = format_thread_dump(
            stalled_seconds=6.0,
            gui_thread_ident=None,
            frames={999_999: _current_frame()},
            threads=[],
        )

        self.assertIn("Thread 999999: <unknown>", report)
        self.assertIn("GUI thread id: неизвестен", report)


class SaveThreadDumpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.folder = Path(self._tempdir.name)

    def test_writes_report_into_folder(self) -> None:
        path = save_thread_dump("отчёт", folder=self.folder / "nested")

        self.assertTrue(path)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "отчёт")
        self.assertTrue(Path(path).name.startswith("freeze_"))

    def test_unwritable_target_returns_empty_string(self) -> None:
        blocked = self.folder / "not-a-dir"
        blocked.write_text("busy", encoding="utf-8")

        # Диагностика не имеет права падать: неудачная запись — просто пустой путь.
        self.assertEqual(save_thread_dump("отчёт", folder=blocked), "")


class DisableFlagTests(unittest.TestCase):
    def test_flag_reading(self) -> None:
        self.assertTrue(is_thread_dump_disabled({DISABLE_ENV_VAR: "1"}))
        self.assertFalse(is_thread_dump_disabled({DISABLE_ENV_VAR: "0"}))
        self.assertFalse(is_thread_dump_disabled({}))

    def test_reads_process_environment_by_default(self) -> None:
        previous = os.environ.get(DISABLE_ENV_VAR)
        os.environ[DISABLE_ENV_VAR] = "1"
        try:
            self.assertTrue(is_thread_dump_disabled())
        finally:
            if previous is None:
                os.environ.pop(DISABLE_ENV_VAR, None)
            else:
                os.environ[DISABLE_ENV_VAR] = previous


class FaulthandlerHangTimerTests(unittest.TestCase):
    """Уровень, который обязан пережить GIL-дедлок, проверяем на живом таймере."""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.path = Path(self._tempdir.name) / "nested" / "hangs_faulthandler.log"

    def test_fires_and_writes_thread_stacks(self) -> None:
        timer = FaulthandlerHangTimer(self.path)
        self.addCleanup(timer.close)

        timer.arm(0.2)
        time.sleep(0.6)
        timer.disarm()

        text = self.path.read_text(encoding="utf-8")
        self.assertIn("Timeout", text)
        self.assertIn("Thread", text)
        self.assertIn("test_fires_and_writes_thread_stacks", text)

    def test_rearming_postpones_dump(self) -> None:
        timer = FaulthandlerHangTimer(self.path)
        self.addCleanup(timer.close)

        # Живой GUI-поток перевзводит таймер каждым биением, поэтому дампа быть
        # не должно, хотя суммарно прошло больше таймаута.
        for _ in range(4):
            timer.arm(0.5)
            time.sleep(0.15)
        timer.disarm()

        self.assertNotIn("Timeout", self.path.read_text(encoding="utf-8"))

    def test_disarm_before_timeout_prevents_dump(self) -> None:
        timer = FaulthandlerHangTimer(self.path)
        self.addCleanup(timer.close)

        timer.arm(0.4)
        timer.disarm()
        time.sleep(0.6)

        self.assertNotIn("Timeout", self.path.read_text(encoding="utf-8"))

    def test_close_is_idempotent(self) -> None:
        timer = FaulthandlerHangTimer(self.path)

        timer.arm(5.0)
        timer.close()
        timer.close()

        self.assertTrue(self.path.exists())


if __name__ == "__main__":
    unittest.main()
