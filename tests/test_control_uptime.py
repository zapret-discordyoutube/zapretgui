from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

import winws_runtime.state.launch_runtime_service as service_module
from app.state_store import AppUiState, MainWindowStateStore
from presets.ui.control.control_page_runtime_shared import apply_running_since
from presets.ui.control.uptime_label import UptimeLabel, format_uptime
from winws_runtime.runtime.process_probe import filetime_to_unix_seconds
from winws_runtime.state.launch_runtime_service import LaunchRuntimeService


class UptimeTextTests(unittest.TestCase):
    def test_formats_minutes_hours_and_days(self) -> None:
        cases = {
            0: "меньше минуты",
            59: "меньше минуты",
            60: "1 мин",
            14 * 60 + 30: "14 мин",
            2 * 3600 + 14 * 60: "2 ч 14 мин",
            24 * 3600 - 1: "23 ч 59 мин",
            3 * 86400 + 4 * 3600 + 120: "3 д 4 ч",
            -5: "меньше минуты",
        }
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(format_uptime(seconds, language="ru"), expected)

    def test_english_text(self) -> None:
        self.assertEqual(format_uptime(2 * 3600 + 14 * 60, language="en"), "2 h 14 min")

    def test_filetime_converts_to_unix_seconds(self) -> None:
        # 2026-01-01 00:00:00 UTC = 1767225600 секунд Unix.
        ticks = 1767225600 * 10_000_000 + 116444736000000000
        self.assertEqual(filetime_to_unix_seconds(ticks & 0xFFFFFFFF, ticks >> 32), 1767225600.0)
        self.assertEqual(filetime_to_unix_seconds(0, 0), 0.0)


class LaunchRunningSinceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MainWindowStateStore(AppUiState())
        self.service = LaunchRuntimeService(self.store)
        self.now = 1_000_000.0
        clock = mock.patch.object(service_module.time, "time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.process_started = 0.0
        probe = mock.patch.object(
            service_module, "query_process_start_time", side_effect=lambda _pid: self.process_started
        )
        probe.start()
        self.addCleanup(probe.stop)

    def _since(self) -> float:
        return self.store.snapshot().launch_running_since

    def test_running_since_is_the_process_creation_time(self) -> None:
        self.process_started = self.now - 7200
        self.service.begin_start(expected_process="winws2.exe")
        self.assertEqual(self._since(), 0.0)

        self.service.mark_running(pid=4242)

        self.assertEqual(self._since(), self.now - 7200)

    def test_without_process_time_counts_from_the_first_running_moment(self) -> None:
        self.service.mark_running(pid=4242)
        self.assertEqual(self._since(), self.now)

        # Повторное подтверждение «работает» не сбрасывает отсчёт.
        self.now += 300
        self.service.mark_running(pid=4242)
        self.assertEqual(self._since(), self.now - 300)

    def test_process_time_from_the_future_is_ignored(self) -> None:
        self.process_started = self.now + 3600
        self.service.mark_running(pid=4242)
        self.assertEqual(self._since(), self.now)

    def test_found_running_process_gets_its_real_start_once_pid_is_known(self) -> None:
        # Программу открыли, когда обход уже шёл: сначала известно только «работает».
        self.service.bootstrap_probe(True, expected_process="winws2.exe")
        self.assertEqual(self._since(), self.now)

        self.process_started = self.now - 5400
        self.service.observe_process_details({"winws2.exe": [777]})

        self.assertEqual(self._since(), self.now - 5400)

    def test_every_non_running_phase_clears_the_time(self) -> None:
        for leave in (
            self.service.begin_stop,
            self.service.mark_stopped,
            lambda: self.service.mark_start_failed("ошибка"),
            lambda: self.service.begin_start(expected_process="winws2.exe"),
            lambda: self.service.bootstrap_probe(False),
        ):
            with self.subTest(leave=leave):
                self.service.mark_running(pid=4242)
                self.assertGreater(self._since(), 0.0)
                leave()
                self.assertEqual(self._since(), 0.0)


class UptimeLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.now = 50_000.0
        self.host = QWidget()
        self.label = UptimeLabel(self.host, clock=lambda: self.now)
        QVBoxLayout(self.host).addWidget(self.label)
        self.addCleanup(self.host.deleteLater)

    def test_hidden_and_silent_until_bypass_runs(self) -> None:
        self.host.show()
        self.assertTrue(self.label.isHidden())
        self.assertFalse(self.label._timer.isActive())

    def test_shows_uptime_and_wakes_at_the_next_minute(self) -> None:
        self.host.show()
        self.label.set_running_since(self.now - (2 * 3600 + 14 * 60 + 45))

        self.assertFalse(self.label.isHidden())
        self.assertEqual(self.label.text(), "·  2 ч 14 мин")
        self.assertEqual(self.label.property("screenReaderStateText"), "Обход работает: 2 ч 14 мин")
        self.assertTrue(self.label._timer.isActive())
        self.assertTrue(self.label._timer.isSingleShot())
        # До следующей минуты осталось 15 секунд.
        self.assertAlmostEqual(self.label._timer.interval(), 15_250, delta=5)

        self.now += 15.3
        self.label._refresh()
        self.assertEqual(self.label.text(), "·  2 ч 15 мин")

    def test_timer_sleeps_while_hidden_and_text_is_fresh_on_return(self) -> None:
        self.label.set_running_since(self.now - 120)
        self.assertFalse(self.label._timer.isActive())

        self.host.show()
        self.assertTrue(self.label._timer.isActive())
        self.host.hide()
        self.assertFalse(self.label._timer.isActive())

        self.now += 600
        self.host.show()
        self.assertEqual(self.label.text(), "·  12 мин")

    def test_stopping_hides_the_label(self) -> None:
        self.host.show()
        self.label.set_running_since(self.now - 120)
        self.label.set_running_since(0.0)

        self.assertTrue(self.label.isHidden())
        self.assertEqual(self.label.text(), "")
        self.assertFalse(self.label._timer.isActive())

    def test_language_switch_updates_text(self) -> None:
        self.host.show()
        self.label.set_running_since(self.now - 3 * 60)
        self.label.set_language("en")
        self.assertEqual(self.label.text(), "·  3 min")

    def test_page_passes_time_only_in_running_phase(self) -> None:
        self.host.show()
        apply_running_since(self.label, SimpleNamespace(launch_phase="running", launch_running_since=self.now - 60))
        self.assertEqual(self.label.running_since(), self.now - 60)

        apply_running_since(self.label, SimpleNamespace(launch_phase="stopping", launch_running_since=self.now - 60))
        self.assertEqual(self.label.running_since(), 0.0)
        apply_running_since(None, SimpleNamespace(launch_phase="running", launch_running_since=1.0))


if __name__ == "__main__":
    unittest.main()
