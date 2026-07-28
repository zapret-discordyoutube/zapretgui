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


if __name__ == "__main__":
    unittest.main()
