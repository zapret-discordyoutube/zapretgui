from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

import presets.ui.control.top_summary_widget as summary_module
import ui.pulsing_dot as dot_module
import ui.widgets.gesture_buttons as buttons_module
import ui.widgets.motion_icon as motion_module
from donater.premium_display import TIER_ACTIVE, TIER_FREE, PremiumDisplay
from presets.ui.control.top_summary_widget import ControlTopSummaryWidget
from ui.pulsing_dot import PulsingDot
from ui.widgets.gesture_buttons import ICON_GESTURE_SQUEEZE, GesturePushButton
from ui.widgets.motion_icon import GESTURE_BOUNCE, MotionIcon


def _host(test: unittest.TestCase, child: QWidget) -> QWidget:
    host = QWidget()
    QVBoxLayout(host).addWidget(child)
    test.addCleanup(host.deleteLater)
    return host


def _animations(enabled: bool, *modules):
    patches = [mock.patch.object(module, "are_animations_enabled", return_value=enabled) for module in modules]
    for patcher in patches:
        patcher.start()
    return patches


class PulsingDotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._patches = _animations(True, dot_module)
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def test_beat_then_rest_without_frames(self) -> None:
        dot = PulsingDot()
        _host(self, dot).show()

        dot.start_pulse()
        self.assertTrue(dot.is_beating())
        self.assertFalse(dot._rest_timer.isActive())

        dot._finish_beat()
        # После удара кадры не рисуются: ждёт только одиночный таймер паузы.
        self.assertFalse(dot.is_beating())
        self.assertTrue(dot._rest_timer.isActive())

    def test_hidden_or_stopped_dot_does_not_animate(self) -> None:
        dot = PulsingDot()
        host = _host(self, dot)

        dot.start_pulse()
        self.assertTrue(dot._is_pulsing)
        self.assertFalse(dot.is_beating())

        host.show()
        self.assertTrue(dot.is_beating())

        host.hide()
        self.assertFalse(dot.is_beating())
        self.assertFalse(dot._rest_timer.isActive())

        host.show()
        dot.stop_pulse()
        self.assertFalse(dot.is_beating())
        self.assertFalse(dot._rest_timer.isActive())

    def test_beat_is_drawn_at_modest_frame_rate(self) -> None:
        dot = PulsingDot()
        self.addCleanup(dot.deleteLater)
        self.assertGreaterEqual(dot._beat.interval(), 30)
        duty = dot_module.BEAT_DURATION_MS / (dot_module.BEAT_DURATION_MS + dot_module.BEAT_REST_MS)
        frames_per_second = duty * 1000 / dot._beat.interval()
        self.assertLess(frames_per_second, 10.0)

    def test_no_beat_when_animations_are_disabled(self) -> None:
        for patcher in self._patches:
            patcher.stop()
        self._patches = _animations(False, dot_module)
        dot = PulsingDot()
        _host(self, dot).show()

        dot.start_pulse()

        self.assertTrue(dot._is_pulsing)
        self.assertFalse(dot.is_beating())

    def test_color_fades_when_visible_and_jumps_when_hidden(self) -> None:
        dot = PulsingDot()
        host = _host(self, dot)
        dot.set_color("#808080")
        self.assertEqual(dot._color, QColor("#808080"))

        host.show()
        dot.set_color("#4caf50")
        self.assertEqual(dot._fade.state(), dot._fade.State.Running)
        dot._fade.setCurrentTime(dot._fade.duration())
        self.assertEqual(dot._color, QColor("#4caf50"))


class MotionIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_keeps_label_like_pixmap_api(self) -> None:
        icon = MotionIcon(size=24)
        self.addCleanup(icon.deleteLater)
        self.assertTrue(icon.pixmap().isNull())
        pixmap = QPixmap(22, 22)
        icon.setPixmap(pixmap)
        self.assertFalse(icon.pixmap().isNull())

    def test_twinkle_timer_runs_only_while_visible(self) -> None:
        patches = _animations(True, motion_module)
        self.addCleanup(lambda: [p.stop() for p in patches])
        icon = MotionIcon(size=24)
        host = _host(self, icon)

        icon.set_idle_twinkle(9000)
        self.assertFalse(icon._twinkle_timer.isActive())

        host.show()
        self.assertTrue(icon._twinkle_timer.isActive())

        host.hide()
        self.assertFalse(icon._twinkle_timer.isActive())

        host.show()
        icon.set_idle_twinkle(0)
        self.assertFalse(icon._twinkle_timer.isActive())


class ControlTopSummaryAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._patches = _animations(True, summary_module, motion_module)
        self.addCleanup(lambda: [p.stop() for p in self._patches])
        self.summary = ControlTopSummaryWidget(language="ru", mode_value="Zapret 2")
        _host(self, self.summary).show()

    def test_first_profile_count_does_not_animate(self) -> None:
        self.summary.set_profile_count(70)

        self.assertFalse(self.summary._profile_roll.state() == self.summary._profile_roll.State.Running)
        self.assertEqual(self.summary.profiles_item._icon_label.gesture(), "")

    def test_changed_profile_count_rolls_and_bounces(self) -> None:
        self.summary.set_profile_count(70)
        self.summary.set_profile_count(74)

        roll = self.summary._profile_roll
        self.assertEqual(roll.state(), roll.State.Running)
        self.assertEqual(self.summary.profiles_item._icon_label.gesture(), GESTURE_BOUNCE)

        roll.setCurrentTime(roll.duration())
        self.assertEqual(self.summary.profiles_item._value_label.text(), "74 включено")

    def test_premium_star_glows_and_twinkles(self) -> None:
        star = self.summary.premium_item._icon_label
        self.assertIsNone(star._glow)
        self.assertEqual(star._twinkle_interval_ms, 0)

        self.summary.set_premium(PremiumDisplay(tier=TIER_FREE))
        self.assertIsNone(star._glow)
        self.assertGreater(star._twinkle_interval_ms, 0)

        self.summary.set_premium(PremiumDisplay(tier=TIER_ACTIVE))
        self.assertIsNotNone(star._glow)
        self.assertEqual(star.gesture(), GESTURE_BOUNCE)


class GestureButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_click_plays_icon_gesture(self) -> None:
        patches = _animations(True, buttons_module)
        self.addCleanup(lambda: [p.stop() for p in patches])
        button = GesturePushButton("Стоп")
        _host(self, button).show()
        button.set_icon_gesture(ICON_GESTURE_SQUEEZE)

        button.click()

        self.assertTrue(button.is_icon_gesture_running())
        anim = button._icon_gesture_anim
        anim.setCurrentTime(anim.duration() // 5)
        _dx, scale, _angle = button._icon_gesture_transform()
        self.assertLess(scale, 1.0)

    def test_no_gesture_when_animations_are_disabled(self) -> None:
        patches = _animations(False, buttons_module)
        self.addCleanup(lambda: [p.stop() for p in patches])
        button = GesturePushButton("Стоп")
        _host(self, button).show()
        button.set_icon_gesture(ICON_GESTURE_SQUEEZE)

        button.click()

        self.assertFalse(button.is_icon_gesture_running())


if __name__ == "__main__":
    unittest.main()
