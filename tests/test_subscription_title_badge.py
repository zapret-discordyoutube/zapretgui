from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.page_names import PageName  # noqa: E402
from app.state_store import MainWindowStateStore  # noqa: E402
from donater.premium_display import TIER_FREE, TIER_UNKNOWN, PremiumDisplay, build_premium_display  # noqa: E402
from ui import window_state_binder  # noqa: E402
from ui.fluent_app_window import ZapretFluentWindow  # noqa: E402
from ui.navigation.search import attach_sidebar_search_to_titlebar, update_titlebar_search_width  # noqa: E402
from ui.subscription_title_badge import SubscriptionTitleBadge, build_title_badge_texts  # noqa: E402
from ui.window_state_binder import (  # noqa: E402
    bind_subscription_title_badge,
    retranslate_subscription_title_badge,
)
from ui.window_ui_facade import _SidebarSearchNavWidget  # noqa: E402


class TitleBadgeTextTests(unittest.TestCase):
    def test_unknown_status_has_no_text(self) -> None:
        self.assertEqual(build_title_badge_texts(PremiumDisplay(TIER_UNKNOWN), language="ru"), ("", ""))

    def test_free_badge_invites_to_premium(self) -> None:
        text, tooltip = build_title_badge_texts(PremiumDisplay(TIER_FREE), language="ru")

        self.assertEqual(text, "FREE")
        self.assertIn("Premium", tooltip)

    def test_premium_badge_texts(self) -> None:
        unknown_days = build_premium_display(is_premium=True, days_remaining=None)
        with_days = build_premium_display(is_premium=True, days_remaining=21)

        self.assertEqual(build_title_badge_texts(unknown_days, language="ru")[0], "PREMIUM")
        self.assertEqual(
            build_title_badge_texts(with_days, language="ru"),
            ("PREMIUM · 21 дн.", "Premium активен. Осталось 21 день. Нажмите, чтобы открыть страницу подписки"),
        )
        self.assertEqual(build_title_badge_texts(with_days, language="en")[0], "PREMIUM · 21 d")


class SubscriptionTitleBadgeBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _make_window(self, *, width: int = 1571, language: str = "ru") -> tuple[ZapretFluentWindow, object]:
        window = ZapretFluentWindow()
        search_widget = _SidebarSearchNavWidget()
        window.ui_session = SimpleNamespace(
            sidebar_search_nav_widget=search_widget,
            sidebar_search_titlebar_attached=False,
            ui_language=language,
        )
        window.resize(width, 1070)
        window.show()
        self._app.processEvents()
        attach_sidebar_search_to_titlebar(window)
        update_titlebar_search_width(window)
        self.addCleanup(window.deleteLater)
        return window, search_widget

    def _settle(self, window) -> None:
        # Отложенный пересчёт ширины поиска идёт через QTimer.singleShot(0).
        self._app.processEvents()
        window.titleBar.hBoxLayout.activate()
        self._app.processEvents()

    def test_badge_is_hidden_until_subscription_status_is_known(self) -> None:
        window, _search = self._make_window()
        store = MainWindowStateStore()

        badge = bind_subscription_title_badge(window, store)

        self.assertIsNotNone(badge)
        self.assertTrue(badge.isHidden())
        self.assertEqual(badge.text(), "")

    def test_badge_sits_right_after_window_title(self) -> None:
        window, _search = self._make_window()
        badge = bind_subscription_title_badge(window, MainWindowStateStore())
        layout = window.titleBar.hBoxLayout

        self.assertEqual(layout.indexOf(badge), layout.indexOf(window.titleBar.titleLabel) + 1)

    def test_badge_follows_store_without_restart(self) -> None:
        window, _search = self._make_window()
        store = MainWindowStateStore()
        badge = bind_subscription_title_badge(window, store)

        store.set_subscription(False)
        self.assertFalse(badge.isHidden())
        self.assertEqual(badge.text(), "FREE")

        store.set_subscription(True, 12)
        self.assertEqual(badge.text(), "PREMIUM · 12 дн.")

        store.set_subscription(True, None)
        self.assertEqual(badge.text(), "PREMIUM")

        store.set_subscription(False)
        self.assertEqual(badge.text(), "FREE")

    def test_badge_shows_state_that_was_known_before_binding(self) -> None:
        window, _search = self._make_window()
        store = MainWindowStateStore()
        store.set_subscription(True, 40)

        badge = bind_subscription_title_badge(window, store)

        self.assertFalse(badge.isHidden())
        self.assertEqual(badge.text(), "PREMIUM · 40 дн.")

    def test_binding_twice_reuses_single_badge(self) -> None:
        window, _search = self._make_window()
        store = MainWindowStateStore()

        first = bind_subscription_title_badge(window, store)
        second = bind_subscription_title_badge(window, store)

        self.assertIs(first, second)
        self.assertEqual(len(window.titleBar.findChildren(SubscriptionTitleBadge)), 1)

    def test_click_opens_premium_page(self) -> None:
        window, _search = self._make_window()
        store = MainWindowStateStore()
        badge = bind_subscription_title_badge(window, store)
        store.set_subscription(False)
        self._settle(window)

        with patch.object(window_state_binder, "show_page") as show_page:
            QTest.mouseClick(badge, Qt.MouseButton.LeftButton, pos=QPoint(badge.width() // 2, badge.height() // 2))

        show_page.assert_called_once_with(window, PageName.PREMIUM)

    def test_premium_and_free_use_different_theme_aware_styles(self) -> None:
        window, _search = self._make_window()
        store = MainWindowStateStore()
        badge = bind_subscription_title_badge(window, store)

        store.set_subscription(False)
        free_light = badge.property("lightCustomQss")
        free_dark = badge.property("darkCustomQss")
        store.set_subscription(True, 40)
        premium_light = badge.property("lightCustomQss")
        premium_dark = badge.property("darkCustomQss")

        self.assertTrue(free_light and free_dark and premium_light and premium_dark)
        self.assertNotEqual(free_light, premium_light)
        self.assertNotEqual(premium_light, premium_dark)
        self.assertIn("#b45309", premium_light)

    def test_destroyed_badge_unsubscribes_from_store(self) -> None:
        window, _search = self._make_window()
        store = MainWindowStateStore()
        baseline = len(store._subscribers)
        badge = bind_subscription_title_badge(window, store)
        self.assertEqual(len(store._subscribers), baseline + 1)

        badge.setParent(None)
        badge.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)

        self.assertEqual(len(store._subscribers), baseline)
        store.set_subscription(False)  # не должно падать на удалённом виджете

    def test_language_change_retranslates_badge(self) -> None:
        window, _search = self._make_window()
        store = MainWindowStateStore()
        badge = bind_subscription_title_badge(window, store)
        store.set_subscription(True, 5)
        self.assertEqual(badge.text(), "PREMIUM · 5 дн.")

        window.ui_session.ui_language = "en"
        retranslate_subscription_title_badge(window)

        self.assertEqual(badge.text(), "PREMIUM · 5 d")

    def test_search_stays_centered_after_badge_appears(self) -> None:
        window, search_widget = self._make_window()
        store = MainWindowStateStore()
        bind_subscription_title_badge(window, store)

        store.set_subscription(True, 30)
        self._settle(window)

        search_center = window.titleBar.x() + search_widget.x() + search_widget.width() / 2
        self.assertAlmostEqual(search_center, window.width() / 2, delta=4)

    def test_window_without_titlebar_is_ignored(self) -> None:
        self.assertIsNone(bind_subscription_title_badge(SimpleNamespace(), MainWindowStateStore()))


if __name__ == "__main__":
    unittest.main()
