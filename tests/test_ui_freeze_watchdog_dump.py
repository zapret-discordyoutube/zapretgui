"""Наблюдатель заморозок: файлы со стеками и сторожевой таймер без GIL.

Отдельный файл от `test_ui_freeze_watchdog.py`: там проверяется измерение
задержки event loop, здесь — что по факту заморозки остаются улики.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class _FakeNativeTimer:
    def __init__(self) -> None:
        self.arm_calls: list[float] = []
        self.closed = False

    def arm(self, timeout_seconds: float) -> None:
        self.arm_calls.append(float(timeout_seconds))

    def disarm(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _BrokenNativeTimer(_FakeNativeTimer):
    def arm(self, timeout_seconds: float) -> None:
        raise OSError("нет файлового дескриптора")


class UiFreezeWatchdogDumpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.dump_dir = Path(self._tempdir.name)

    def _build(self, **kwargs):
        from PyQt6.QtWidgets import QApplication

        from ui.ui_freeze_watchdog import UiFreezeWatchdog

        QApplication.instance() or QApplication([])
        records: list[tuple[str, str]] = []
        params = {
            "freeze_threshold_seconds": 0.2,
            "repeat_seconds": 1.0,
            "thread_dump_min_seconds": 0.3,
            "hard_freeze_dump_seconds": 9.0,
            "log_fn": lambda message, level: records.append((message, level)),
            "stack_fn": lambda: "  File \"page.py\", line 1, in run\n    blocking_call()",
            "dump_dir": self.dump_dir,
            "native_timer": _FakeNativeTimer(),
        }
        params.update(kwargs)
        watchdog = UiFreezeWatchdog(**params)
        self.addCleanup(watchdog.stop)
        return watchdog, records

    def _dumps(self) -> list[Path]:
        return sorted(self.dump_dir.glob("freeze_*.log"))

    def test_long_freeze_writes_full_thread_dump(self) -> None:
        watchdog, records = self._build()
        watchdog.beat()

        watchdog.check_once(now=_monotonic() + 1.0)

        dumps = self._dumps()
        self.assertEqual(len(dumps), 1)
        text = dumps[0].read_text(encoding="utf-8")
        self.assertIn("UI FREEZE REPORT", text)
        self.assertIn("Thread", text)
        # Путь к отчёту обязан быть в самом логе, иначе его никто не найдёт.
        self.assertIn(str(dumps[0]), records[0][0])

    def test_short_freeze_does_not_write_dump(self) -> None:
        watchdog, records = self._build(thread_dump_min_seconds=5.0)
        watchdog.beat()

        watchdog.check_once(now=_monotonic() + 1.0)

        self.assertEqual(self._dumps(), [])
        self.assertEqual(len(records), 1)
        self.assertNotIn("Стеки всех потоков", records[0][0])

    def test_dump_can_be_disabled_by_env(self) -> None:
        from log.thread_dump import DISABLE_ENV_VAR

        watchdog, _records = self._build()
        watchdog.beat()

        previous = os.environ.get(DISABLE_ENV_VAR)
        os.environ[DISABLE_ENV_VAR] = "1"
        try:
            watchdog.check_once(now=_monotonic() + 1.0)
        finally:
            if previous is None:
                os.environ.pop(DISABLE_ENV_VAR, None)
            else:
                os.environ[DISABLE_ENV_VAR] = previous

        self.assertEqual(self._dumps(), [])

    def test_beat_rearms_native_timer(self) -> None:
        native = _FakeNativeTimer()
        watchdog, _records = self._build(native_timer=native, hard_freeze_dump_seconds=9.0)

        watchdog.beat()
        watchdog.beat()

        # Пока event loop жив, таймер перевзводится и до дампа дело не доходит.
        self.assertEqual(native.arm_calls, [9.0, 9.0])

    def test_broken_native_timer_does_not_break_heartbeat(self) -> None:
        watchdog, records = self._build(native_timer=_BrokenNativeTimer())

        watchdog.beat()
        watchdog.beat()

        # Сломанный нижний уровень не должен мешать верхним.
        watchdog.check_once(now=_monotonic() + 1.0)
        self.assertTrue(records)

    def test_stop_does_not_close_injected_native_timer(self) -> None:
        native = _FakeNativeTimer()
        watchdog, _records = self._build(native_timer=native)
        watchdog.beat()

        watchdog.stop()

        # Владелец переданного таймера закрывает его сам.
        self.assertFalse(native.closed)

    def test_owned_native_timer_is_created_and_closed(self) -> None:
        watchdog, _records = self._build(native_timer=None)

        watchdog.beat()
        created = sorted(self.dump_dir.glob("hangs_faulthandler.log"))
        self.assertEqual(len(created), 1)

        watchdog.stop()
        # Взведённый таймер после stop() дампил бы уже завершающийся процесс.
        self.assertIsNone(watchdog._native_timer)


def _monotonic() -> float:
    import time

    return time.monotonic()


if __name__ == "__main__":
    unittest.main()
