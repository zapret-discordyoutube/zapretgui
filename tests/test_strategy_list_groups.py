from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QFontMetrics, QKeyEvent
from PyQt6.QtWidgets import QApplication

from profile.strategy_state import ProfileStrategyState
from profile.ui import profile_strategy_list_widget as widget_module
from profile.ui.profile_strategy_list_widget import ProfileStrategyListWidget


W = ProfileStrategyListWidget


def _entry(name: str, *desync: str):
    return SimpleNamespace(name=name, args="\n".join(f"--lua-desync={value}" for value in desync))


def _entries(per_family: int = 12) -> dict:
    """Три способа обхода по per_family стратегий: по умолчанию 36, длинный список."""
    entries = {}
    for n in range(per_family):
        entries[f"fake-{n:02d}"] = _entry(f"Fake {n:02d}", "fake")
        entries[f"split-{n:02d}"] = _entry(f"Split {n:02d}", "multisplit")
        entries[f"host-{n:02d}"] = _entry(f"Host {n:02d}", "hostfakesplit")
    return entries


def _key(key: Qt.Key) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


class StrategyListGroupsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self._app.closeAllWindows()
        self._app.processEvents()

    def _widget(self, *, entries=None, states=None, current: str = "none") -> ProfileStrategyListWidget:
        widget = ProfileStrategyListWidget()
        self.addCleanup(widget.deleteLater)
        # Без фонового фильтра список собирается сразу, в этом же вызове.
        widget._strategy_filter_runtime = None
        widget.resize(900, 600)
        widget.set_rows(entries=_entries() if entries is None else entries, states=states or {}, current_strategy_id=current)
        return widget

    def _rows(self, widget) -> list[tuple[str, str, bool]]:
        """(вид, имя или id, скрыта?) для каждой строки списка."""
        result = []
        for row in range(widget._list.count()):
            item = widget._list.item(row)
            if item.data(W._ROLE_ROW_KIND) == "group":
                result.append(("group", str(item.data(W._ROLE_GROUP_KEY)), widget._list.isRowHidden(row)))
            else:
                result.append(("strategy", str(item.data(W._ROLE_STRATEGY_ID)), widget._list.isRowHidden(row)))
        return result

    def _visible(self, widget) -> list[str]:
        return [name for _kind, name, hidden in self._rows(widget) if not hidden]

    def _header(self, widget, key: str):
        return widget._group_header_items[key]

    # -------------------------- что раскрыто --------------------------

    def test_long_list_opens_as_a_map_of_groups(self) -> None:
        widget = self._widget()

        self.assertEqual(self._visible(widget), ["fake", "split", "host"])
        self.assertEqual(widget._list.count(), 36 + 3)
        self.assertEqual(widget._summary.text(), "36 из 36")
        header = self._header(widget, "fake")
        self.assertEqual(header.data(W._ROLE_NAME_TEXT), "Подмена пакета")
        self.assertEqual(header.data(W._ROLE_GROUP_COUNT), 12)
        self.assertFalse(header.data(W._ROLE_GROUP_EXPANDED))
        self.assertIs(widget._list.currentItem(), header)
        self.assertIn("Группа Подмена пакета, 12 стратегий, свернута", widget._list.property("screenReaderStateText"))

    def test_group_of_the_selected_strategy_is_open_and_focused(self) -> None:
        widget = self._widget(current="host-05")

        visible = self._visible(widget)
        self.assertEqual(visible[:3], ["fake", "split", "host"])
        self.assertEqual(visible[3:], [f"host-{n:02d}" for n in range(12)])
        self.assertTrue(self._header(widget, "host").data(W._ROLE_GROUP_EXPANDED))
        self.assertEqual(widget._list.currentItem().data(W._ROLE_STRATEGY_ID), "host-05")

    def test_groups_with_favorite_or_working_strategy_are_open(self) -> None:
        widget = self._widget(
            states={
                "fake-03": ProfileStrategyState(rating="", favorite=True),
                "split-07": ProfileStrategyState(rating="work", favorite=False),
                "host-01": ProfileStrategyState(rating="notwork", favorite=False),
            }
        )

        self.assertTrue(self._header(widget, "fake").data(W._ROLE_GROUP_EXPANDED))
        self.assertTrue(self._header(widget, "split").data(W._ROLE_GROUP_EXPANDED))
        self.assertFalse(self._header(widget, "host").data(W._ROLE_GROUP_EXPANDED))
        # Избранная стратегия стоит первой в своей группе.
        self.assertEqual(self._visible(widget)[1], "fake-03")

    def test_short_list_is_fully_open(self) -> None:
        widget = self._widget(entries=_entries(per_family=4))

        self.assertEqual(len(self._visible(widget)), 12 + 3)
        self.assertEqual(widget._list.currentItem().data(W._ROLE_STRATEGY_ID), "fake-00")

    def test_one_technique_means_no_headers(self) -> None:
        entries = {f"fake-{n}": _entry(f"Fake {n}", "fake") for n in range(40)}
        widget = self._widget(entries=entries)

        self.assertEqual(widget._group_header_items, {})
        self.assertEqual(widget._list.count(), 40)
        self.assertEqual(len(self._visible(widget)), 40)

    # -------------------------- сворачивание --------------------------

    def test_toggle_shows_and_hides_rows_without_rebuilding_the_list(self) -> None:
        widget = self._widget()
        items_before = [widget._list.item(row) for row in range(widget._list.count())]
        widget._rebuild_tree = Mock(side_effect=AssertionError("сворачивание не должно пересобирать список"))
        header = self._header(widget, "split")

        widget._list.group_toggle_requested.emit(header)

        self.assertEqual(self._visible(widget), ["fake", "split", *[f"split-{n:02d}" for n in range(12)], "host"])
        self.assertTrue(header.data(W._ROLE_GROUP_EXPANDED))
        self.assertIn("развернута", header.data(Qt.ItemDataRole.AccessibleTextRole))
        self.assertEqual([widget._list.item(row) for row in range(widget._list.count())], items_before)

        widget._list.group_toggle_requested.emit(header)

        self.assertEqual(self._visible(widget), ["fake", "split", "host"])
        self.assertIn("свернута", header.data(Qt.ItemDataRole.AccessibleTextRole))

    def test_collapsing_the_group_of_current_row_moves_focus_to_its_header(self) -> None:
        widget = self._widget(current="host-05")
        header = self._header(widget, "host")

        widget._toggle_group_item(header)

        self.assertIs(widget._list.currentItem(), header)
        self.assertIn("Группа По имени сайта", widget._list.property("screenReaderStateText"))

    def test_user_choice_survives_a_state_update_and_resets_for_another_catalog(self) -> None:
        entries = _entries()
        widget = self._widget(entries=entries, current="host-05")
        widget._toggle_group_item(self._header(widget, "host"))
        widget._toggle_group_item(self._header(widget, "fake"))

        # Оценка поменялась — список тот же, выбор пользователя остаётся.
        widget._search.setText("Fake 0")
        widget._search.setText("")
        self.assertTrue(self._header(widget, "fake").data(W._ROLE_GROUP_EXPANDED))
        self.assertFalse(self._header(widget, "host").data(W._ROLE_GROUP_EXPANDED))

        other = {f"x-{key}": entry for key, entry in entries.items()}
        widget.set_rows(entries=other, states={}, current_strategy_id="x-host-05")
        self.assertFalse(self._header(widget, "fake").data(W._ROLE_GROUP_EXPANDED))
        self.assertTrue(self._header(widget, "host").data(W._ROLE_GROUP_EXPANDED))

    def test_search_shows_matches_from_collapsed_groups(self) -> None:
        widget = self._widget()

        widget._search.setText("07")

        self.assertEqual(self._visible(widget), ["fake", "fake-07", "split", "split-07", "host", "host-07"])
        self.assertEqual(self._header(widget, "fake").data(W._ROLE_GROUP_COUNT), 1)
        self.assertEqual(widget._summary.text(), "3 из 36")

    # -------------------------- клавиатура и мышь --------------------------

    def test_arrows_walk_only_visible_rows(self) -> None:
        widget = self._widget()
        view = widget._list
        view.setCurrentItem(self._header(widget, "fake"))

        view.keyPressEvent(_key(Qt.Key.Key_Down))
        self.assertIs(view.currentItem(), self._header(widget, "split"))
        view.keyPressEvent(_key(Qt.Key.Key_End))
        self.assertIs(view.currentItem(), self._header(widget, "host"))
        view.keyPressEvent(_key(Qt.Key.Key_Home))
        self.assertIs(view.currentItem(), self._header(widget, "fake"))
        view.keyPressEvent(_key(Qt.Key.Key_Up))
        self.assertIs(view.currentItem(), self._header(widget, "fake"))

    def test_enter_on_header_toggles_the_group_and_does_not_pick_a_strategy(self) -> None:
        widget = self._widget()
        activated: list[str] = []
        widget.strategy_activated.connect(activated.append)
        view = widget._list
        view.setCurrentItem(self._header(widget, "split"))

        view.keyPressEvent(_key(Qt.Key.Key_Return))

        self.assertTrue(self._header(widget, "split").data(W._ROLE_GROUP_EXPANDED))
        view.keyPressEvent(_key(Qt.Key.Key_Down))
        self.assertEqual(view.currentItem().data(W._ROLE_STRATEGY_ID), "split-00")
        self.assertEqual(activated, [])

        view.keyPressEvent(_key(Qt.Key.Key_Return))
        self.assertEqual(activated, ["split-00"])

    def test_widget_level_enter_on_header_toggles_too(self) -> None:
        widget = self._widget()
        widget._list.setCurrentItem(self._header(widget, "host"))

        self.assertTrue(widget._handle_strategy_keyboard_event(_key(Qt.Key.Key_Space)))

        self.assertTrue(self._header(widget, "host").data(W._ROLE_GROUP_EXPANDED))

    def test_clicking_a_header_item_never_activates_a_strategy(self) -> None:
        widget = self._widget()
        activated: list[str] = []
        widget.strategy_activated.connect(activated.append)

        widget._on_item_clicked(self._header(widget, "fake"))
        widget._on_item_activated(self._header(widget, "fake"))

        self.assertEqual(activated, [])

    # -------------------------- обновление на месте --------------------------

    def test_favorite_moves_inside_its_group_without_rebuild(self) -> None:
        entries = _entries()
        widget = self._widget(entries=entries, current="split-05")
        widget._rebuild_tree = Mock(side_effect=AssertionError("избранное не должно пересобирать список"))
        headers_before = dict(widget._group_header_items)

        widget.set_rows(
            entries=dict(entries),
            states={"split-09": ProfileStrategyState(rating="", favorite=True)},
            current_strategy_id="split-05",
        )

        rows = self._rows(widget)
        split_header_row = rows.index(("group", "split", False))
        self.assertEqual(rows[split_header_row + 1], ("strategy", "split-09", False))
        self.assertEqual(rows[split_header_row + 2], ("strategy", "split-00", False))
        self.assertEqual(widget._group_header_items, headers_before)
        self.assertEqual(widget._list.count(), 36 + 3)
        self.assertTrue(widget._item_by_strategy_id["split-09"].data(W._ROLE_FAVORITE))

    def test_row_moved_inside_a_collapsed_group_stays_hidden(self) -> None:
        entries = _entries()
        widget = self._widget(entries=entries, current="split-05")

        widget.set_rows(
            entries=dict(entries),
            states={"host-09": ProfileStrategyState(rating="", favorite=True)},
            current_strategy_id="split-05",
        )

        rows = self._rows(widget)
        host_header_row = rows.index(("group", "host", False))
        self.assertEqual(rows[host_header_row + 1], ("strategy", "host-09", True))

    def test_rating_change_updates_the_row_marks_in_place(self) -> None:
        entries = _entries()
        widget = self._widget(entries=entries, current="split-05")
        widget._rebuild_tree = Mock(side_effect=AssertionError("оценка не должна пересобирать список"))
        item = widget._item_by_strategy_id["split-02"]

        widget.set_rows(
            entries=dict(entries),
            states={"split-02": ProfileStrategyState(rating="notwork", favorite=False)},
            current_strategy_id="split-05",
        )

        self.assertIs(widget._item_by_strategy_id["split-02"], item)
        self.assertEqual(item.data(W._ROLE_RATING), "notwork")
        self.assertFalse(item.data(W._ROLE_FAVORITE))


class StrategyMarksTooltipTests(unittest.TestCase):
    def test_marks_are_explained_in_words(self) -> None:
        marks = widget_module._strategy_marks_tooltip

        self.assertEqual(marks(is_active=False, favorite=False, rating=""), "")
        self.assertEqual(
            marks(is_active=True, favorite=True, rating="work"),
            "Эта стратегия выбрана для профиля.\n"
            "Звезда: стратегия у вас в избранном.\n"
            "Галочка: вы отметили, что эта стратегия работает.",
        )
        self.assertEqual(
            marks(is_active=False, favorite=False, rating="notwork"),
            "Крестик: вы отметили, что эта стратегия не работает.",
        )


class _SpyMetrics(QFontMetrics):
    elided: list[str] = []

    def elidedText(self, text, mode, width, flags=0):  # noqa: N802
        type(self).elided.append(str(text))
        return super().elidedText(text, mode, width, flags)


class StrategyRowPaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_marks_are_icons_and_only_selected_row_has_a_word(self) -> None:
        entries = _entries(per_family=4)
        widget = ProfileStrategyListWidget()
        self.addCleanup(widget.deleteLater)
        widget._strategy_filter_runtime = None
        widget.resize(900, 600)
        widget.set_rows(
            entries=entries,
            states={
                "fake-00": ProfileStrategyState(rating="work", favorite=True),
                "fake-01": ProfileStrategyState(rating="notwork", favorite=False),
            },
            current_strategy_id="fake-02",
        )
        widget.show()
        self._app.processEvents()

        icons: list[str] = []
        real_pixmap = widget_module.get_cached_qta_pixmap

        def spy_pixmap(name, **kwargs):
            icons.append(str(name))
            return real_pixmap(name, **kwargs)

        _SpyMetrics.elided = []
        with patch.object(widget_module, "get_cached_qta_pixmap", spy_pixmap), patch.object(
            widget_module, "QFontMetrics", _SpyMetrics
        ):
            image = widget.grab().toImage()

        self.assertFalse(image.isNull())
        self.assertEqual(icons.count("fa5s.star"), 1)
        self.assertEqual(icons.count("fa5s.check"), 1)
        self.assertEqual(icons.count("fa5s.times"), 1)
        # Заголовки трёх групп раскрыты: у каждого стрелка «вниз» и свой значок.
        self.assertEqual(icons.count("fa5s.chevron-down"), 3)
        texts = _SpyMetrics.elided
        self.assertEqual(texts.count("Выбрана"), 1)
        self.assertFalse(any("В избранном" in text or "Работает" in text for text in texts))
        self.assertIn("Подмена пакета", texts)
        self.assertIn("перед настоящими данными уходит поддельный пакет", texts)


if __name__ == "__main__":
    unittest.main()
