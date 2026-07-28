"""Наблюдатель за блокировками интерфейса."""

from __future__ import annotations

import threading
import time
import unittest


class UiFreezeWatchdogTests(unittest.TestCase):
    def _build(self, **kwargs):
        from PyQt6.QtWidgets import QApplication

        from ui.ui_freeze_watchdog import UiFreezeWatchdog

        QApplication.instance() or QApplication([])
        records: list[tuple[str, str]] = []
        watchdog = UiFreezeWatchdog(
            freeze_threshold_seconds=kwargs.pop("freeze_threshold_seconds", 0.3),
            repeat_seconds=kwargs.pop("repeat_seconds", 1.0),
            log_fn=lambda message, level: records.append((message, level)),
            stack_fn=lambda: "  File \"page.py\", line 1, in run\n    blocking_call()",
            **kwargs,
        )
        self.addCleanup(watchdog.stop)
        return watchdog, records

    def test_quiet_while_event_loop_answers(self) -> None:
        watchdog, records = self._build()
        watchdog.beat()
        watchdog.check_once()
        self.assertEqual(records, [])

    def test_reports_freeze_with_gui_stack(self) -> None:
        watchdog, records = self._build(freeze_threshold_seconds=0.2)

        watchdog.beat()
        time.sleep(0.35)
        stall = watchdog.check_once()

        self.assertGreaterEqual(stall, 0.2)
        self.assertEqual(len(records), 1)
        message, level = records[0]
        self.assertIn("Интерфейс не отвечает", message)
        self.assertIn("blocking_call()", message)
        self.assertIn("WARNING", level)

    def test_reports_recovery_with_duration(self) -> None:
        watchdog, records = self._build(freeze_threshold_seconds=0.2)

        watchdog.beat()
        time.sleep(0.35)
        watchdog.check_once()
        watchdog.beat()
        watchdog.check_once()

        self.assertEqual(len(records), 2)
        self.assertIn("Интерфейс снова отвечает", records[1][0])
        self.assertIn("INFO", records[1][1])
        self.assertGreater(watchdog.longest_freeze_seconds, 0.0)

    def test_long_freeze_reported_repeatedly_but_throttled(self) -> None:
        watchdog, records = self._build(freeze_threshold_seconds=0.2, repeat_seconds=0.4)

        watchdog.beat()
        time.sleep(0.3)
        watchdog.check_once()          # первый отчёт
        watchdog.check_once()          # ещё рано для повтора
        self.assertEqual(len(records), 1)

        time.sleep(0.45)
        watchdog.check_once()          # прошёл repeat_seconds
        self.assertEqual(len(records), 2)
        self.assertIn("всё ещё не отвечает", records[1][0])

    def test_background_loop_detects_blocked_gui_thread(self) -> None:
        watchdog, records = self._build(heartbeat_interval_ms=50, freeze_threshold_seconds=0.2)
        watchdog.start()
        self.addCleanup(watchdog.stop)

        # Наблюдатель живёт в своём потоке: heartbeat не приходит, потому что
        # GUI-поток «занят» и не крутит event loop.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not records:
            time.sleep(0.05)

        self.assertTrue(records, "watchdog не заметил остановку event loop")
        self.assertIn("Интерфейс не отвечает", records[0][0])

    def test_gui_stack_snapshot_contains_frames(self) -> None:
        from ui.ui_freeze_watchdog import _format_gui_stack
        from ui.ui_thread_guard import mark_gui_thread, reset_gui_thread_marker

        reset_gui_thread_marker()
        self.addCleanup(reset_gui_thread_marker)
        mark_gui_thread()

        captured: list[str] = []
        thread = threading.Thread(target=lambda: captured.append(_format_gui_stack()))
        thread.start()
        thread.join(5)

        self.assertTrue(captured)
        self.assertIn("test_gui_stack_snapshot_contains_frames", captured[0])


if __name__ == "__main__":
    unittest.main()
