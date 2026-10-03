from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QHBoxLayout, QWidget

import ui.subscription_title_badge as badge_module
from donater.premium_display import TIER_ACTIVE, TIER_FREE, PremiumDisplay
from ui.subscription_title_badge import SubscriptionTitleBadge


class SubscriptionBadgeShineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        patcher = mock.patch.object(badge_module, "are_live_animations_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.host = QWidget()
        self.addCleanup(self.host.deleteLater)
        self.badge = SubscriptionTitleBadge(self.host, language_provider=lambda: "ru")
        QHBoxLayout(self.host).addWidget(self.badge)
        self.host.show()

    def test_premium_badge_schedules_shine_and_repeats(self) -> None:
        self.badge.set_display(PremiumDisplay(tier=TIER_ACTIVE, days=37))
        QApplication.processEvents()
        self.assertTrue(self.badge._shine_timer.isActive())

        self.badge.play_shine()
        self.assertTrue(self.badge.is_shining())
        # Кадр на общем такте после конца вспышки завершает её.
        with mock.patch.object(
            self.badge._shine, "elapsed_ms", return_value=badge_module.PREMIUM_SHINE_DURATION_MS
        ):
            self.badge._on_shine_frame()
        self.assertFalse(self.badge.is_shining())
        self.assertEqual(self.badge._shine_t, 0.0)
        # После вспышки ждёт следующую только одиночный таймер.
        self.assertTrue(self.badge._shine_timer.isActive())
        self.assertEqual(self.badge._shine_timer.interval(), badge_module.PREMIUM_SHINE_INTERVAL_MS)

    def test_free_badge_never_shines(self) -> None:
        self.badge.set_display(PremiumDisplay(tier=TIER_FREE))
        QApplication.processEvents()
        self.assertFalse(self.badge._shine_timer.isActive())
        self.badge.play_shine()
        self.assertFalse(self.badge.is_shining())

    def test_hidden_badge_stops_shining(self) -> None:
        self.badge.set_display(PremiumDisplay(tier=TIER_ACTIVE, days=37))
        QApplication.processEvents()
        self.badge.play_shine()

        self.host.hide()

        self.assertFalse(self.badge.is_shining())
        self.assertFalse(self.badge._shine_timer.isActive())

    def test_no_shine_when_live_animations_are_off(self) -> None:
        with mock.patch.object(badge_module, "are_live_animations_enabled", return_value=False):
            self.badge.set_display(PremiumDisplay(tier=TIER_ACTIVE, days=37))
            QApplication.processEvents()
            self.badge.play_shine()
        self.assertFalse(self.badge.is_shining())
        self.assertFalse(self.badge._shine_timer.isActive())


if __name__ == "__main__":
    unittest.main()
