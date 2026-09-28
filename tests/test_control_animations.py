from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

import presets.ui.control.top_summary_widget as summary_module
import ui.pulsing_dot as dot_module
import ui.widgets.motion_icon as motion_module
import ui.widgets.soft_visibility as soft_module
from ui.widgets.soft_visibility import set_visible_softly, soft_visibility_target
from donater.premium_display import TIER_ACTIVE, TIER_FREE, PremiumDisplay
from presets.ui.control.top_summary_widget import ControlTopSummaryWidget
from ui.pulsing_dot import PacketFlowIndicator, PulsingDot
from ui.widgets.motion_icon import GESTURE_BOUNCE, MotionIcon


def _host(test: unittest.TestCase, child: QWidget) -> QWidget:
    host = QWidget()
    QVBoxLayout(host).addWidget(child)
    test.addCleanup(host.deleteLater)
    return host


def _animations(enabled: bool, *modules):
    patches = [mock.patch.object(module, "are_live_animations_enabled", return_value=enabled) for module in modules]
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
        self.assertEqual(dot._color, QColor("#4caf50"))
        self.assertNotEqual(dot._shown_color, QColor("#4caf50"))
        dot._fade.setCurrentTime(dot._fade.duration())
        self.assertEqual(dot._shown_color, QColor("#4caf50"))


class PacketFlowIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._enabled = True
        patcher = mock.patch.object(dot_module, "are_live_animations_enabled", side_effect=lambda: self._enabled)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_flow_is_continuous_without_rest_pause(self) -> None:
        flow = PacketFlowIndicator()
        _host(self, flow).show()
        self.assertEqual(flow.width(), dot_module.FLOW_WIDTH)

        flow.start_pulse()
        with mock.patch.object(flow._beat_clock, "elapsed", return_value=dot_module.BEAT_DURATION_MS * 3):
            flow._on_beat_frame()

        self.assertTrue(flow.is_beating())
        self.assertFalse(flow._rest_timer.isActive())
        self.assertGreater(flow._flow_time, 0.0)

    def test_flow_stops_when_hidden_or_stopped(self) -> None:
        flow = PacketFlowIndicator()
        host = _host(self, flow)
        flow.start_pulse()
        self.assertFalse(flow.is_beating())

        host.show()
        self.assertTrue(flow.is_beating())
        host.hide()
        self.assertFalse(flow.is_beating())

        host.show()
        flow.stop_pulse()
        self.assertFalse(flow.is_beating())
        self.assertFalse(flow._rest_timer.isActive())

    def test_flow_halts_on_next_frame_when_animations_turn_off(self) -> None:
        flow = PacketFlowIndicator()
        _host(self, flow).show()
        flow.start_pulse()
        self.assertTrue(flow.is_beating())

        self._enabled = False
        flow._on_beat_frame()

        self.assertFalse(flow.is_beating())
        self.assertFalse(flow._rest_timer.isActive())

    def test_no_flow_when_animations_are_disabled(self) -> None:
        self._enabled = False
        flow = PacketFlowIndicator()
        _host(self, flow).show()

        flow.start_pulse()

        self.assertTrue(flow._is_pulsing)
        self.assertFalse(flow.is_beating())

    def test_paints_running_and_stopped_states(self) -> None:
        flow = PacketFlowIndicator()
        flow.set_color("#6ccb5f")
        for pulsing in (True, False):
            flow._is_pulsing = pulsing
            image = QPixmap(flow.size())
            image.fill(QColor(0, 0, 0, 0))
            flow.render(image)
            self.assertFalse(image.isNull())


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


class ControlTopSummaryPendingChangeTests(unittest.TestCase):
    """Пресет переключили на другой странице — изменение видно при возврате."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._patches = _animations(True, summary_module, motion_module)
        self.addCleanup(lambda: [p.stop() for p in self._patches])
        self.summary = ControlTopSummaryWidget(language="ru", mode_value="Zapret 2")
        self.host = _host(self, self.summary)
        self.host.show()
        self.summary.set_preset("Default v1")
        self.summary.set_profile_count(70)

    def test_change_while_hidden_plays_after_return(self) -> None:
        self.host.hide()
        self.summary.set_preset("general ALT3")
        self.summary.set_profile_count(None)
        self.summary.set_profile_count(64)

        self.assertTrue(self.summary.has_pending_changes())
        self.assertFalse(self.summary.preset_item.is_change_playing())

        self.host.show()
        self.assertTrue(self.summary._pending_timer.isActive())
        self.summary._pending_timer.stop()
        self.summary._play_pending_changes()

        self.assertFalse(self.summary.has_pending_changes())
        self.assertTrue(self.summary.preset_item.is_change_playing())
        self.assertTrue(self.summary.profiles_item.is_change_playing())
        roll = self.summary._profile_roll
        # Счёт идёт от числа, которое было до переключения, а не от «Проверяем...».
        self.assertEqual(roll.startValue(), 70.0)
        self.assertEqual(roll.endValue(), 64.0)

    def test_visible_change_plays_immediately_and_cleans_up(self) -> None:
        self.summary.set_preset("general ALT3")

        item = self.summary.preset_item
        self.assertTrue(item.is_change_playing())
        self.assertIsNotNone(item._value_label.graphicsEffect())

        pop = item._change_pop
        pop.setCurrentTime(pop.duration())
        self.assertFalse(item.is_change_playing())
        self.assertIsNone(item._value_label.graphicsEffect())

    def test_nothing_is_remembered_when_live_animations_are_off(self) -> None:
        for patcher in self._patches:
            patcher.stop()
        self._patches = _animations(False, summary_module, motion_module)
        self.host.hide()
        self.summary.set_preset("general ALT3")
        self.summary.set_profile_count(64)

        self.assertFalse(self.summary.has_pending_changes())


class LiveAnimationsSettingTests(unittest.TestCase):
    def test_live_animations_are_on_by_default_and_separate_from_winui(self) -> None:
        from settings import schema
        from settings.normalize import normalize_settings

        defaults = schema.default_appearance()
        self.assertTrue(defaults["live_animations_enabled"])
        self.assertFalse(defaults["animations_enabled"])
        normalized = normalize_settings({"appearance": {}})
        self.assertTrue(normalized["appearance"]["live_animations_enabled"])
        normalized = normalize_settings({"appearance": {"live_animations_enabled": False}})
        self.assertFalse(normalized["appearance"]["live_animations_enabled"])

    def test_policy_reads_warmed_value_and_defaults_to_on(self) -> None:
        from settings import appearance
        from ui.animation_policy import are_live_animations_enabled

        self.addCleanup(appearance.clear_warmed_live_animations_enabled_cache)
        appearance.clear_warmed_live_animations_enabled_cache()
        self.assertTrue(are_live_animations_enabled())
        appearance.store_warmed_live_animations_enabled(False)
        self.assertFalse(are_live_animations_enabled())


class SoftVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _make(self, enabled: bool = True):
        patches = _animations(enabled, soft_module)
        self.addCleanup(lambda: [p.stop() for p in patches])
        button = QWidget()
        host = _host(self, button)
        host.show()
        return button

    def test_hide_fades_out_before_hiding(self) -> None:
        button = self._make()

        self.assertTrue(set_visible_softly(button, False))

        # Пока затухает, кнопка ещё на экране, но цель уже «скрыта».
        self.assertFalse(button.isHidden())
        self.assertFalse(soft_visibility_target(button))
        self.assertFalse(set_visible_softly(button, False))

        anim = button._zapret_soft_visibility.anim
        anim.setCurrentTime(anim.duration())
        self.assertTrue(button.isHidden())
        self.assertIsNone(button.graphicsEffect())

    def test_show_waits_for_neighbour_then_fades_in(self) -> None:
        button = self._make()
        button.hide()

        self.assertTrue(set_visible_softly(button, True))
        state = button._zapret_soft_visibility
        self.assertTrue(button.isHidden())
        self.assertTrue(state.delay.isActive())

        state.delay.stop()
        soft_module._begin_fade_in(button)
        self.assertFalse(button.isHidden())
        self.assertIsNotNone(button.graphicsEffect())
        state.anim.setCurrentTime(state.anim.duration())
        self.assertIsNone(button.graphicsEffect())

    def test_switching_back_during_fade_out_keeps_widget(self) -> None:
        button = self._make()
        set_visible_softly(button, False)
        set_visible_softly(button, True)

        anim = button._zapret_soft_visibility.anim
        anim.setCurrentTime(anim.duration())
        self.assertFalse(button.isHidden())

    def test_immediate_when_live_animations_are_off(self) -> None:
        button = self._make(enabled=False)

        set_visible_softly(button, False)

        self.assertTrue(button.isHidden())
        self.assertIsNone(button.graphicsEffect())


if __name__ == "__main__":
    unittest.main()
