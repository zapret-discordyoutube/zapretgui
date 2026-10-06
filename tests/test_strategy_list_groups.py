from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QKeyEvent, QPainter, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem

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

    def _open_groups(self, widget) -> list[str]:
        return [key for key, header in widget._group_header_items.items() if header.data(W._ROLE_GROUP_EXPANDED)]

    def _shown_widget(self, *, per_family: int = 40, current: str = "none") -> ProfileStrategyListWidget:
        """Список на экране: групп по per_family строк хватает на прокрутку."""
        widget = self._widget(entries=_entries(per_family=per_family), current=current)
        widget.show()
        self._app.processEvents()
        return widget

    def _scroll_to(self, widget, value: int) -> None:
        widget._list.verticalScrollBar().setValue(int(value))
        self._app.processEvents()

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

    def test_only_the_group_of_the_selected_strategy_is_open(self) -> None:
        # Избранное и отметка «работает» групп не раскрывают: открыта одна.
        widget = self._widget(
            states={
                "fake-03": ProfileStrategyState(rating="", favorite=True),
                "split-07": ProfileStrategyState(rating="work", favorite=False),
            },
            current="host-05",
        )

        self.assertEqual(self._open_groups(widget), ["host"])
        widget._toggle_group_item(self._header(widget, "fake"))
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

    # -------------------------- одна открытая группа --------------------------

    def test_opening_another_group_closes_the_previous_one(self) -> None:
        widget = self._widget(current="host-05")
        widget._rebuild_tree = Mock(side_effect=AssertionError("сворачивание не должно пересобирать список"))

        widget._list.group_toggle_requested.emit(self._header(widget, "split"))

        self.assertEqual(self._open_groups(widget), ["split"])
        self.assertEqual(self._visible(widget), ["fake", "split", *[f"split-{n:02d}" for n in range(12)], "host"])

    def test_short_list_groups_fold_independently(self) -> None:
        widget = self._widget(entries=_entries(per_family=4))

        widget._toggle_group_item(self._header(widget, "fake"))

        self.assertEqual(self._open_groups(widget), ["split", "host"])

    def test_opened_group_is_reported_and_remembered_for_its_profile(self) -> None:
        entries = _entries()
        widget = self._widget(entries=entries)
        reported: list[tuple[str, str]] = []
        widget.open_group_changed.connect(lambda token, group: reported.append((token, group)))
        widget.set_rows(entries=entries, states={}, current_strategy_id="host-05", open_group_token="uid:a")
        self.assertEqual(self._open_groups(widget), ["host"])

        widget._toggle_group_item(self._header(widget, "split"))
        self.assertEqual(reported, [("uid:a", "split")])

        # Другой профиль с тем же каталогом: у него своя открытая группа.
        widget.set_rows(entries=entries, states={}, current_strategy_id="fake-02", open_group_token="uid:b")
        self.assertEqual(self._open_groups(widget), ["fake"])
        self.assertEqual(widget._list.currentItem().data(W._ROLE_STRATEGY_ID), "fake-02")

        # Вернулись: открыта та же группа, даже если из настроек пришло старое значение.
        widget.set_rows(
            entries=entries,
            states={},
            current_strategy_id="host-05",
            open_group_token="uid:a",
            open_group="fake",
        )
        self.assertEqual(self._open_groups(widget), ["split"])

        widget._toggle_group_item(self._header(widget, "split"))
        self.assertEqual(self._open_groups(widget), [])
        self.assertEqual(reported[-1], ("uid:a", ""))

    def test_saved_group_opens_on_first_show_of_the_profile(self) -> None:
        entries = _entries()
        widget = self._widget(entries=entries)

        widget.set_rows(
            entries=entries, states={}, current_strategy_id="host-05", open_group_token="uid:a", open_group="split"
        )
        self.assertEqual(self._open_groups(widget), ["split"])

        # "" — человек свернул все группы.
        widget.set_rows(
            entries=entries, states={}, current_strategy_id="host-05", open_group_token="uid:b", open_group=""
        )
        self.assertEqual(self._open_groups(widget), [])

        # Группы с таким ключом в каталоге нет — открыта группа выбранной стратегии.
        widget.set_rows(
            entries=entries, states={}, current_strategy_id="host-05", open_group_token="uid:c", open_group="send"
        )
        self.assertEqual(self._open_groups(widget), ["host"])

    def test_search_does_not_change_the_remembered_group(self) -> None:
        widget = self._widget(current="host-05")
        reported: list[tuple[str, str]] = []
        widget.open_group_changed.connect(lambda token, group: reported.append((token, group)))

        widget._search.setText("07")
        widget._toggle_group_item(self._header(widget, "fake"))
        widget._search.setText("")

        self.assertEqual(reported, [])
        self.assertEqual(self._open_groups(widget), ["host"])

    # -------------------------- где выбранная стратегия --------------------------

    def _current_marks(self, widget) -> dict[str, str]:
        return {
            key: str(header.data(W._ROLE_GROUP_CURRENT_NAME) or "")
            for key, header in widget._group_header_items.items()
            if header.data(W._ROLE_GROUP_CURRENT_NAME)
        }

    def test_collapsed_group_of_the_selected_strategy_is_marked(self) -> None:
        entries = _entries()
        widget = self._widget(entries=entries)
        # Человек свернул все группы: выбранной строки не видно.
        widget.set_rows(
            entries=entries, states={}, current_strategy_id="host-05", open_group_token="uid:a", open_group=""
        )

        self.assertEqual(self._open_groups(widget), [])
        self.assertEqual(self._current_marks(widget), {"host": "Host 05"})
        header = self._header(widget, "host")
        self.assertIn("В этой группе выбранная стратегия: Host 05.", header.data(Qt.ItemDataRole.AccessibleTextRole))
        self.assertIn("В этой группе выбранная стратегия: Host 05", header.data(W._ROLE_TOOLTIP_TEXT))
        self.assertNotIn("выбранная стратегия", self._header(widget, "fake").data(Qt.ItemDataRole.AccessibleTextRole))

    def test_mark_follows_the_selected_strategy_without_rebuild(self) -> None:
        widget = self._widget(current="host-05")
        widget._rebuild_tree = Mock(side_effect=AssertionError("пометка не должна пересобирать список"))

        widget.set_current_strategy_id("fake-02")
        self.assertEqual(self._current_marks(widget), {"fake": "Fake 02"})
        self.assertNotIn("выбранная стратегия", self._header(widget, "host").data(Qt.ItemDataRole.AccessibleTextRole))

        widget.set_rows(entries=_entries(), states={}, current_strategy_id="split-01")
        self.assertEqual(self._current_marks(widget), {"split": "Split 01"})

        widget.set_current_strategy_id("none")
        self.assertEqual(self._current_marks(widget), {})

    def test_mark_survives_toggling_and_search(self) -> None:
        widget = self._widget(current="host-05")

        widget._toggle_group_item(self._header(widget, "host"))
        widget._toggle_group_item(self._header(widget, "fake"))
        self.assertEqual(self._current_marks(widget), {"host": "Host 05"})
        self.assertIn("свернута", self._header(widget, "host").data(Qt.ItemDataRole.AccessibleTextRole))
        self.assertIn("Host 05", self._header(widget, "host").data(Qt.ItemDataRole.AccessibleTextRole))

        # Выбранная стратегия под поиск не попала, но её группа всё равно помечена.
        widget._search.setText("07")
        self.assertEqual(self._current_marks(widget), {"host": "Host 05"})

    def test_marked_header_paints_accent_strip_and_strategy_name(self) -> None:
        from ui.widgets.hover_row import profile_hover_row_rect

        widget = self._shown_widget(per_family=12, current="host-05")
        widget._toggle_group_item(self._header(widget, "host"))
        view = widget._list
        option = QStyleOptionViewItem()
        view.initViewItemOption(option)
        option.rect = QRect(0, 0, 900, widget_module._STRATEGY_ROW_HEIGHT)
        row_left = profile_hover_row_rect(option.rect).left()

        def painted_columns(group_key: str) -> set[int]:
            canvas = QPixmap(900, widget_module._STRATEGY_ROW_HEIGHT)
            canvas.fill(QColor(0, 0, 0, 0))
            painter = QPainter(canvas)
            try:
                view.itemDelegate().paint(painter, option, view.indexFromItem(self._header(widget, group_key)))
            finally:
                painter.end()
            image = canvas.toImage()
            return {
                x for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x, y).alpha() > 0
            }

        marked = painted_columns("host")
        plain = painted_columns("fake")
        # Полоска стоит левее стрелки сворачивания.
        self.assertTrue(any(x < row_left + 12 for x in marked))
        self.assertFalse(any(x < row_left + 12 for x in plain))
        # Справа — название выбранной стратегии и «Выбрана»; у обычной группы там пусто.
        self.assertTrue(any(x > 700 for x in marked))
        self.assertFalse(any(x > 700 for x in plain))

    def test_open_group_shows_only_the_strip_because_the_row_is_visible(self) -> None:
        widget = self._shown_widget(per_family=12, current="host-05")
        view = widget._list
        option = QStyleOptionViewItem()
        view.initViewItemOption(option)
        option.rect = QRect(0, 0, 900, widget_module._STRATEGY_ROW_HEIGHT)
        canvas = QPixmap(900, widget_module._STRATEGY_ROW_HEIGHT)
        canvas.fill(QColor(0, 0, 0, 0))
        painter = QPainter(canvas)
        try:
            view.itemDelegate().paint(painter, option, view.indexFromItem(self._header(widget, "host")))
        finally:
            painter.end()
        image = canvas.toImage()

        self.assertEqual(self._open_groups(widget), ["host"])
        self.assertFalse(
            any(image.pixelColor(x, y).alpha() > 0 for x in range(700, 900) for y in range(image.height()))
        )

    # -------------------------- приклеенный заголовок --------------------------

    def test_header_of_open_group_stays_at_the_top_while_its_rows_scroll(self) -> None:
        widget = self._shown_widget()
        view = widget._list
        header = self._header(widget, "fake")
        widget._toggle_group_item(header)
        self._app.processEvents()
        self.assertIsNone(view.pinned_group_header())

        self._scroll_to(widget, widget_module._STRATEGY_ROW_HEIGHT * 10)

        pinned = view.pinned_group_header()
        self.assertIsNotNone(pinned)
        self.assertIs(pinned[0], header)
        self.assertEqual(pinned[1].top(), 0)
        self.assertEqual(pinned[1].height(), widget_module._STRATEGY_ROW_HEIGHT)
        self.assertFalse(view.grab().isNull())
        self.assertEqual(view.rows_clip_top, 0)

    def test_next_header_pushes_the_pinned_one_out(self) -> None:
        widget = self._shown_widget()
        view = widget._list
        # Поиск раскрывает все группы: в каждой по 13 найденных строк.
        widget._search.setText("0")
        self._app.processEvents()
        next_top = view.visualItemRect(self._header(widget, "split")).top()

        # Следующий заголовок в 10 точках от верха: приклеенному места нет.
        self._scroll_to(widget, view.verticalScrollBar().value() + next_top - 10)

        pinned = view.pinned_group_header()
        self.assertIsNotNone(pinned)
        self.assertIs(pinned[0], self._header(widget, "fake"))
        self.assertEqual(view.visualItemRect(self._header(widget, "split")).top(), 10)
        self.assertEqual(pinned[1].bottom() + 1, 10)

        # Следующая группа доехала до верха — теперь приклеен её заголовок.
        self._scroll_to(widget, view.verticalScrollBar().value() + 10 + widget_module._STRATEGY_ROW_HEIGHT * 3)
        pinned = view.pinned_group_header()
        self.assertIs(pinned[0], self._header(widget, "split"))
        self.assertEqual(pinned[1].top(), 0)

    def test_click_on_pinned_header_closes_the_group_and_picks_nothing(self) -> None:
        widget = self._shown_widget()
        view = widget._list
        widget._toggle_group_item(self._header(widget, "fake"))
        self._scroll_to(widget, widget_module._STRATEGY_ROW_HEIGHT * 10)
        activated: list[str] = []
        widget.strategy_activated.connect(activated.append)

        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(120, 12))
        self._app.processEvents()

        self.assertEqual(self._open_groups(widget), [])
        self.assertEqual(activated, [])
        self.assertEqual(view.verticalScrollBar().value(), 0)
        self.assertIsNone(view.pinned_group_header())

    def test_clicked_header_stays_on_screen_when_group_above_closes(self) -> None:
        widget = self._shown_widget()
        view = widget._list
        widget._toggle_group_item(self._header(widget, "fake"))
        self._app.processEvents()
        split_header = self._header(widget, "split")
        # Долистали до конца раскрытой группы: следующий заголовок на виду.
        self._scroll_to(widget, view.verticalScrollBar().maximum())
        self.assertGreater(view.verticalScrollBar().value(), widget_module._STRATEGY_ROW_HEIGHT * 10)
        self.assertLess(view.visualItemRect(split_header).top(), view.viewport().height())

        view.group_toggle_requested.emit(split_header)
        self._app.processEvents()

        self.assertEqual(self._open_groups(widget), ["split"])
        top = view.visualItemRect(split_header).top()
        self.assertGreaterEqual(top, 0)
        self.assertLess(top, view.viewport().height())
        self.assertIsNone(view.pinned_group_header())

    def test_rows_are_not_painted_under_the_pinned_header(self) -> None:
        widget = self._shown_widget(current="fake-00")
        view = widget._list
        index = view.indexFromItem(widget._item_by_strategy_id["fake-00"])
        option = QStyleOptionViewItem()
        view.initViewItemOption(option)
        option.rect = QRect(0, 0, 400, widget_module._STRATEGY_ROW_HEIGHT)

        def painted_rows(clip_top: int) -> set[int]:
            canvas = QPixmap(400, widget_module._STRATEGY_ROW_HEIGHT)
            canvas.fill(QColor(0, 0, 0, 0))
            painter = QPainter(canvas)
            view.rows_clip_top = clip_top
            try:
                view.itemDelegate().paint(painter, option, index)
            finally:
                view.rows_clip_top = 0
                painter.end()
            image = canvas.toImage()
            return {
                y for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x, y).alpha() > 0
            }

        self.assertLess(min(painted_rows(0)), 12)
        clipped = painted_rows(20)
        self.assertTrue(clipped)
        self.assertGreaterEqual(min(clipped), 20)

    # -------------------------- клавиатура и мышь --------------------------

    def test_left_arrow_closes_the_group_and_right_arrow_opens_it(self) -> None:
        widget = self._widget(current="host-05")
        view = widget._list
        header = self._header(widget, "host")
        self.assertEqual(view.currentItem().data(W._ROLE_STRATEGY_ID), "host-05")

        view.keyPressEvent(_key(Qt.Key.Key_Left))
        self.assertEqual(self._open_groups(widget), [])
        self.assertIs(view.currentItem(), header)

        view.keyPressEvent(_key(Qt.Key.Key_Left))
        self.assertEqual(self._open_groups(widget), [])

        view.keyPressEvent(_key(Qt.Key.Key_Right))
        self.assertEqual(self._open_groups(widget), ["host"])
        view.keyPressEvent(_key(Qt.Key.Key_Right))
        self.assertEqual(self._open_groups(widget), ["host"])

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
