from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from log.hang_watchdog import (
    DISABLE_ENV_VAR,
    FaulthandlerHangTimer,
    GuiHangWatchdog,
    HangDetector,
    format_thread_dump,
    install_gui_hang_watchdog,
    is_hang_watchdog_disabled,
)


class _FakeClock:
    """Управляемое время: тесты не должны ждать реальных секунд."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += float(seconds)
        return self.now


class _FakeNativeTimer:
    def __init__(self) -> None:
        self.arm_calls: list[float] = []
        self.disarm_calls = 0
        self.closed = False

    def arm(self, timeout_seconds: float) -> None:
        self.arm_calls.append(float(timeout_seconds))

    def disarm(self) -> None:
        self.disarm_calls += 1

    def close(self) -> None:
        self.closed = True
        self.disarm()


class HangDetectorTests(unittest.TestCase):
    def test_stays_quiet_until_first_beat(self) -> None:
        detector = HangDetector(threshold_seconds=5.0)

        decision = detector.evaluate(10_000.0)

        self.assertFalse(decision.should_dump)
        self.assertFalse(decision.recovered)
        self.assertEqual(detector.episode, 0)

    def test_no_dump_below_threshold(self) -> None:
        detector = HangDetector(threshold_seconds=5.0)
        detector.beat(100.0)

        decision = detector.evaluate(104.9)

        self.assertFalse(decision.should_dump)

    def test_dumps_once_when_gui_thread_stalls(self) -> None:
        detector = HangDetector(threshold_seconds=5.0, repeat_seconds=30.0)
        detector.beat(100.0)

        first = detector.evaluate(106.0)
        second = detector.evaluate(107.0)

        self.assertTrue(first.should_dump)
        self.assertEqual(first.episode, 1)
        self.assertEqual(first.dump_index, 1)
        self.assertAlmostEqual(first.stalled_seconds, 6.0)
        # Один эпизод — один отчёт, иначе каждый замер плодил бы файлы.
        self.assertFalse(second.should_dump)
        self.assertEqual(second.episode, 1)

    def test_repeats_dump_after_repeat_interval(self) -> None:
        detector = HangDetector(threshold_seconds=5.0, repeat_seconds=30.0)
        detector.beat(100.0)
        detector.evaluate(106.0)

        too_early = detector.evaluate(130.0)
        repeated = detector.evaluate(136.0)

        self.assertFalse(too_early.should_dump)
        self.assertTrue(repeated.should_dump)
        self.assertEqual(repeated.episode, 1)
        self.assertEqual(repeated.dump_index, 2)

    def test_stops_after_max_dumps_per_episode(self) -> None:
        detector = HangDetector(
            threshold_seconds=5.0,
            repeat_seconds=10.0,
            max_dumps_per_episode=2,
        )
        detector.beat(100.0)
        detector.evaluate(106.0)
        detector.evaluate(116.0)

        third = detector.evaluate(200.0)

        self.assertFalse(third.should_dump)

    def test_reports_recovery_with_peak_stall(self) -> None:
        detector = HangDetector(threshold_seconds=5.0)
        detector.beat(100.0)
        detector.evaluate(106.0)
        detector.evaluate(120.0)

        detector.beat(121.0)
        recovery = detector.evaluate(121.5)

        self.assertTrue(recovery.recovered)
        self.assertFalse(recovery.should_dump)
        self.assertEqual(recovery.episode, 1)
        self.assertAlmostEqual(recovery.stalled_seconds, 20.0)

    def test_new_episode_after_recovery(self) -> None:
        detector = HangDetector(threshold_seconds=5.0)
        detector.beat(100.0)
        detector.evaluate(106.0)
        detector.beat(107.0)
        detector.evaluate(107.5)

        second_episode = detector.evaluate(115.0)

        self.assertTrue(second_episode.should_dump)
        self.assertEqual(second_episode.episode, 2)
        self.assertEqual(second_episode.dump_index, 1)


class ThreadDumpFormatTests(unittest.TestCase):
    def test_lists_threads_and_marks_gui_thread(self) -> None:
        gui_ident = threading.get_ident()

        report = format_thread_dump(
            stalled_seconds=7.5,
            episode=3,
            dump_index=2,
            gui_thread_ident=gui_ident,
        )

        self.assertIn("GUI HANG REPORT", report)
        self.assertIn("Эпизод: 3 (дамп #2)", report)
        self.assertIn("7.5с", report)
        self.assertIn(f"Thread {gui_ident}", report)
        self.assertIn("[GUI]", report)
        # Стек текущего потока обязан быть в отчёте: иначе дамп бесполезен.
        self.assertIn("test_lists_threads_and_marks_gui_thread", report)

    def test_survives_thread_without_name_entry(self) -> None:
        report = format_thread_dump(
            stalled_seconds=6.0,
            episode=1,
            dump_index=1,
            gui_thread_ident=None,
            frames={999_999: _current_frame()},
            threads=[],
        )

        self.assertIn("Thread 999999: <unknown>", report)
        self.assertIn("GUI thread id: неизвестен", report)


def _current_frame():
    import inspect

    return inspect.currentframe()


class GuiHangWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.dump_dir = Path(self._tempdir.name)
        self.clock = _FakeClock()
        self.native_timer = _FakeNativeTimer()
        self.logs: list[tuple[str, str]] = []

    def _watchdog(self, **kwargs) -> GuiHangWatchdog:
        params = {
            "threshold_seconds": 5.0,
            "repeat_seconds": 30.0,
            "dump_dir": self.dump_dir,
            "clock": self.clock,
            "log_fn": lambda message, level: self.logs.append((message, level)),
            "native_timer": self.native_timer,
        }
        params.update(kwargs)
        return GuiHangWatchdog(**params)

    def test_writes_report_file_and_logs_hang(self) -> None:
        watchdog = self._watchdog()
        watchdog.beat()
        self.clock.advance(6.0)

        decision = watchdog.poll_once()

        self.assertTrue(decision.should_dump)
        reports = sorted(self.dump_dir.glob("hang_*.log"))
        self.assertEqual(len(reports), 1)
        self.assertIn("GUI HANG REPORT", reports[0].read_text(encoding="utf-8"))
        self.assertTrue(
            any("не отвечает" in message for message, _level in self.logs),
            self.logs,
        )
        self.assertTrue(
            any(str(reports[0]) in message for message, _level in self.logs),
            self.logs,
        )

    def test_quiet_run_writes_nothing(self) -> None:
        watchdog = self._watchdog()
        watchdog.beat()
        self.clock.advance(1.0)

        decision = watchdog.poll_once()

        self.assertFalse(decision.should_dump)
        self.assertEqual(list(self.dump_dir.glob("hang_*.log")), [])
        self.assertEqual(self.logs, [])

    def test_logs_recovery(self) -> None:
        watchdog = self._watchdog()
        watchdog.beat()
        self.clock.advance(6.0)
        watchdog.poll_once()
        self.logs.clear()

        watchdog.beat()
        decision = watchdog.poll_once()

        self.assertTrue(decision.recovered)
        self.assertTrue(
            any("снова отвечает" in message for message, _level in self.logs),
            self.logs,
        )

    def test_beat_arms_native_timer_with_threshold(self) -> None:
        watchdog = self._watchdog(threshold_seconds=7.0)

        watchdog.beat()
        watchdog.beat()

        self.assertEqual(self.native_timer.arm_calls, [7.0, 7.0])

    def test_broken_native_timer_does_not_break_beat(self) -> None:
        class _BrokenTimer(_FakeNativeTimer):
            def arm(self, timeout_seconds: float) -> None:
                raise OSError("no file descriptor")

        watchdog = self._watchdog(native_timer=_BrokenTimer())

        watchdog.beat()
        watchdog.beat()

        self.assertIsNotNone(watchdog.detector)
        self.assertTrue(any(level == "DEBUG" for _message, level in self.logs), self.logs)

    def test_dump_failure_is_logged_and_swallowed(self) -> None:
        blocked = self.dump_dir / "not-a-dir"
        blocked.write_text("busy", encoding="utf-8")
        watchdog = self._watchdog(dump_dir=blocked)
        watchdog.beat()
        self.clock.advance(6.0)

        decision = watchdog.poll_once()

        self.assertTrue(decision.should_dump)
        self.assertTrue(
            any("Не удалось сохранить отчёт" in message for message, _level in self.logs),
            self.logs,
        )

    def test_worker_thread_is_daemon_and_stops(self) -> None:
        watchdog = self._watchdog(poll_seconds=0.05)
        watchdog.start()
        try:
            worker = next(
                thread
                for thread in threading.enumerate()
                if thread.name == "GuiHangWatchdog"
            )
            # Не-демонический поток задержал бы выход интерпретатора.
            self.assertTrue(worker.daemon)
        finally:
            watchdog.stop(timeout=1.0)

        self.assertFalse(
            any(thread.name == "GuiHangWatchdog" for thread in threading.enumerate())
        )

    def test_stop_disarms_owned_native_timer_only(self) -> None:
        watchdog = self._watchdog()
        watchdog.beat()

        watchdog.stop(timeout=0.1)

        # Таймер передан снаружи — владелец его и закрывает, watchdog не трогает.
        self.assertFalse(self.native_timer.closed)


class FaulthandlerHangTimerTests(unittest.TestCase):
    """Уровень, который обязан пережить GIL-дедлок, проверяем на настоящем таймере."""

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


class HangWatchdogInstallTests(unittest.TestCase):
    def test_disabled_by_env_flag(self) -> None:
        self.assertTrue(is_hang_watchdog_disabled({DISABLE_ENV_VAR: "1"}))
        self.assertFalse(is_hang_watchdog_disabled({DISABLE_ENV_VAR: "0"}))
        self.assertFalse(is_hang_watchdog_disabled({}))

    def test_install_returns_none_when_disabled(self) -> None:
        previous = os.environ.get(DISABLE_ENV_VAR)
        os.environ[DISABLE_ENV_VAR] = "1"
        try:
            self.assertIsNone(install_gui_hang_watchdog(None))
        finally:
            if previous is None:
                os.environ.pop(DISABLE_ENV_VAR, None)
            else:
                os.environ[DISABLE_ENV_VAR] = previous

    def test_qt_timer_feeds_watchdog_from_gui_thread(self) -> None:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication(["tests"])
        watchdog = install_gui_hang_watchdog(app, beat_seconds=0.1, poll_seconds=0.1)
        self.assertIsNotNone(watchdog)
        self.addCleanup(watchdog.stop, timeout=1.0)

        deadline = time.monotonic() + 3.0
        while watchdog.gui_thread_ident is None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)

        # Биение обязано приходить именно из GUI-потока: чужой поток отметил бы
        # живым цикл событий, которого нет.
        self.assertEqual(watchdog.gui_thread_ident, threading.get_ident())

    def test_qt_bootstrap_installs_watchdog(self) -> None:
        """Модуль без вызова бесполезен: контракт закрепляем статически."""
        source = (
            Path(__file__).resolve().parents[1] / "src" / "main" / "qt_runtime.py"
        ).read_text(encoding="utf-8")

        self.assertIn("from log.hang_watchdog import install_gui_hang_watchdog", source)
        self.assertIn("install_gui_hang_watchdog(app)", source)


if __name__ == "__main__":
    unittest.main()
