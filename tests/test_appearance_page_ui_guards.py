from __future__ import annotations

import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.state_store import AppUiState


class AppearancePageUiGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_opacity_state_change_skips_premium_repaint(self) -> None:
        from ui.pages.appearance_page import AppearancePage

        page = AppearancePage.__new__(AppearancePage)
        page._cleanup_in_progress = False
        page.set_opacity_value = Mock()
        page._apply_premium_access = Mock(
            side_effect=AssertionError("opacity-only change must not repaint premium controls")
        )

        AppearancePage._on_ui_state_changed(
            page,
            AppUiState(subscription_known=True, subscription_is_premium=True, window_opacity=72),
            frozenset({"window_opacity"}),
        )

        page.set_opacity_value.assert_called_once_with(72)
        page._apply_premium_access.assert_not_called()

    def test_subscription_change_repaints_premium_controls_from_store_state(self) -> None:
        from ui.pages.appearance_page import AppearancePage

        page = AppearancePage.__new__(AppearancePage)
        page._cleanup_in_progress = False
        page.set_opacity_value = Mock()
        page._apply_premium_access = Mock()

        for known in (False, True):
            page._apply_premium_access.reset_mock()
            state = AppUiState(subscription_known=known, subscription_is_premium=False)
            AppearancePage._on_ui_state_changed(page, state, frozenset({"subscription_known"}))
            page._apply_premium_access.assert_called_once_with(state)


class AccentRestyleGuardTests(unittest.TestCase):
    """Тот же акцент не должен перекрашивать всю программу заново."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from PyQt6.QtGui import QColor
        from qfluentwidgets.common.config import qconfig

        previous = QColor(qconfig.get(qconfig.themeColor))
        self.addCleanup(lambda: qconfig.set(qconfig.themeColor, previous, save=False))
        qconfig.set(qconfig.themeColor, QColor("#112233"), save=False)

    def test_same_accent_does_not_restyle_every_widget(self) -> None:
        from PyQt6.QtGui import QColor
        from unittest.mock import patch

        from ui.pages import appearance_page

        # qfluentwidgets.setThemeColor проходит по всем своим виджетам в
        # программе, даже если цвет не изменился: сотни миллисекунд.
        with patch.object(appearance_page, "setThemeColor") as set_theme_color:
            changed = appearance_page.set_theme_color_if_changed(QColor("#112233"))

        self.assertFalse(changed)
        set_theme_color.assert_not_called()

    def test_new_accent_is_applied(self) -> None:
        from PyQt6.QtGui import QColor
        from unittest.mock import patch

        from ui.pages import appearance_page

        with patch.object(appearance_page, "setThemeColor") as set_theme_color:
            changed = appearance_page.set_theme_color_if_changed(QColor("#445566"))

        self.assertTrue(changed)
        set_theme_color.assert_called_once()
        self.assertEqual(set_theme_color.call_args.args[0].name(), "#445566")

    def test_page_build_with_saved_accent_does_not_restyle(self) -> None:
        from unittest.mock import patch

        from ui.pages import appearance_page
        from ui.pages.appearance_page import AppearancePage

        button = Mock()
        page = AppearancePage.__new__(AppearancePage)
        page._color_picker_btn = button
        page._follow_windows_accent_cb = None
        page._tinted_bg_cb = None
        page._tinted_intensity_slider = None
        page._tinted_intensity_value_label = None
        page._tinted_intensity_container = None
        page._begin_ui_sync = lambda: None
        page._end_ui_sync = lambda: None
        page._update_accent_color_button_accessibility = lambda *_a, **_k: None
        plan = Mock(accent_color="#112233", follow_windows_accent=False, tinted_background=False, tinted_intensity=15)

        with patch.object(appearance_page, "setThemeColor") as set_theme_color:
            try:
                AppearancePage._apply_initial_accent_state(page, plan)
            except Exception:
                # Остальная часть метода настраивает виджеты, которых в этом
                # тесте нет; проверяется только обращение к акценту.
                pass

        button.setColor.assert_called_once()
        set_theme_color.assert_not_called()


if __name__ == "__main__":
    unittest.main()
