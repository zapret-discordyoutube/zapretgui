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
from profile.ui import profile_strategy_list_widget as list_module
from profile.ui.profile_strategy_list_widget import ProfileStrategyListWidget
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


class StrategyListMenuTests(_MenuCase):
    def _widget(self, *, states: dict | None = None, entries: dict | None = None) -> ProfileStrategyListWidget:
        widget = ProfileStrategyListWidget()
        self.addCleanup(widget.deleteLater)
        # Без фонового фильтра список собирается сразу, в этом же вызове.
        widget._strategy_filter_runtime = None
        widget.resize(1000, 700)
        widget.set_rows(entries=entries or _entries(), states=states or {}, current_strategy_id="fake-0")
        widget.show()
        self._app.processEvents()
        return widget

    def _right_click(self, widget, strategy_id: str) -> None:
        view = widget._list
        pos = view.visualItemRect(widget._item_by_strategy_id[strategy_id]).center()
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, view.viewport().mapToGlobal(pos))
        view.contextMenuEvent(event)

    def test_right_click_opens_the_menu_of_the_clicked_strategy(self) -> None:
        widget = self._widget(states={"fake-2": ProfileStrategyState(rating="work", favorite=True)})
        activated = []
        widget.strategy_activated.connect(activated.append)

        with patch.object(list_module, "show_strategy_context_menu", return_value=("", None)) as show:
            self._right_click(widget, "fake-2")

        kwargs = show.call_args.kwargs
        self.assertEqual(kwargs["strategy_name"], "General ALT2")
        self.assertEqual(kwargs["rating"], "work")
        self.assertTrue(kwargs["favorite"])
        # Меню закрыли без выбора: ничего не меняется, стратегия не выбрана.
        self.assertEqual(activated, [])

    def test_chosen_rating_and_favorite_are_reported_with_the_strategy(self) -> None:
        widget = self._widget()
        ratings, favorites = [], []
        widget.strategy_rating_requested.connect(lambda *args: ratings.append(args))
        widget.strategy_favorite_requested.connect(lambda *args: favorites.append(args))

        with patch.object(list_module, "show_strategy_context_menu", return_value=(COMMAND_RATING, "notwork")):
            self._right_click(widget, "fake-3")
        with patch.object(list_module, "show_strategy_context_menu", return_value=(COMMAND_FAVORITE, True)):
            self._right_click(widget, "fake-1")

        self.assertEqual(ratings, [("fake-3", "notwork")])
        self.assertEqual(favorites, [("fake-1", True)])

    def test_group_header_has_no_menu(self) -> None:
        # Длинный список делится на группы с заголовками.
        entries = _entries(20)
        entries.update(
            {f"split-{n}": SimpleNamespace(name=f"MultiSplit {n}", args="--lua-desync=multisplit") for n in range(20)}
        )
        widget = self._widget(entries=entries)
        view = widget._list
        header = next(view.item(row) for row in range(view.count()) if list_module._is_group_item(view.item(row)))
        pos = view.visualItemRect(header).center()

        with patch.object(list_module, "show_strategy_context_menu") as show:
            view.contextMenuEvent(
                QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, view.viewport().mapToGlobal(pos))
            )

        show.assert_not_called()

    def test_menu_key_opens_the_menu_of_the_current_row(self) -> None:
        widget = self._widget()
        view = widget._list
        view.setCurrentItem(widget._item_by_strategy_id["fake-1"])
        ratings = []
        widget.strategy_rating_requested.connect(lambda *args: ratings.append(args))

        with patch.object(list_module, "show_strategy_context_menu", return_value=(COMMAND_RATING, "work")) as show:
            view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(0, 0), QPoint(0, 0)))

        # Меню встаёт у самой строки, а не там, где оказался курсор мыши.
        expected = view.viewport().mapToGlobal(view.visualItemRect(view.currentItem()).center())
        self.assertEqual(show.call_args.kwargs["global_pos"], expected)
        self.assertEqual(ratings, [("fake-1", "work")])


if __name__ == "__main__":
    unittest.main()
