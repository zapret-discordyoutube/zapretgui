"""Группировки списка готовых стратегий и плитки в несколько столбцов.

Список раскладывается по способу обхода, по серии или по источнику
(profile.strategy_grouping); внутри большой группы стоят подзаголовки. На
широком списке стратегии показаны плитками в несколько столбцов.
"""

from __future__ import annotations

import copy
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from profile.strategy_catalog import _parse_catalog_file
from profile.strategy_families import strategy_family_keys
from profile.strategy_grouping import (
    GROUPING_METHOD,
    GROUPING_SERIES,
    GROUPING_SOURCE,
    SERIES_OTHER_KEY,
    SOURCE_NONE_KEY,
    normalize_strategy_grouping,
    split_strategy_name,
    strategy_detail_without_source,
    strategy_grouping_layout,
    strategy_series,
    strategy_source,
)
from profile.strategy_list_filter import build_profile_strategy_list_plan
from profile.strategy_state import ProfileStrategyStateStore
from profile.ui.profile_strategy_list_widget import ProfileStrategyListWidget

W = ProfileStrategyListWidget
CATALOGS_ROOT = Path(__file__).resolve().parents[1] / "src" / "system" / "strategy_catalogs"
# Таким ключ открытой группы принимают настройки (settings.normalize).
_SETTINGS_GROUP_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def _catalog(engine: str, name: str) -> dict:
    path = CATALOGS_ROOT / engine / f"{name}.txt"
    return _parse_catalog_file(path, path.stem)


def _entry(name: str, *desync: str):
    return SimpleNamespace(name=name, args="\n".join(f"--lua-desync={value}" for value in desync))


def _entries() -> dict:
    """Два способа обхода, в каждом серии General и Flowseal и одиночки."""
    entries = {}
    for n in range(8):
        entries[f"gf-{n}"] = _entry(f"General ALT{n} 1.9.9 · из Steam", "fake")
        entries[f"ff-{n}"] = _entry(f"Flowseal ALT{n} 1.10.3 · из Telegram", "fake")
        entries[f"gs-{n}"] = _entry(f"General S{n} 1.9.9 · из Telegram", "multisplit")
        entries[f"fs-{n}"] = _entry(f"Flowseal S{n} 1.10.3", "multisplit")
    entries["lone-fake"] = _entry("Censorliber Google", "fake")
    entries["lone-split"] = _entry("Dronatar 4.3", "multisplit")
    return entries


def _key(key: Qt.Key) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


class StrategyNamePartsTests(unittest.TestCase):
    def test_name_is_split_into_title_and_detail(self) -> None:
        self.assertEqual(split_strategy_name("General ALT3 1.9.9 · из Telegram"), ("General ALT3 1.9.9", "из Telegram"))
        self.assertEqual(split_strategy_name("Censorliber Google"), ("Censorliber Google", ""))
        self.assertEqual(
            split_strategy_name("Fake с syndata · AutoTTL · из «Все сайты»"),
            ("Fake с syndata", "AutoTTL · из «Все сайты»"),
        )

    def test_series_is_the_first_word_of_the_name(self) -> None:
        self.assertEqual(strategy_series("General ALT3 1.9.9 · из Telegram"), "General")
        self.assertEqual(strategy_series("Combo: fake и split"), "Combo")
        self.assertEqual(strategy_series("Fake, tls и quic"), "Fake")
        self.assertEqual(strategy_series(""), "")

    def test_source_is_taken_from_the_detail_that_starts_with_iz(self) -> None:
        self.assertEqual(strategy_source("General 1.9.9 · из Steam"), "Steam")
        self.assertEqual(strategy_source("Fake · из «Все сайты» №2"), "Все сайты")
        self.assertEqual(strategy_source("Fake · AutoTTL · из «Все сайты»"), "Все сайты")
        self.assertEqual(strategy_source("Split2 seqovl 2 · устаревшая"), "")
        self.assertEqual(strategy_source("General 1.9.9"), "")

    def test_detail_under_a_source_header_drops_the_source(self) -> None:
        self.assertEqual(strategy_detail_without_source("General 1.9.9 · из Steam"), "")
        self.assertEqual(strategy_detail_without_source("Fake · AutoTTL · из «Все сайты»"), "AutoTTL")

    def test_unknown_grouping_falls_back_to_method(self) -> None:
        self.assertEqual(normalize_strategy_grouping("SERIES "), GROUPING_SERIES)
        self.assertEqual(normalize_strategy_grouping("по цвету"), GROUPING_METHOD)
        self.assertEqual(normalize_strategy_grouping(None), GROUPING_METHOD)


class StrategyGroupingLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = _catalog("winws2", "tcp")

    def test_method_grouping_keeps_groups_by_bypass_method(self) -> None:
        layout = strategy_grouping_layout(self.catalog, GROUPING_METHOD)

        self.assertEqual(
            {strategy_id: layout.group_key(strategy_id) for strategy_id in self.catalog},
            strategy_family_keys(self.catalog),
        )

    def test_every_grouping_places_every_strategy_under_a_storable_key(self) -> None:
        for grouping in (GROUPING_METHOD, GROUPING_SERIES, GROUPING_SOURCE):
            with self.subTest(grouping=grouping):
                layout = strategy_grouping_layout(self.catalog, grouping)

                self.assertEqual(set(layout.placements), set(self.catalog))
                group_keys = {placement.group_key for placement in layout.placements.values()}
                self.assertGreater(len(group_keys), 1)
                for group_key in group_keys:
                    self.assertRegex(group_key, _SETTINGS_GROUP_KEY)
                    self.assertTrue(layout.group_info(group_key).title)

    def test_series_grouping_gathers_small_series_into_one_group(self) -> None:
        layout = strategy_grouping_layout(_entries(), GROUPING_SERIES)

        titles = {layout.group_info(layout.group_key(strategy_id)).title for strategy_id in _entries()}
        self.assertEqual(titles, {"General", "Flowseal", "Остальные серии"})
        self.assertEqual(layout.group_key("lone-fake"), SERIES_OTHER_KEY)
        self.assertEqual(layout.group_key("lone-split"), SERIES_OTHER_KEY)
        # Группа-остаток стоит последней, как бы велика она ни была.
        self.assertEqual(layout.group_rank[SERIES_OTHER_KEY], len(layout.group_rank) - 1)

    def test_source_grouping_puts_strategies_without_source_last(self) -> None:
        layout = strategy_grouping_layout(_entries(), GROUPING_SOURCE)

        self.assertEqual(layout.group_info(layout.group_key("gf-0")).title, "Steam")
        self.assertEqual(layout.group_info(layout.group_key("ff-0")).title, "Telegram")
        self.assertEqual(layout.group_key("gs-0"), layout.group_key("ff-0"))
        self.assertEqual(layout.group_key("fs-0"), SOURCE_NONE_KEY)
        self.assertEqual(layout.group_rank[SOURCE_NONE_KEY], len(layout.group_rank) - 1)

    def test_big_group_is_divided_by_series_and_singles_go_to_the_rest(self) -> None:
        layout = strategy_grouping_layout(_entries(), GROUPING_METHOD)

        self.assertEqual(layout.placement("gf-0").subgroup_title, "General")
        self.assertEqual(layout.placement("ff-0").subgroup_title, "Flowseal")
        self.assertEqual(layout.placement("lone-fake").subgroup_title, "Остальные")
        self.assertNotEqual(layout.placement("gf-0").subgroup_key, layout.placement("gs-0").subgroup_key)

    def test_series_group_is_divided_by_bypass_method(self) -> None:
        layout = strategy_grouping_layout(_entries(), GROUPING_SERIES)

        self.assertEqual(layout.placement("gf-0").subgroup_title, "Подмена пакета")
        self.assertEqual(layout.placement("gs-0").subgroup_title, "Разделение")

    def test_small_group_has_no_subheaders(self) -> None:
        entries = {
            "a": _entry("General 1", "fake"),
            "b": _entry("General 2", "fake"),
            "c": _entry("Flowseal 1", "fake"),
            "d": _entry("Flowseal 2", "fake"),
            "e": _entry("General 3", "multisplit"),
            "f": _entry("General 4", "multisplit"),
            "g": _entry("General 5", "multisplit"),
        }

        layout = strategy_grouping_layout(entries, GROUPING_METHOD)

        self.assertEqual({placement.subgroup_key for placement in layout.placements.values()}, {""})

    def test_catalog_with_one_group_stays_a_flat_list(self) -> None:
        layout = strategy_grouping_layout(_catalog("winws1", "tcp"), GROUPING_METHOD)

        self.assertEqual({placement.subgroup_key for placement in layout.placements.values()}, {""})


class StrategyGroupingPlanTests(unittest.TestCase):
    def _plan(self, grouping: str, search_text: str = ""):
        return build_profile_strategy_list_plan(
            entries=_entries(),
            states={},
            current_strategy_id="none",
            search_text=search_text,
            grouping=grouping,
        )

    def test_rows_of_one_group_and_one_subheader_stand_together(self) -> None:
        for grouping in (GROUPING_METHOD, GROUPING_SERIES, GROUPING_SOURCE):
            with self.subTest(grouping=grouping):
                plan = self._plan(grouping)

                self.assertEqual(plan.grouping, grouping)
                self.assertEqual(len(plan.rows), len(_entries()))
                for field in ("family_key", "subgroup_key"):
                    seen: list[tuple[str, str]] = []
                    for row in plan.rows:
                        value = (row.family_key, getattr(row, field))
                        if not seen or seen[-1] != value:
                            self.assertNotIn(value, seen)
                            seen.append(value)

    def test_row_carries_title_and_detail_for_the_tile(self) -> None:
        rows = {row.strategy_id: row for row in self._plan(GROUPING_METHOD).rows}

        self.assertEqual(rows["gf-3"].name, "General ALT3 1.9.9 · из Steam")
        self.assertEqual(rows["gf-3"].title, "General ALT3 1.9.9")
        self.assertEqual(rows["gf-3"].detail, "из Steam")
        self.assertEqual(rows["fs-3"].detail, "")

    def test_source_group_does_not_repeat_the_source_in_tiles(self) -> None:
        rows = {row.strategy_id: row for row in self._plan(GROUPING_SOURCE).rows}

        self.assertEqual(rows["gf-3"].detail, "")
        self.assertEqual(rows["gf-3"].title, "General ALT3 1.9.9")

    def test_search_results_have_no_subheaders(self) -> None:
        plan = self._plan(GROUPING_METHOD, search_text="general")

        self.assertEqual(len(plan.rows), 16)
        self.assertEqual({row.subgroup_key for row in plan.rows}, {""})


class StrategyTilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self._app.closeAllWindows()
        self._app.processEvents()

    def _widget(self, *, width: int = 1000, current: str = "gf-0", grouping: str | None = None) -> W:
        widget = ProfileStrategyListWidget()
        self.addCleanup(widget.deleteLater)
        # Без фонового фильтра список собирается сразу, в этом же вызове.
        widget._strategy_filter_runtime = None
        widget.resize(width, 700)
        widget.set_rows(entries=_entries(), states={}, current_strategy_id=current, grouping=grouping)
        widget.show()
        self._app.processEvents()
        return widget

    def _rect(self, widget, strategy_id: str):
        return widget._list.visualItemRect(widget._item_by_strategy_id[strategy_id])

    def _visible_tiles(self, widget) -> list[str]:
        result = []
        for row in range(widget._list.count()):
            item = widget._list.item(row)
            strategy_id = str(item.data(W._ROLE_STRATEGY_ID) or "")
            if strategy_id and not widget._list.isRowHidden(row):
                result.append(strategy_id)
        return result

    def _subheaders(self, widget) -> list[tuple[str, int, bool]]:
        """(подпись, число стратегий, скрыт?) каждого подзаголовка."""
        return [
            (str(item.data(W._ROLE_NAME_TEXT)), int(item.data(W._ROLE_GROUP_COUNT)), item.isHidden())
            for item in widget._subgroup_items
        ]

    def test_wide_list_shows_tiles_in_several_columns(self) -> None:
        widget = self._widget(width=1000)

        self.assertEqual(widget._list.column_count(), 3)
        tiles = self._visible_tiles(widget)
        first_line = [self._rect(widget, strategy_id) for strategy_id in tiles[:3]]
        self.assertEqual(len({rect.top() for rect in first_line}), 1)
        self.assertEqual([rect.left() for rect in first_line], sorted({rect.left() for rect in first_line}))
        self.assertGreater(self._rect(widget, tiles[3]).top(), first_line[0].top())
        self.assertEqual(self._rect(widget, tiles[3]).left(), first_line[0].left())
        self.assertLessEqual(first_line[-1].right(), widget._list.viewport().width())

    def test_headers_take_the_whole_line(self) -> None:
        widget = self._widget(width=1000)
        full_width = widget._list.viewport().width()

        for header in widget._group_header_items.values():
            self.assertEqual(widget._list.visualItemRect(header).width(), full_width)
        for item in widget._subgroup_items:
            if not item.isHidden():
                self.assertEqual(widget._list.visualItemRect(item).width(), full_width)
                self.assertEqual(widget._list.visualItemRect(item).left(), 0)

    def test_narrow_list_keeps_one_column_of_rows(self) -> None:
        widget = self._widget(width=520)

        self.assertEqual(widget._list.column_count(), 1)
        tiles = self._visible_tiles(widget)
        self.assertEqual({self._rect(widget, strategy_id).left() for strategy_id in tiles}, {0})
        self.assertEqual(self._rect(widget, tiles[0]).height(), 31)

    def test_column_count_follows_the_width(self) -> None:
        widget = self._widget(width=1000)

        widget.resize(640, 700)
        self._app.processEvents()
        self.assertEqual(widget._list.column_count(), 2)
        tiles = self._visible_tiles(widget)
        self.assertEqual(self._rect(widget, tiles[0]).top(), self._rect(widget, tiles[1]).top())
        self.assertGreater(self._rect(widget, tiles[2]).top(), self._rect(widget, tiles[0]).top())

    def test_subheaders_of_a_collapsed_group_are_hidden_with_it(self) -> None:
        widget = self._widget(current="gf-0")

        self.assertEqual(
            self._subheaders(widget),
            [
                ("Flowseal", 8, False),
                ("General", 8, False),
                ("Остальные", 1, False),
                ("Flowseal", 8, True),
                ("General", 8, True),
                ("Остальные", 1, True),
            ],
        )

        widget._toggle_group_item(widget._group_header_items["split"])

        self.assertEqual([hidden for _title, _count, hidden in self._subheaders(widget)], [True] * 3 + [False] * 3)

    def test_subheader_is_not_a_stop_for_the_keyboard(self) -> None:
        widget = self._widget(current="gf-0")
        header = widget._group_header_items["fake"]
        widget._list.setCurrentItem(header)

        widget._list.keyPressEvent(_key(Qt.Key.Key_Down))

        current = widget._list.currentItem()
        self.assertEqual(current.data(W._ROLE_STRATEGY_ID), self._visible_tiles(widget)[0])

    def test_arrows_move_over_the_grid_of_tiles(self) -> None:
        widget = self._widget(current="gf-0")
        tiles = self._visible_tiles(widget)
        widget._list.setCurrentItem(widget._item_by_strategy_id[tiles[1]])

        widget._list.keyPressEvent(_key(Qt.Key.Key_Down))
        self.assertEqual(widget._list.currentItem().data(W._ROLE_STRATEGY_ID), tiles[4])

        widget._list.keyPressEvent(_key(Qt.Key.Key_Right))
        self.assertEqual(widget._list.currentItem().data(W._ROLE_STRATEGY_ID), tiles[5])

        widget._list.keyPressEvent(_key(Qt.Key.Key_Up))
        self.assertEqual(widget._list.currentItem().data(W._ROLE_STRATEGY_ID), tiles[2])

        widget._list.keyPressEvent(_key(Qt.Key.Key_Left))
        self.assertEqual(widget._list.currentItem().data(W._ROLE_STRATEGY_ID), tiles[1])
        # Группа при этом не свернулась: на плитке стрелки вбок ходят по плиткам.
        self.assertTrue(widget._group_header_items["fake"].data(W._ROLE_GROUP_EXPANDED))

    def test_arrow_left_on_a_header_still_collapses_the_group(self) -> None:
        widget = self._widget(current="gf-0")
        header = widget._group_header_items["fake"]
        widget._list.setCurrentItem(header)

        widget._list.keyPressEvent(_key(Qt.Key.Key_Left))

        self.assertFalse(header.data(W._ROLE_GROUP_EXPANDED))
        self.assertEqual(self._visible_tiles(widget), [])

    def test_tiles_paint_without_errors_in_every_grouping(self) -> None:
        for grouping in (GROUPING_METHOD, GROUPING_SERIES, GROUPING_SOURCE):
            with self.subTest(grouping=grouping):
                widget = self._widget(grouping=grouping)

                self.assertFalse(widget.grab().toImage().isNull())

    # ----------------------- переключатель группировки -----------------------

    def test_switch_is_shown_only_for_a_long_list(self) -> None:
        widget = self._widget()
        self.assertTrue(widget._grouping_row.isVisible())

        widget.set_rows(
            entries={key: value for key, value in list(_entries().items())[:10]},
            states={},
            current_strategy_id="none",
        )

        self.assertFalse(widget._grouping_row.isVisible())

    def test_choosing_a_grouping_rebuilds_groups_and_reports_it(self) -> None:
        widget = self._widget(current="gf-0")
        groupings: list[str] = []
        open_groups: list[tuple[str, str]] = []
        widget.grouping_changed.connect(groupings.append)
        widget.open_group_changed.connect(lambda token, group: open_groups.append((token, group)))

        widget._grouping_combo.setCurrentIndex(widget._grouping_combo.findData(GROUPING_SERIES))

        self.assertEqual(groupings, [GROUPING_SERIES])
        titles = [header.data(W._ROLE_NAME_TEXT) for header in widget._group_header_items.values()]
        self.assertEqual(titles, ["Flowseal", "General", "Остальные серии"])
        # Открыта группа выбранной стратегии, и её ключ ушёл на сохранение.
        general = next(key for key, header in widget._group_header_items.items() if header.data(W._ROLE_NAME_TEXT) == "General")
        self.assertEqual(open_groups, [("", general)])
        self.assertIn("gf-0", self._visible_tiles(widget))
        self.assertNotIn("ff-0", self._visible_tiles(widget))

    def test_saved_grouping_regroups_rows_that_are_already_built(self) -> None:
        widget = self._widget(current="gf-0")
        self.assertEqual(list(widget._group_header_items), ["fake", "split"])

        # Те же стратегии и те же отметки, изменилась только группировка.
        widget.set_rows(entries=_entries(), states={}, current_strategy_id="gf-0", grouping=GROUPING_SERIES)

        titles = [header.data(W._ROLE_NAME_TEXT) for header in widget._group_header_items.values()]
        self.assertEqual(titles, ["Flowseal", "General", "Остальные серии"])
        self.assertEqual(widget._grouping_combo.currentData(), GROUPING_SERIES)

    def test_saved_grouping_is_applied_until_the_user_chooses_another(self) -> None:
        widget = self._widget(grouping=GROUPING_SOURCE)
        self.assertEqual(widget._grouping_combo.currentData(), GROUPING_SOURCE)
        self.assertIn("Steam", [header.data(W._ROLE_NAME_TEXT) for header in widget._group_header_items.values()])

        widget._grouping_combo.setCurrentIndex(widget._grouping_combo.findData(GROUPING_METHOD))
        # Запоздавший ответ из настроек выбор человека не перебивает.
        widget.set_rows(entries=_entries(), states={}, current_strategy_id="gf-0", grouping=GROUPING_SOURCE)

        self.assertEqual(widget._grouping_combo.currentData(), GROUPING_METHOD)
        self.assertEqual(list(widget._group_header_items), ["fake", "split"])


class StrategyGroupingStoreTests(unittest.TestCase):
    """Выбранная группировка лежит в настройках рядом с отметками стратегий."""

    def _store(self, data: dict | None = None) -> tuple[ProfileStrategyStateStore, dict]:
        from settings.normalize import normalize_profile_strategy_state

        box = {"data": copy.deepcopy(data) if data is not None else {"version": 1, "profiles": {}}, "writes": 0}

        def write(values: dict) -> dict:
            box["data"] = normalize_profile_strategy_state(values)
            box["writes"] += 1
            return copy.deepcopy(box["data"])

        for item in (
            patch(
                "profile.strategy_state.settings_store.get_profile_strategy_state_settings",
                side_effect=lambda: copy.deepcopy(box["data"]),
            ),
            patch("profile.strategy_state.settings_store.set_profile_strategy_state_settings", side_effect=write),
        ):
            item.start()
            self.addCleanup(item.stop)
        return ProfileStrategyStateStore(), box

    def test_list_is_grouped_by_method_until_the_user_chooses(self) -> None:
        store, _box = self._store()

        self.assertEqual(store.get_grouping(), GROUPING_METHOD)

    def test_chosen_grouping_is_remembered(self) -> None:
        store, box = self._store()

        self.assertTrue(store.set_grouping(GROUPING_SERIES))
        self.assertEqual(store.get_grouping(), GROUPING_SERIES)
        self.assertEqual(box["data"]["grouping"], GROUPING_SERIES)
        self.assertFalse(store.set_grouping(GROUPING_SERIES))
        self.assertEqual(box["writes"], 1)

    def test_default_grouping_is_not_stored(self) -> None:
        store, box = self._store({"version": 1, "profiles": {}, "grouping": GROUPING_SOURCE})

        self.assertTrue(store.set_grouping(GROUPING_METHOD))

        self.assertNotIn("grouping", box["data"])
        self.assertEqual(store.get_grouping(), GROUPING_METHOD)

    def test_marks_and_open_group_survive_a_grouping_change(self) -> None:
        store, box = self._store()
        store.set_open_group("uid:youtube", "split")

        store.set_grouping(GROUPING_SOURCE)

        self.assertEqual(store.get_open_group("uid:youtube"), "split")
        self.assertEqual(box["data"]["grouping"], GROUPING_SOURCE)

    def test_unknown_grouping_in_settings_is_dropped(self) -> None:
        from settings.normalize import normalize_profile_strategy_state

        self.assertNotIn("grouping", normalize_profile_strategy_state({"profiles": {}, "grouping": "по цвету"}))
        self.assertNotIn("grouping", normalize_profile_strategy_state({"profiles": {}, "grouping": "method"}))
        self.assertEqual(
            normalize_profile_strategy_state({"profiles": {}, "grouping": "source"})["grouping"],
            GROUPING_SOURCE,
        )


if __name__ == "__main__":
    unittest.main()
