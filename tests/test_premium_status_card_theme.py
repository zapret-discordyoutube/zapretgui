from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from qfluentwidgets import Theme, isDarkTheme, setTheme

from donater.ui.status_card import StatusCard
from ui import theme_refresh
from ui.theme import get_theme_tokens
from ui.theme_refresh import ThemeRefreshBinding
from ui.theme_semantic import get_semantic_palette


def _expected_colors(theme_name: str) -> dict[str, tuple[str, str]]:
    tokens = get_theme_tokens(theme_name)
    palette = get_semantic_palette(theme_name)
    light = theme_name == "light"
    return {
        "active": ("#0f7b0f" if light else "#6ccb5f", palette.success_soft_bg),
        "warning": ("#9d5d00" if light else "#ff9800", palette.warning_soft_bg),
        "expired": ("#c42b1c" if light else "#ff5252", palette.error_soft_bg),
        "neutral": (tokens.accent_hex, tokens.accent_soft_bg),
    }


class SemanticPaletteStatusTextTests(unittest.TestCase):
    def test_light_theme_uses_dark_readable_status_text(self) -> None:
        palette = get_semantic_palette("light")

        self.assertEqual(palette.success_text, "#0f7b0f")
        self.assertEqual(palette.warning_text, "#9d5d00")
        self.assertEqual(palette.error_text, "#c42b1c")

    def test_dark_theme_status_text_matches_bright_semantic_colors(self) -> None:
        palette = get_semantic_palette("dark")

        self.assertEqual(palette.success_text, palette.success)
        self.assertEqual(palette.warning_text, palette.warning)
        self.assertEqual(palette.error_text, palette.error)


class PremiumStatusCardThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _make_card(self) -> StatusCard:
        card = StatusCard()
        self.addCleanup(card.deleteLater)
        return card

    def _assert_card_colors(self, card: StatusCard, status: str, theme_name: str) -> None:
        fg, bg = _expected_colors(theme_name)[status]
        tokens = get_theme_tokens(theme_name)

        self.assertIn(f"color: {fg};", card._icon_lbl.styleSheet())
        self.assertIn(f"color: {fg};", card._title_lbl.styleSheet())
        self.assertIn(f"background-color: {bg};", card.styleSheet())
        self.assertIn(f"color: {tokens.fg_muted};", card._detail_lbl.styleSheet())

    def test_each_status_uses_theme_colors_in_light_and_dark(self) -> None:
        card = self._make_card()

        for theme_name in ("light", "dark"):
            for status in ("active", "warning", "expired", "neutral"):
                with self.subTest(theme=theme_name, status=status):
                    card.set_status("Заголовок", "Подробности", status)
                    card._apply_theme_refresh(tokens=get_theme_tokens(theme_name))

                    self._assert_card_colors(card, status, theme_name)

    def test_light_theme_has_no_dark_only_colors(self) -> None:
        card = self._make_card()
        card.set_status("Premium активен", "Осталось 7 дней", "active")

        card._apply_theme_refresh(tokens=get_theme_tokens("light"))

        self.assertNotIn("255, 255, 255", card._detail_lbl.styleSheet())
        self.assertNotIn("255,255,255", card._detail_lbl.styleSheet())
        for old_bg in ("#1c2e24", "#2a2516", "#2a1e1e", "#1a2030"):
            self.assertNotIn(old_bg, card.styleSheet())

    def test_theme_switch_recolors_without_losing_status_and_texts(self) -> None:
        card = self._make_card()
        card.set_status("Premium активен", "Осталось 7 дней", "warning")
        card._apply_theme_refresh(tokens=get_theme_tokens("dark"))
        self._assert_card_colors(card, "warning", "dark")

        card._apply_theme_refresh(tokens=get_theme_tokens("light"))

        self._assert_card_colors(card, "warning", "light")
        self.assertEqual(card._icon_lbl.text(), "⚠")
        self.assertEqual(card._title_lbl.text(), "Premium активен")
        self.assertEqual(card._detail_lbl.text(), "Осталось 7 дней")
        self.assertEqual(card.accessibleName(), "Статус Premium: Premium активен. Осталось 7 дней")
        self.assertEqual(card._detail_lbl.accessibleName(), "Детали Premium: Осталось 7 дней")

    def test_unknown_status_falls_back_to_neutral(self) -> None:
        card = self._make_card()

        card.set_status("Проверка", "", "something-new")
        card._apply_theme_refresh(tokens=get_theme_tokens("light"))

        self.assertEqual(card._icon_lbl.text(), "ℹ")
        self._assert_card_colors(card, "neutral", "light")

    def test_card_owns_theme_refresh_binding(self) -> None:
        card = self._make_card()

        bindings = card.findChildren(ThemeRefreshBinding)

        self.assertEqual(len(bindings), 1)
        self.assertIs(card._theme_refresh, bindings[0])

    def test_global_theme_change_recolors_visible_card(self) -> None:
        original_theme = Theme.DARK if isDarkTheme() else Theme.LIGHT
        self.addCleanup(setTheme, original_theme)

        with patch.object(
            theme_refresh.QTimer,
            "singleShot",
            side_effect=lambda _delay_ms, callback: callback(),
        ):
            setTheme(Theme.DARK)
            card = self._make_card()
            card.set_status("Premium истёк", "Продлите подписку", "expired")
            card.show()
            self._app.processEvents()
            self._assert_card_colors(card, "expired", "dark")

            setTheme(Theme.LIGHT)
            self._app.processEvents()

        self._assert_card_colors(card, "expired", "light")
        self.assertEqual(card.accessibleName(), "Статус Premium: Premium истёк. Продлите подписку")


if __name__ == "__main__":
    unittest.main()
