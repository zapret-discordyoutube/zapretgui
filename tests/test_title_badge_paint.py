"""Значки в заголовке: фон рисуется со сглаженными углами, не таблицей стилей."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication


class TitleBadgePaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _badges(self):
        from donater.premium_display import PREMIUM_TIERS, PremiumDisplay
        from ui.launch_title_badge import LaunchTitleBadge
        from ui.subscription_title_badge import SubscriptionTitleBadge

        premium = SubscriptionTitleBadge(language_provider=lambda: "ru")
        premium.set_display(PremiumDisplay(tier=next(iter(PREMIUM_TIERS)), days=37))
        launch = LaunchTitleBadge(language_provider=lambda: "ru")
        launch.set_state(phase="running", launch_method="direct_zapret2")
        for badge in (premium, launch):
            self.addCleanup(badge.deleteLater)
            badge.resize(120, 22)
        return premium, launch

    def test_style_sheet_leaves_background_to_the_painter(self) -> None:
        from ui import launch_title_badge, subscription_title_badge

        styles = []
        for theme in ("light", "dark"):
            styles.append(subscription_title_badge._badge_qss(is_premium=True, theme_name=theme))
            styles.append(subscription_title_badge._badge_qss(is_premium=False, theme_name=theme))
            for phase in ("running", "starting", "failed", ""):
                styles.append(launch_title_badge._badge_qss(phase=phase, theme_name=theme))
        for style in styles:
            with self.subTest(style=style[:40]):
                self.assertNotIn("border-radius", style)
                self.assertNotRegex(style, r"background:(?!\s*transparent)")
                self.assertIn("background: transparent", style)

    def test_corners_are_round_and_body_is_filled(self) -> None:
        for badge in self._badges():
            with self.subTest(badge=type(badge).__name__):
                image = badge.grab().toImage()
                corner = QColor(image.pixelColor(0, 0))
                middle = QColor(image.pixelColor(image.width() - 4, image.height() // 2))
                self.assertLess(corner.alpha(), 40)
                self.assertGreater(middle.alpha(), 0)


class RunningBadgePulseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _badge(self, *, animations: bool):
        from unittest import mock

        import ui.launch_title_badge as badge_module

        patcher = mock.patch.object(badge_module, "are_live_animations_enabled", return_value=animations)
        patcher.start()
        self.addCleanup(patcher.stop)
        badge = badge_module.LaunchTitleBadge(language_provider=lambda: "ru")
        self.addCleanup(badge.deleteLater)
        return badge

    def test_running_badge_pulses_while_visible(self) -> None:
        badge = self._badge(animations=True)
        badge.set_state(phase="running", launch_method="direct_zapret2")
        QApplication.processEvents()

        self.assertTrue(badge.is_pulsing())
        badge.hide()
        self.assertFalse(badge.is_pulsing())

    def test_pulse_stops_when_zapret_stops(self) -> None:
        badge = self._badge(animations=True)
        badge.set_state(phase="running", launch_method="direct_zapret2")
        badge.set_state(phase="stopped", launch_method="direct_zapret2")

        self.assertFalse(badge.is_pulsing())

    def test_no_pulse_when_live_animations_are_off(self) -> None:
        badge = self._badge(animations=False)
        badge.set_state(phase="running", launch_method="direct_zapret2")

        self.assertFalse(badge.is_pulsing())


class LaunchBadgeReadyRingTests(unittest.TestCase):
    """Кольцо с точкой берётся готовой картинкой для каждого шага пульса."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _pulsing_badge(self):
        from unittest import mock

        import ui.launch_title_badge as badge_module

        patcher = mock.patch.object(badge_module, "are_live_animations_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        badge = badge_module.LaunchTitleBadge(language_provider=lambda: "ru")
        self.addCleanup(badge.deleteLater)
        badge.set_state(phase="running", launch_method="direct_zapret2")
        self.assertTrue(badge.is_pulsing())
        return badge, badge_module

    def test_pulse_moves_in_steps_of_one_screen_frame(self) -> None:
        from unittest import mock

        badge, badge_module = self._pulsing_badge()
        steps = badge_module.PULSE_STEPS
        self.assertEqual(steps, 108)  # 1,8 с по 1/60 с
        for elapsed in (0.0, 5.0, 16.0, 17.0, 40.0, 900.0, 1799.0, 1801.0, 3 * 1800.0 + 250.0):
            with mock.patch.object(badge._pulse, "elapsed_ms", return_value=elapsed):
                badge._on_pulse_frame()
            exact = (elapsed % badge_module.RUNNING_PULSE_MS) / badge_module.RUNNING_PULSE_MS
            self.assertAlmostEqual(badge._pulse_t * steps, round(badge._pulse_t * steps), places=6)
            # Отстаёт от точной фазы меньше чем на один кадр экрана.
            self.assertLessEqual(badge._pulse_t, exact + 1e-9)
            self.assertLess(exact - badge._pulse_t, 1.0 / steps)

    def test_ring_of_a_pulse_step_is_painted_once(self) -> None:
        from unittest import mock

        from test_paint_layer_cache import render

        badge, badge_module = self._pulsing_badge()
        painted: list[float] = []
        original = badge_module.LaunchTitleBadge._paint_dot

        def counting(painter, shape, center, color, *, ring_t, halo):
            painted.append(ring_t)
            original(painter, shape, center, color, ring_t=ring_t, halo=halo)

        with mock.patch.object(badge_module.LaunchTitleBadge, "_paint_dot", staticmethod(counting)):
            for _circle in range(3):
                for step in (0, 20, 40, 107):
                    badge._pulse_t = step / badge_module.PULSE_STEPS
                    render(badge, 1.0)
        self.assertEqual(len(painted), 4)
        self.assertEqual(len(badge._layers), 4)

    def test_ready_ring_looks_like_direct_painting(self) -> None:
        from unittest import mock

        from test_paint_layer_cache import ROUNDING, difference, paint_directly, render
        from ui.paint_layer_cache import LayerCache

        badge, badge_module = self._pulsing_badge()
        for scale in (1.0, 1.25, 1.5, 2.0):
            for step in (0, 13, 54, 90, 107):
                with self.subTest(scale=scale, step=step):
                    badge._pulse_t = step / badge_module.PULSE_STEPS
                    with mock.patch.object(LayerCache, "draw", paint_directly):
                        expected = render(badge, scale)
                    self.assertLessEqual(difference(expected, render(badge, scale)), ROUNDING)
                    self.assertLessEqual(difference(expected, render(badge, scale)), ROUNDING)

    def test_busy_and_stopped_badges_paint_the_dot_directly(self) -> None:
        from test_paint_layer_cache import render

        badge, _badge_module = self._pulsing_badge()
        for phase in ("starting", "stopped", "failed"):
            badge.set_state(phase=phase, launch_method="direct_zapret2")
            self.assertFalse(badge.is_pulsing())
            render(badge, 1.0)
        self.assertEqual(len(badge._layers), 0)


if __name__ == "__main__":
    unittest.main()
