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


if __name__ == "__main__":
    unittest.main()
