from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from qfluentwidgets import SubtitleLabel, Theme, isDarkTheme, setTheme

from donater.ui.page import PremiumPage


# Тёмные оттенки для светлой темы и яркие — для тёмной.
_EXPECTED_COLORS = {
    "normal": ("#0f7b0f", "#6ccb5f"),
    "warning": ("#9d5d00", "#ff9800"),
    "urgent": ("#c42b1c", "#ff5252"),
}


def _effective_color(color: str) -> str:
    # Fluent-надпись пишет в свой стиль цвет текущей темы в формате #AARRGGBB.
    return f"color:#ff{color.lstrip('#')}"


class PremiumDaysLabelThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _make_fake_page(self) -> SimpleNamespace:
        page = SimpleNamespace(
            days_label=SubtitleLabel(""),
            _tr=lambda _key, default, **kwargs: default.format(**kwargs),
            _days_state_kind="none",
            _days_state_value=0,
        )
        self.addCleanup(page.days_label.deleteLater)
        return page

    def _make_real_page(self) -> PremiumPage:
        page = PremiumPage(deps=SimpleNamespace(premium_feature=None, subscription_state_store=None))
        self.addCleanup(page.deleteLater)
        return page

    def test_each_days_state_sets_readable_colors_for_both_themes(self) -> None:
        page = self._make_fake_page()

        for kind, (light, dark) in _EXPECTED_COLORS.items():
            with self.subTest(kind=kind):
                page._days_state_kind = kind
                page._days_state_value = 5
                PremiumPage._render_days_label(page)

                self.assertEqual(page.days_label.lightColor.name(), light)
                self.assertEqual(page.days_label.darkColor.name(), dark)

    def test_inactive_state_returns_default_label_colors(self) -> None:
        page = self._make_fake_page()
        page._days_state_kind = "urgent"
        page._days_state_value = 2
        PremiumPage._render_days_label(page)

        page._days_state_kind = "none"
        page._days_state_value = 0
        PremiumPage._render_days_label(page)

        self.assertEqual(page.days_label.text(), "")
        self.assertEqual(page.days_label.lightColor.name(), "#000000")
        self.assertEqual(page.days_label.darkColor.name(), "#ffffff")

    def test_global_theme_change_recolors_days_label_and_keeps_texts(self) -> None:
        original_theme = Theme.DARK if isDarkTheme() else Theme.LIGHT
        self.addCleanup(setTheme, original_theme)
        light, dark = _EXPECTED_COLORS["warning"]

        setTheme(Theme.DARK)
        page = self._make_real_page()
        page._days_state_kind = "warning"
        page._days_state_value = 6
        page._render_days_label()
        page.show()
        self._app.processEvents()
        self.assertIn(_effective_color(dark), page.days_label.styleSheet())

        setTheme(Theme.LIGHT)
        self._app.processEvents()

        style = page.days_label.styleSheet()
        self.assertIn(_effective_color(light), style)
        self.assertNotIn(_effective_color(dark), style)
        self.assertEqual(page.days_label.text(), "⚠️ Осталось дней: 6")
        self.assertEqual(page.days_label.accessibleName(), "Осталось дней Premium: 6")
        self.assertEqual(page.days_label.property("screenReaderStateText"), "Осталось дней Premium: 6")

        setTheme(Theme.DARK)
        self._app.processEvents()

        self.assertIn(_effective_color(dark), page.days_label.styleSheet())


if __name__ == "__main__":
    unittest.main()
