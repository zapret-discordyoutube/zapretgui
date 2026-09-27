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


if __name__ == "__main__":
    unittest.main()
