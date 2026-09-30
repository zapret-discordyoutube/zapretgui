from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.state_store import MainWindowStateStore  # noqa: E402
from ui.fluent_app_window import ZapretFluentWindow  # noqa: E402
from ui.launch_title_badge import LaunchTitleBadge, build_launch_badge_view  # noqa: E402
from ui.navigation.search import attach_sidebar_search_to_titlebar, update_titlebar_search_width  # noqa: E402
from ui.window_state_binder import bind_launch_title_badge, bind_window_ui_state  # noqa: E402
from ui.subscription_title_badge import SubscriptionTitleBadge  # noqa: E402
from ui.window_ui_facade import _SidebarSearchNavWidget  # noqa: E402


class LaunchBadgeViewTests(unittest.TestCase):
    def test_texts_follow_phase(self) -> None:
        cases = {
            "running": ("Работает", "Zapret 2 работает · нажмите, чтобы остановить"),
            "starting": ("Запуск…", "Zapret 2 запускается · нажмите, чтобы остановить"),
            "autostart_pending": ("Запуск…", "Zapret 2 запускается · нажмите, чтобы остановить"),
            "stopping": ("Остановка…", "Zapret 2 останавливается…"),
            "stopped": ("Остановлен", "Zapret 2 остановлен · нажмите, чтобы запустить"),
            "failed": ("Ошибка", "Ошибка запуска Zapret 2 · нажмите, чтобы попробовать снова"),
        }
        for phase, (text, tooltip) in cases.items():
            with self.subTest(phase=phase):
                view = build_launch_badge_view(phase=phase, launch_method="zapret2_mode", language="ru")
                self.assertEqual((view.text, view.tooltip), (text, tooltip))

    def test_english_texts_and_mode_names(self) -> None:
        view = build_launch_badge_view(phase="running", launch_method="zapret1_mode", language="en")

        self.assertEqual(view.text, "Running")
        self.assertEqual(view.tooltip, "Zapret 1 is running · click to stop")


class LaunchTitleBadgeBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _make_window(self) -> ZapretFluentWindow:
        window = ZapretFluentWindow()
        window.ui_session = SimpleNamespace(
            sidebar_search_nav_widget=_SidebarSearchNavWidget(),
            sidebar_search_titlebar_attached=False,
            ui_language="ru",
        )
        window.resize(1400, 900)
        window.show()
        self._app.processEvents()
        attach_sidebar_search_to_titlebar(window)
        update_titlebar_search_width(window)
        self.addCleanup(window.deleteLater)
        return window

    @staticmethod
    def _set_phase(store: MainWindowStateStore, phase: str) -> None:
        store.update(launch_phase=phase, launch_running=phase == "running", launch_method="zapret2_mode")

    def test_badge_follows_launch_phase(self) -> None:
        window = self._make_window()
        store = MainWindowStateStore()
        badge = bind_launch_title_badge(window, store, Mock())

        self.assertFalse(badge.isHidden())
        self.assertEqual(badge.text(), "Остановлен")

        self._set_phase(store, "starting")
        self.assertEqual(badge.text(), "Запуск…")
        self._set_phase(store, "running")
        self.assertEqual(badge.text(), "Работает")
        self.assertIn("остановить", badge.toolTip())

    def test_click_toggles_through_launch_control(self) -> None:
        window = self._make_window()
        store = MainWindowStateStore()
        launch_control = Mock()
        badge = bind_launch_title_badge(window, store, launch_control)

        badge.click()

        launch_control.toggle.assert_called_once_with()

    def test_badge_is_disabled_while_stopping(self) -> None:
        window = self._make_window()
        store = MainWindowStateStore()
        badge = bind_launch_title_badge(window, store, Mock())

        self._set_phase(store, "stopping")
        self.assertFalse(badge.isEnabled())
        self._set_phase(store, "stopped")
        self.assertTrue(badge.isEnabled())

    def test_badge_sits_after_subscription_badge(self) -> None:
        window = self._make_window()
        store = MainWindowStateStore()

        bind_window_ui_state(window, store, launch_control=Mock())

        layout = window.titleBar.hBoxLayout
        subscription = window.titleBar.findChild(SubscriptionTitleBadge)
        launch = window.titleBar.findChild(LaunchTitleBadge)
        self.assertEqual(layout.indexOf(launch), layout.indexOf(subscription) + 1)

    def test_no_badge_without_launch_control(self) -> None:
        window = self._make_window()

        self.assertIsNone(bind_launch_title_badge(window, MainWindowStateStore(), None))
        self.assertIsNone(window.titleBar.findChild(LaunchTitleBadge))

    def test_binding_twice_reuses_single_badge(self) -> None:
        window = self._make_window()
        store = MainWindowStateStore()

        first = bind_launch_title_badge(window, store, Mock())
        second = bind_launch_title_badge(window, store, Mock())

        self.assertIs(first, second)
        self.assertEqual(len(window.titleBar.findChildren(LaunchTitleBadge)), 1)


if __name__ == "__main__":
    unittest.main()
