"""Общий такт кадров: анимации рисуются в одном кадре и не тикают в пустоту."""

from __future__ import annotations

import ctypes
import os
import unittest
from ctypes import wintypes
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication, QWidget

from ui import windows_screen_presence as presence
from ui.frame_clock import BASE_FRAME_MS, FrameClock, frame_clock


class FrameClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.clock = FrameClock()
        self.addCleanup(self.clock.deleteLater)

    def test_clock_runs_only_while_someone_is_subscribed(self) -> None:
        subscription = self.clock.subscribe(lambda: None, interval_ms=33)
        self.assertFalse(self.clock.is_running())

        subscription.start()
        self.assertTrue(self.clock.is_running())
        self.assertTrue(subscription.isActive())

        subscription.stop()
        self.assertFalse(self.clock.is_running())
        self.assertEqual(self.clock.frames_per_second(), 0.0)

    def test_animations_share_one_frame(self) -> None:
        fired: list[str] = []
        first = self.clock.subscribe(lambda: fired.append("badge"), interval_ms=33)
        second = self.clock.subscribe(lambda: fired.append("dot"), interval_ms=33)
        first.start()
        second.start()

        # Всем хватает 30 кадров — таймер не просыпается 60 раз в секунду.
        self.assertAlmostEqual(self.clock.frames_per_second(), 30.0, places=3)
        self.clock._tick()
        self.assertEqual(fired, ["badge", "dot"])

    def test_requested_interval_snaps_to_shared_frames(self) -> None:
        subscription = self.clock.subscribe(lambda: None, interval_ms=40)
        self.assertAlmostEqual(subscription.interval(), 2 * BASE_FRAME_MS)
        subscription.setInterval(16)
        self.assertAlmostEqual(subscription.interval(), BASE_FRAME_MS)

    def test_slower_animation_fires_on_its_own_frames(self) -> None:
        fast: list[int] = []
        slow: list[int] = []
        fast_sub = self.clock.subscribe(lambda: fast.append(1), interval_ms=16)
        slow_sub = self.clock.subscribe(lambda: slow.append(1), interval_ms=33)
        fast_sub.start()
        slow_sub.start()
        self.assertAlmostEqual(self.clock.frames_per_second(), 60.0, places=3)

        for _ in range(6):
            self.clock._tick()
        self.assertEqual((len(fast), len(slow)), (6, 3))

    def test_slow_animation_keeps_firing_after_fast_one_stops(self) -> None:
        slow: list[int] = []
        fast_sub = self.clock.subscribe(lambda: None, interval_ms=16)
        slow_sub = self.clock.subscribe(lambda: slow.append(1), interval_ms=33)
        fast_sub.start()
        slow_sub.start()
        self.clock._tick()  # счётчик кадров стал нечётным
        fast_sub.stop()

        for _ in range(4):
            self.clock._tick()
        self.assertEqual(len(slow), 4)

    def test_pause_stops_timer_until_every_reason_is_gone(self) -> None:
        subscription = self.clock.subscribe(lambda: None, interval_ms=33)
        subscription.start()

        self.clock.set_paused("display_off", True)
        self.clock.set_paused("session_locked", True)
        self.assertFalse(self.clock.is_running())
        self.assertTrue(subscription.isActive())

        self.clock.set_paused("display_off", False)
        self.assertFalse(self.clock.is_running())
        self.clock.set_paused("session_locked", False)
        self.assertTrue(self.clock.is_running())

        self.clock.set_paused("display_off", True)
        self.clock.resume_all()
        self.assertTrue(self.clock.is_running())

    def test_subscription_ends_with_its_widget(self) -> None:
        owner = QObject()
        subscription = self.clock.subscribe(lambda: None, interval_ms=33, owner=owner)
        subscription.start()

        from PyQt6 import sip

        sip.delete(owner)
        self.assertFalse(subscription.isActive())
        self.assertFalse(self.clock.is_running())

    def test_dead_widget_callback_is_dropped(self) -> None:
        def callback() -> None:
            raise RuntimeError("wrapped C/C++ object has been deleted")

        subscription = self.clock.subscribe(callback, interval_ms=33)
        subscription.start()
        self.clock._tick()
        self.assertFalse(subscription.isActive())

    def test_elapsed_time_restarts_with_start(self) -> None:
        subscription = self.clock.subscribe(lambda: None, interval_ms=33)
        with mock.patch("ui.frame_clock.time.monotonic", return_value=100.0):
            subscription.start()
        with mock.patch("ui.frame_clock.time.monotonic", return_value=100.25):
            self.assertAlmostEqual(subscription.elapsed_ms(), 250.0)


class SharedClockAnimationsTests(unittest.TestCase):
    """Значок «Работает» и точка статуса рисуются в общем кадре, а не каждый по своему таймеру."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_title_badge_and_status_dot_use_the_application_clock(self) -> None:
        import ui.launch_title_badge as badge_module
        import ui.pulsing_dot as dot_module

        for module in (badge_module, dot_module):
            patcher = mock.patch.object(module, "are_live_animations_enabled", return_value=True)
            patcher.start()
            self.addCleanup(patcher.stop)

        host = QWidget()
        self.addCleanup(host.deleteLater)
        badge = badge_module.LaunchTitleBadge(host, language_provider=lambda: "ru")
        dot = dot_module.PulsingDot(host)
        badge.set_state(phase="running", launch_method="zapret2_mode")
        host.show()
        QApplication.processEvents()
        dot.start_pulse()

        clock = frame_clock()
        self.assertTrue(badge.is_pulsing())
        self.assertTrue(dot.is_beating())
        self.assertIn(badge._pulse, clock._subscriptions)
        self.assertIn(dot._beat, clock._subscriptions)
        self.assertAlmostEqual(clock.frames_per_second(), 30.0, places=3)

        with mock.patch.object(badge, "update") as badge_update, mock.patch.object(dot, "update") as dot_update:
            clock._tick()
        badge_update.assert_called_once()
        dot_update.assert_called_once()

        host.hide()
        self.assertFalse(badge.is_pulsing())
        self.assertFalse(dot.is_beating())

    def test_badge_pulse_phase_follows_time_not_frame_count(self) -> None:
        import ui.launch_title_badge as badge_module

        patcher = mock.patch.object(badge_module, "are_live_animations_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        badge = badge_module.LaunchTitleBadge(language_provider=lambda: "ru")
        self.addCleanup(badge.deleteLater)
        badge.set_state(phase="running", launch_method="zapret2_mode")
        QApplication.processEvents()

        with mock.patch.object(badge._pulse, "elapsed_ms", return_value=badge_module.RUNNING_PULSE_MS * 2.25):
            badge._on_pulse_frame()
        self.assertAlmostEqual(badge._pulse_t, 0.25)


class ScreenPresenceTests(unittest.TestCase):
    """Сеанс заблокирован или дисплей выключен — кадры не рисуются."""

    def _message(self, message_id: int, wparam: int = 0, lparam: int = 0) -> wintypes.MSG:
        msg = wintypes.MSG()
        msg.message = message_id
        msg.wParam = wparam
        msg.lParam = lparam
        return msg

    def test_session_events_map_to_pause(self) -> None:
        self.assertEqual(presence.session_pause_change(presence.WTS_SESSION_LOCK), (presence.PAUSE_SESSION_LOCKED, True))
        self.assertEqual(
            presence.session_pause_change(presence.WTS_SESSION_UNLOCK), (presence.PAUSE_SESSION_LOCKED, False)
        )
        self.assertEqual(
            presence.session_pause_change(presence.WTS_REMOTE_DISCONNECT),
            (presence.PAUSE_SESSION_DISCONNECTED, True),
        )
        self.assertEqual(
            presence.session_pause_change(presence.WTS_CONSOLE_CONNECT),
            (presence.PAUSE_SESSION_DISCONNECTED, False),
        )
        self.assertIsNone(presence.session_pause_change(0x5))  # вход в систему: кадрам не важно

    def test_lock_message_pauses_clock(self) -> None:
        clock = mock.Mock()
        msg = self._message(presence.WM_WTSSESSION_CHANGE, presence.WTS_SESSION_LOCK)
        presence.handle_native_screen_presence(ctypes.addressof(msg), clock=clock)
        clock.set_paused.assert_called_once_with(presence.PAUSE_SESSION_LOCKED, True)

    def test_display_off_and_on_messages(self) -> None:
        for state, paused in ((0, True), (1, False), (2, False)):
            with self.subTest(state=state):
                setting = presence._POWERBROADCAST_SETTING()
                setting.PowerSetting = presence._GUID_CONSOLE_DISPLAY_STATE
                setting.DataLength = ctypes.sizeof(wintypes.DWORD)
                setting.Data = state
                msg = self._message(
                    presence.WM_POWERBROADCAST,
                    presence.PBT_POWERSETTINGCHANGE,
                    ctypes.addressof(setting),
                )
                clock = mock.Mock()
                presence.handle_native_screen_presence(ctypes.addressof(msg), clock=clock)
                clock.set_paused.assert_called_once_with(presence.PAUSE_DISPLAY_OFF, paused)

    def test_other_power_settings_are_ignored(self) -> None:
        setting = presence._POWERBROADCAST_SETTING()
        setting.DataLength = ctypes.sizeof(wintypes.DWORD)
        msg = self._message(presence.WM_POWERBROADCAST, presence.PBT_POWERSETTINGCHANGE, ctypes.addressof(setting))
        clock = mock.Mock()
        presence.handle_native_screen_presence(ctypes.addressof(msg), clock=clock)
        clock.set_paused.assert_not_called()

    def test_message_id_is_read_only_on_windows(self) -> None:
        msg = self._message(presence.WM_WTSSESSION_CHANGE)
        self.assertEqual(
            presence.native_message_id(ctypes.addressof(msg), platform="win32"), presence.WM_WTSSESSION_CHANGE
        )
        self.assertIsNone(presence.native_message_id(ctypes.addressof(msg), platform="linux"))

    def test_window_parses_each_native_message_once(self) -> None:
        import inspect

        from main import window_lifecycle

        source = inspect.getsource(window_lifecycle)
        native_event = source[source.index("def nativeEvent") : source.index("def showMinimized")]
        self.assertEqual(native_event.count("native_message_id(message)"), 1)
        self.assertIn("handle_native_screen_presence(message)", native_event)
        self.assertIn("frame_clock().resume_all()", source)


if __name__ == "__main__":
    unittest.main()
