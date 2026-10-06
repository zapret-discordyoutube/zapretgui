from __future__ import annotations

import unittest
from unittest.mock import patch

from ui.ui_freeze_watchdog import (
    FREEZE_THRESHOLD_SECONDS,
    HEARTBEAT_INTERVAL_MS,
    UI_JITTER_ENV,
    UiFreezeWatchdog,
    build_watchdog_settings,
    jitter_threshold_ms,
)


class JitterThresholdTests(unittest.TestCase):
    def test_missing_env_keeps_default_mode(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop(UI_JITTER_ENV, None)
            self.assertEqual(jitter_threshold_ms(), 0)

    def test_env_value_enables_jitter_mode(self) -> None:
        with patch.dict("os.environ", {UI_JITTER_ENV: "60"}):
            self.assertEqual(jitter_threshold_ms(), 60)

    def test_too_small_and_broken_values_are_ignored(self) -> None:
        for raw in ("5", "abc", "", "-100"):
            with self.subTest(raw=raw), patch.dict("os.environ", {UI_JITTER_ENV: raw}):
                self.assertEqual(jitter_threshold_ms(), 0)


class WatchdogSettingsTests(unittest.TestCase):
    def test_default_settings_are_empty(self) -> None:
        self.assertEqual(build_watchdog_settings(0), {})

    def test_jitter_settings_beat_faster_than_threshold(self) -> None:
        settings = build_watchdog_settings(60)

        self.assertEqual(settings["freeze_threshold_seconds"], 0.06)
        self.assertLess(settings["heartbeat_interval_ms"] / 1000.0, settings["freeze_threshold_seconds"])
        self.assertNotIn("thread_dump_min_seconds", settings)

    def test_jitter_watchdog_keeps_requested_threshold(self) -> None:
        watchdog = UiFreezeWatchdog(**build_watchdog_settings(60))

        self.assertAlmostEqual(watchdog._freeze_threshold, 0.06)
        self.assertEqual(watchdog._heartbeat_interval_ms, 20)

    def test_default_watchdog_is_unchanged(self) -> None:
        watchdog = UiFreezeWatchdog()

        self.assertAlmostEqual(watchdog._freeze_threshold, FREEZE_THRESHOLD_SECONDS)
        self.assertEqual(watchdog._heartbeat_interval_ms, HEARTBEAT_INTERVAL_MS)


class JitterReportTests(unittest.TestCase):
    def test_short_stall_is_reported_as_jitter_in_milliseconds(self) -> None:
        messages: list[tuple[str, str]] = []
        watchdog = UiFreezeWatchdog(
            **build_watchdog_settings(60),
            log_fn=lambda message, level: messages.append((message, level)),
            stack_fn=lambda: "<стек>",
        )

        watchdog._report_freeze_started(0.085)

        self.assertEqual(len(messages), 1)
        self.assertIn("Рывок интерфейса", messages[0][0])
        self.assertIn("85мс", messages[0][0])

    def test_long_freeze_keeps_the_original_wording(self) -> None:
        messages: list[tuple[str, str]] = []
        watchdog = UiFreezeWatchdog(
            log_fn=lambda message, level: messages.append((message, level)),
            stack_fn=lambda: "<стек>",
        )

        watchdog._report_freeze_started(3.2)

        self.assertIn("Интерфейс не отвечает 3.2с", messages[0][0])



class JitterBusyThreadsTests(unittest.TestCase):
    """Рывок без питоновского стека: видно, какой фоновый поток держал GIL."""

    def test_jitter_report_names_busy_background_threads(self) -> None:
        messages: list[str] = []
        watchdog = UiFreezeWatchdog(
            **build_watchdog_settings(60),
            log_fn=lambda message, _level: messages.append(message),
            stack_fn=lambda: "<стек>",
            busy_threads_fn=lambda: "  StartupQueue-lists: ipsets_manager.py:74 _normalize_ip_entry",
        )

        watchdog._report_freeze_started(0.085)

        self.assertIn("Занятые фоновые потоки", messages[0])
        self.assertIn("StartupQueue-lists", messages[0])

    def test_report_without_busy_threads_has_no_empty_section(self) -> None:
        messages: list[str] = []
        watchdog = UiFreezeWatchdog(
            **build_watchdog_settings(60),
            log_fn=lambda message, _level: messages.append(message),
            stack_fn=lambda: "<стек>",
            busy_threads_fn=lambda: "",
        )

        watchdog._report_freeze_started(0.085)

        self.assertNotIn("Занятые фоновые потоки", messages[0])

    def test_real_freeze_report_stays_without_thread_list(self) -> None:
        messages: list[str] = []
        watchdog = UiFreezeWatchdog(
            log_fn=lambda message, _level: messages.append(message),
            stack_fn=lambda: "<стек>",
            busy_threads_fn=lambda: "  worker: file.py:1 run",
            thread_dump_min_seconds=1_000.0,
        )

        watchdog._report_freeze_started(2.5)

        # Для настоящих зависаний есть отдельный файл со стеками всех потоков.
        self.assertNotIn("Занятые фоновые потоки", messages[0])

    def test_busy_thread_listing_shows_working_thread_and_skips_sleeping_one(self) -> None:
        import threading
        import time

        from ui.ui_freeze_watchdog import _format_busy_threads

        stop = threading.Event()
        started = threading.Event()

        def zapret_busy_loop() -> None:
            started.set()
            while not stop.is_set():
                sum(range(200))

        def zapret_sleeper() -> None:
            stop.wait(10)

        busy = threading.Thread(target=zapret_busy_loop, name="zapret-busy-worker", daemon=True)
        idle = threading.Thread(target=zapret_sleeper, name="zapret-idle-worker", daemon=True)
        busy.start()
        idle.start()
        try:
            started.wait(2)
            time.sleep(0.05)
            listing = _format_busy_threads()
        finally:
            stop.set()
            busy.join(2)
            idle.join(2)

        self.assertIn("zapret-busy-worker", listing)
        self.assertIn("zapret_busy_loop", listing)
        self.assertNotIn("zapret-idle-worker", listing)


if __name__ == "__main__":
    unittest.main()
