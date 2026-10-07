"""Меню стратегии по правой кнопке: оценка и избранное.

Кнопок оценки под списком нет. Меню открывается на той стратегии, по которой
щёлкнули, и сообщает странице, что выбрано; саму стратегию для профиля щелчок
правой кнопкой не выбирает.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtWidgets import QApplication

from profile.strategy_state import ProfileStrategyState
from profile.ui.strategy_context_menu import (
    COMMAND_FAVORITE,
    COMMAND_RATING,
    build_strategy_context_menu,
    can_rate_strategy,
)


def _entries(count: int = 4) -> dict:
    return {
        f"fake-{n}": SimpleNamespace(name=f"General ALT{n}", args="--lua-desync=fake")
        for n in range(count)
    }


class _MenuCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self._app.closeAllWindows()
        self._app.processEvents()


class StrategyMenuItemsTests(_MenuCase):
    def _menu(self, *, rating: str = "", favorite: bool = False) -> dict[str, tuple[str, object]]:
        menu, action_map = build_strategy_context_menu(
            parent=None,
            strategy_name="General ALT2",
            rating=rating,
            favorite=favorite,
        )
        self.addCleanup(menu.deleteLater)
        return {action.text(): command for action, command in action_map.items()}

    def test_unrated_strategy_offers_both_ratings_and_favorite(self) -> None:
        self.assertEqual(
            self._menu(),
            {
                "Работает": (COMMAND_RATING, "work"),
                "Не работает": (COMMAND_RATING, "notwork"),
                "В избранное": (COMMAND_FAVORITE, True),
            },
        )

    def test_set_rating_can_be_taken_off_or_replaced(self) -> None:
        self.assertEqual(
            self._menu(rating="work"),
            {
                "Снять отметку «Работает»": (COMMAND_RATING, ""),
                "Не работает": (COMMAND_RATING, "notwork"),
                "В избранное": (COMMAND_FAVORITE, True),
            },
        )
        self.assertEqual(
            self._menu(rating="notwork", favorite=True),
            {
                "Работает": (COMMAND_RATING, "work"),
                "Снять отметку «Не работает»": (COMMAND_RATING, ""),
                "Убрать из избранного": (COMMAND_FAVORITE, False),
            },
        )

    def test_strategies_without_ready_arguments_are_not_rated(self) -> None:
        self.assertTrue(can_rate_strategy("fake-1"))
        for strategy_id in ("", "none", "custom"):
            self.assertFalse(can_rate_strategy(strategy_id))




if __name__ == "__main__":
    unittest.main()
