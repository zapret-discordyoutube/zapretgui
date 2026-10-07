"""Окно списка готовых стратегий: модель, список, панели и связь со страницей."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QWidget

from profile.strategy_list import (
    FILTER_WORKS,
    RECOMMENDED_GROUP,
    ROW_GROUP,
    ROW_SECTION,
    ROW_STRATEGY,
    PlanRequest,
    build_plan,
    build_strategy_facts,
    visible_rows,
)
from profile.strategy_state import ProfileStrategyState
from profile.strategy_usage import StrategyUsage
from profile.ui.strategy_list import ProfileStrategyListWidget
from profile.ui.strategy_list import widget as widget_module
from profile.ui.strategy_list.delegate import strategy_tooltip, twin_chip_text
from profile.ui.strategy_list.model import ACTIVE_ROLE, ROW_ROLE, StrategyListModel


def _entry(name: str, args: str):
    return SimpleNamespace(name=name, args=args, visual=None, payload_scopes=(), old_name="")


def _entries() -> dict:
    entries = {}
    for number in range(16):
        # Две серии в одной группе: у группы появляются подзаголовки.
        series = "Alpha" if number < 8 else "Omega"
        entries[f"fake-{number:02d}"] = _entry(f"{series} v{number}", "--lua-desync=fake:blob=x")
    for number in range(16):
        entries[f"split-{number:02d}"] = _entry(f"Beta v{number}", "--lua-desync=multisplit:pos=1")
    for source in ("Steam", "Telegram", "Discord"):
        entries[f"twin-{source.lower()}"] = _entry(
            f"Gamma 1.9 · из {source}", "--lua-desync=fake:blob=x\n--lua-desync=multisplit:pos=2"
        )
    for number in range(3):
        entries[f"mix-{number}"] = _entry(f"Delta v{number}", "--lua-desync=fake:blob=x\n--lua-desync=multisplit:pos=2")
    return entries


USAGE = {
    "split-03": StrategyUsage(same_service=5, services=5),
    "fake-05": StrategyUsage(same_service=9, services=9),
    "fake-01": StrategyUsage(same_service=2, services=2),
}


class _WidgetCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _widget(self, *, entries=None, states=None, current="fake-05", usage=USAGE, open_group=None, width=1300):
        host = QWidget()
        self.addCleanup(host.deleteLater)
        widget = ProfileStrategyListWidget(host)
        widget.setGeometry(0, 0, width, 800)
        host.resize(width, 800)
        host.show()
        widget.set_rows(
            entries=entries if entries is not None else _entries(),
            states=states or {},
            current_strategy_id=current,
            open_group_token="uid:test",
            open_group=open_group,
            grouping="method",
            usage=usage,
        )
        self._app.processEvents()
        return widget

    @staticmethod
    def _rows(widget):
        return widget._list.list_model().rows()

    def _open_groups(self, widget) -> list[str]:
        return [row.group_key for row in self._rows(widget) if row.kind == ROW_GROUP and row.expanded]

    def _click_row(self, widget, key: str, *, at: QPoint | None = None) -> None:
        model = widget._list.list_model()
        index = model.index(model.row_of_key(key), 0)
        widget._list.scrollTo(index)
        rect = widget._list.visualRect(index)
        QTest.mouseClick(widget._list.viewport(), Qt.MouseButton.LeftButton, pos=at or QPoint(rect.left() + 60, rect.center().y()))


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _rows(self, **kwargs):
        open_groups = kwargs.pop("open_groups", {"fake"})
        plan = build_plan(PlanRequest(facts=build_strategy_facts(_entries()), **kwargs))
        return visible_rows(plan, open_groups=open_groups)

    def test_same_rows_report_only_changed_ones_without_reset(self) -> None:
        model = StrategyListModel()
        model.set_rows(self._rows())
        resets = QSignalSpy(model.modelReset)
        changes = QSignalSpy(model.dataChanged)

        reset = model.set_rows(self._rows(current_strategy_id="fake-03"))

        self.assertFalse(reset)
        self.assertEqual(len(resets), 0)
        # Перерисовываются только новая выбранная стратегия и заголовок её
        # группы (он называет выбранную стратегию), а не вся группа.
        self.assertEqual({model.row_at(change[0].row()).key for change in changes}, {"g:fake", "i:fake-03"})
        self.assertTrue(model.row_at(model.row_of_key("i:fake-03")).item.is_current)

    def test_different_rows_reset_the_model(self) -> None:
        model = StrategyListModel()
        model.set_rows(self._rows())
        resets = QSignalSpy(model.modelReset)

        self.assertTrue(model.set_rows(self._rows(open_groups={"split"})))
        self.assertEqual(len(resets), 1)

    def test_section_rows_are_disabled_and_roles_come_from_the_row(self) -> None:
        model = StrategyListModel()
        model.set_rows(self._rows(current_strategy_id="fake-03"))
        section = next(position for position, row in enumerate(model.rows()) if row.kind == ROW_SECTION)
        current = model.row_of_key("i:fake-03")

        self.assertEqual(model.flags(model.index(section, 0)), Qt.ItemFlag.NoItemFlags)
        self.assertTrue(model.index(current, 0).data(ACTIVE_ROLE))
        self.assertIs(model.index(current, 0).data(ROW_ROLE), model.rows()[current])
        self.assertIn("Alpha v3, выбрана", model.index(current, 0).data(Qt.ItemDataRole.AccessibleTextRole))


class WidgetStateTests(_WidgetCase):
    def test_long_list_opens_the_group_of_selected_strategy(self) -> None:
        widget = self._widget(current="split-07")

        self.assertEqual(self._open_groups(widget), ["split"])
        self.assertEqual(widget._list.current_row().strategy_id, "split-07")

    def test_remembered_group_wins_and_empty_means_all_collapsed(self) -> None:
        self.assertEqual(self._open_groups(self._widget(open_group="fake")), ["fake"])
        collapsed = self._widget(open_group="")
        self.assertEqual(self._open_groups(collapsed), [])
        self.assertEqual({row.kind for row in self._rows(collapsed)}, {ROW_GROUP})

    def test_opening_another_group_closes_the_previous_and_reports_it(self) -> None:
        widget = self._widget()
        spy = QSignalSpy(widget.open_group_changed)

        self._click_row(widget, "g:split")
        self.assertEqual(self._open_groups(widget), ["split"])
        self.assertEqual(list(spy[-1]), ["uid:test", "split"])

        self._click_row(widget, "g:split")
        self.assertEqual(self._open_groups(widget), [])
        self.assertEqual(list(spy[-1]), ["uid:test", ""])

    def test_short_list_is_fully_open_without_filters_and_try_panel(self) -> None:
        entries = {key: value for key, value in list(_entries().items())[:20]}
        widget = self._widget(entries=entries, current="fake-01", usage={})

        self.assertEqual(sum(row.kind == ROW_STRATEGY for row in self._rows(widget)), 20)
        self.assertTrue(widget._toolbar.filter_row.isHidden())
        self.assertTrue(widget._grouping_combo.isHidden())
        self.assertTrue(widget._try_panel.isHidden())
        self.assertFalse(widget._search.isHidden())

    def test_click_on_strategy_asks_page_to_apply_it_and_keeps_screen_until_page_answers(self) -> None:
        widget = self._widget()
        spy = QSignalSpy(widget.strategy_activated)

        self._click_row(widget, "i:fake-01")
        self._click_row(widget, "i:fake-05")

        self.assertEqual([list(call) for call in spy], [["fake-01"]])
        self.assertEqual(widget._plan.current_strategy_id, "fake-05")

    def test_new_current_strategy_in_collapsed_group_opens_that_group(self) -> None:
        widget = self._widget()

        widget.set_current_strategy_id("split-09")

        self.assertEqual(self._open_groups(widget), ["split"])
        self.assertEqual(widget._list.current_row().strategy_id, "split-09")

    def test_rating_update_without_usage_keeps_recommended_group(self) -> None:
        widget = self._widget()

        widget.set_rows(entries=widget._entries, states={"fake-01": ProfileStrategyState(rating="work")}, current_strategy_id="fake-05")

        recommended = [row.strategy_id for row in self._rows(widget) if row.kind == ROW_STRATEGY and row.group_key == RECOMMENDED_GROUP]
        self.assertEqual(recommended, ["fake-01", "fake-05", "split-03"])

    def test_twins_expand_by_click_on_the_chip(self) -> None:
        widget = self._widget(current="mix-0")
        head = next(row for row in self._rows(widget) if row.twin_count > 1)
        self.assertEqual(twin_chip_text(head), "ещё 2")
        model = widget._list.list_model()
        index = model.index(model.row_of_key(head.key), 0)
        chip = widget._list.itemDelegate().twin_chip_rect(widget._list.visualRect(index), head, widget._list.font())
        spy = QSignalSpy(widget.strategy_activated)

        self._click_row(widget, head.key, at=chip.center())

        twins = [row for row in self._rows(widget) if row.kind == ROW_STRATEGY and row.item.title == "Gamma 1.9"]
        self.assertEqual(len(twins), 3)
        self.assertEqual(twin_chip_text(twins[0]), "свернуть")
        self.assertEqual(len(spy), 0)

    def test_another_profile_forgets_expanded_twins(self) -> None:
        widget = self._widget(current="mix-0")
        widget._on_twins_toggle(next(row for row in self._rows(widget) if row.twin_count > 1).item.twin_key)

        widget.set_rows(entries=widget._entries, states={}, current_strategy_id="mix-0", open_group_token="uid:other", open_group=None, usage=USAGE)

        self.assertEqual(sum(row.kind == ROW_STRATEGY and row.item.title == "Gamma 1.9" for row in self._rows(widget)), 1)


class SearchAndFilterTests(_WidgetCase):
    def test_search_shows_everything_found_and_updates_summary(self) -> None:
        widget = self._widget(open_group="")

        widget._search.setText("beta v1")

        found = [row.strategy_id for row in self._rows(widget) if row.kind == ROW_STRATEGY]
        self.assertEqual(sorted(found), sorted(key for key, entry in _entries().items() if "beta v1" in entry.name.lower()))
        self.assertEqual(widget._toolbar.summary.text(), f"{len(found)} из {len(_entries())}")

        widget._search.clear()
        self.assertEqual(self._open_groups(widget), [])

    def test_quick_filter_button_narrows_the_list(self) -> None:
        widget = self._widget(states={"split-02": ProfileStrategyState(rating="work")})

        widget._toolbar.filter_buttons[FILTER_WORKS].click()

        self.assertEqual([row.strategy_id for row in self._rows(widget) if row.kind == ROW_STRATEGY], ["split-02"])
        self.assertTrue(widget._toolbar.filter_buttons[FILTER_WORKS].isChecked())
        self.assertFalse(widget._toolbar.filter_buttons["all"].isChecked())

    def test_enter_in_search_chooses_current_row_and_escape_clears(self) -> None:
        widget = self._widget()
        spy = QSignalSpy(widget.strategy_activated)
        widget._search.setText("beta v11")

        QTest.keyClick(widget._search, Qt.Key.Key_Down)
        QTest.keyClick(widget._search, Qt.Key.Key_Return)
        self.assertEqual([list(call) for call in spy], [["split-11"]])

        QTest.keyClick(widget._search, Qt.Key.Key_Escape)
        self.assertEqual(widget._search.text(), "")

    def test_grouping_choice_is_reported_and_survives_later_payloads(self) -> None:
        widget = self._widget()
        spy = QSignalSpy(widget.grouping_changed)

        widget._grouping_combo.setCurrentIndex(1)
        self.assertEqual([list(call) for call in spy], [["series"]])

        widget.set_rows(entries=widget._entries, states={}, current_strategy_id="fake-05", grouping="method")
        self.assertEqual(widget._plan.grouping, "series")


class TryPanelTests(_WidgetCase):
    def test_panel_names_current_strategy_and_the_next_one(self) -> None:
        widget = self._widget(current="fake-05")

        self.assertFalse(widget._try_panel.isHidden())
        self.assertIn("Alpha v5", widget._try_panel.title.text())
        self.assertIn("подделка", widget._try_panel.title.text())
        self.assertIn("Проверено 0 из 3 советуемых", widget._try_panel.hint.text())
        self.assertIn("Следующая: Beta v3.", widget._try_panel.hint.text())

    def test_fails_button_rates_current_and_switches_to_next_in_queue(self) -> None:
        widget = self._widget(current="fake-05")
        ratings = QSignalSpy(widget.strategy_rating_requested)
        activated = QSignalSpy(widget.strategy_activated)

        widget._try_panel.fails_button.click()

        self.assertEqual([list(call) for call in ratings], [["fake-05", "notwork"]])
        self.assertEqual([list(call) for call in activated], [["split-03"]])

    def test_works_button_only_rates(self) -> None:
        widget = self._widget(current="fake-05")
        ratings = QSignalSpy(widget.strategy_rating_requested)
        activated = QSignalSpy(widget.strategy_activated)

        widget._try_panel.works_button.click()

        self.assertEqual([list(call) for call in ratings], [["fake-05", "work"]])
        self.assertEqual(len(activated), 0)

    def test_when_recommended_are_over_the_queue_goes_on_through_the_catalog(self) -> None:
        states = {key: ProfileStrategyState(rating="notwork") for key in ("split-03", "fake-01")}
        widget = self._widget(current="fake-05", states=states)
        activated = QSignalSpy(widget.strategy_activated)

        self.assertEqual(widget._try_panel.fails_button.text(), "Не работает — следующая")
        self.assertIn("Проверено 2 из 3 советуемых", widget._try_panel.hint.text())
        widget._try_panel.fails_button.click()
        self.assertEqual(len(activated), 1)
        self.assertNotIn(activated[0][0], ("fake-05", "split-03", "fake-01"))

        states["fake-05"] = ProfileStrategyState(rating="notwork")
        widget.set_rows(entries=widget._entries, states=states, current_strategy_id=activated[0][0])
        self.assertIn("Советуемые проверены, дальше идут остальные: 3 из 38.", widget._try_panel.hint.text())

    def test_queue_end_leaves_plain_fails_button(self) -> None:
        states = {key: ProfileStrategyState(rating="notwork") for key in _entries() if key != "fake-05"}
        widget = self._widget(current="fake-05", states=states)
        activated = QSignalSpy(widget.strategy_activated)

        self.assertEqual(widget._try_panel.fails_button.text(), "Не работает")
        widget._try_panel.fails_button.click()
        self.assertEqual(len(activated), 0)

    def test_panel_is_hidden_without_a_catalog_strategy(self) -> None:
        self.assertTrue(self._widget(current="custom")._try_panel.isHidden())


class KeyboardAndMenuTests(_WidgetCase):
    def test_arrows_skip_section_rows_and_enter_chooses(self) -> None:
        widget = self._widget(current="fake-00", usage={})
        widget._list.setFocus()
        spy = QSignalSpy(widget.strategy_activated)
        self.assertIn(ROW_SECTION, {row.kind for row in self._rows(widget)})

        visited = [widget._list.current_row()]
        for _ in range(20):
            QTest.keyClick(widget._list, Qt.Key.Key_Down)
            visited.append(widget._list.current_row())
            if visited[-1].section.title == "Omega":
                break
        row = widget._list.current_row()
        self.assertNotIn(ROW_SECTION, {item.kind for item in visited})
        # Стрелка вниз прошла из первой серии во вторую мимо подзаголовка.
        self.assertEqual([item.section.title for item in (visited[0], visited[-1])], ["Alpha", "Omega"])
        self.assertEqual(row.kind, ROW_STRATEGY)
        QTest.keyClick(widget._list, Qt.Key.Key_Return)

        self.assertEqual([list(call) for call in spy], [[row.strategy_id]])

    def test_left_and_right_collapse_and_expand_group_header(self) -> None:
        widget = self._widget(current="split-00", usage={})
        widget._list.setFocus()
        widget._list.set_current_key("g:split")

        QTest.keyClick(widget._list, Qt.Key.Key_Left)
        self.assertEqual(self._open_groups(widget), [])
        self.assertEqual(widget._list.current_row().key, "g:split")
        QTest.keyClick(widget._list, Qt.Key.Key_Right)
        self.assertEqual(self._open_groups(widget), ["split"])

    def test_context_menu_choice_becomes_a_request_to_the_page(self) -> None:
        widget = self._widget()
        ratings = QSignalSpy(widget.strategy_rating_requested)
        favorites = QSignalSpy(widget.strategy_favorite_requested)

        with patch.object(widget_module, "show_strategy_context_menu", return_value=("rating", "work")) as menu:
            widget._show_strategy_menu("fake-01", QPoint(0, 0))
        self.assertEqual(menu.call_args.kwargs["strategy_name"], "Alpha v1")
        with patch.object(widget_module, "show_strategy_context_menu", return_value=("favorite", True)):
            widget._show_strategy_menu("fake-01", QPoint(0, 0))

        self.assertEqual([list(call) for call in ratings], [["fake-01", "work"]])
        self.assertEqual([list(call) for call in favorites], [["fake-01", True]])

    def test_tooltip_explains_status_icon_and_usage(self) -> None:
        widget = self._widget(states={"fake-05": ProfileStrategyState(rating="work", favorite=True)})
        row = next(row for row in self._rows(widget) if row.strategy_id == "fake-05")
        text = strategy_tooltip(row)

        self.assertIn("Эта стратегия выбрана для профиля.", text)
        self.assertIn("Зелёная галочка на значке", text)
        self.assertIn("Звезда", text)
        self.assertIn("Стоит на этом сервисе в 9 готовых пресетах.", text)
        self.assertIn("--lua-desync=fake:blob=x", text)


class LayoutAndAccessibilityTests(_WidgetCase):
    def test_wide_list_uses_tiles_and_narrow_list_single_rows(self) -> None:
        wide = self._widget(width=1300)
        narrow = self._widget(width=600)

        self.assertEqual(wide._list.column_count(), 3)
        self.assertEqual(narrow._list.column_count(), 1)
        self.assertGreater(wide._list.row_size(ROW_STRATEGY).height(), narrow._list.row_size(ROW_STRATEGY).height())
        self.assertEqual(wide._list.row_size(ROW_GROUP).width(), wide._list.viewport().width())

    def test_widget_paints_without_errors_in_both_layouts(self) -> None:
        for width in (1300, 600):
            widget = self._widget(width=width, states={"fake-01": ProfileStrategyState(rating="notwork", favorite=True)})
            self.assertFalse(widget.grab().toImage().isNull())

    def test_controls_are_named_for_screen_reader(self) -> None:
        widget = self._widget()

        self.assertEqual(widget._list.accessibleName(), "Список готовых стратегий: показано 38 из 38")
        self.assertEqual(widget._search.accessibleName(), "Поиск готовых стратегий")
        self.assertEqual(widget._grouping_combo.accessibleName(), "Группировка готовых стратегий")
        self.assertEqual(widget._toolbar.filter_buttons["works"].accessibleName(), "Отбор стратегий: Работают у меня")
        header = next(row for row in self._rows(widget) if row.kind == ROW_GROUP)
        index = widget._list.list_model().index(0, 0)
        self.assertEqual(header.key, "g:recommended")
        self.assertIn("Группа: Советуем для этого сервиса, стратегий: 3, раскрыта", index.data(Qt.ItemDataRole.AccessibleTextRole))

    def test_onboarding_targets_point_at_panel_and_toolbar(self) -> None:
        widget = self._widget()
        without_panel = self._widget(current="custom")

        self.assertIs(widget.onboarding_target("strategy_try"), widget._try_panel)
        self.assertIs(widget.onboarding_target("strategy_find"), widget._toolbar)
        self.assertIsNone(without_panel.onboarding_target("strategy_try"))

    def test_strategy_icons_are_painted_without_icon_fonts_and_cached(self) -> None:
        """Значок плитки не зависит от шрифтов значков, которые есть не в каждой сборке."""
        import inspect

        from profile.ui.strategy_list import icons

        source = inspect.getsource(icons)
        self.assertNotIn("qtawesome", source)
        self.assertNotIn("qta", source)
        first = icons.strategy_icon("fake_split", "#6fb8ff", "work", 28, 1.0, "#2d2d2d")
        self.assertFalse(first.isNull())
        self.assertIs(icons.strategy_icon("fake_split", "#6fb8ff", "work", 28, 1.0, "#2d2d2d"), first)
        plain = icons.strategy_icon("fake_split", "#6fb8ff", "", 28, 1.0, "#2d2d2d")
        other = icons.strategy_icon("host", "#6fb8ff", "", 28, 1.0, "#2d2d2d")
        self.assertNotEqual(plain.toImage(), first.toImage())
        self.assertNotEqual(plain.toImage(), other.toImage())
        self.assertNotEqual(icons.strategy_icon("unknown-family", "#6fb8ff", "", 28, 1.0, "#2d2d2d").toImage(), plain.toImage())

    def test_one_frame_asks_theme_once_not_per_tile(self) -> None:
        from profile.ui.strategy_list import delegate as delegate_module

        widget = self._widget()
        with patch.object(delegate_module, "_build_style", wraps=delegate_module._build_style) as build:
            widget._list.viewport().grab()

        self.assertGreater(sum(row.kind == ROW_STRATEGY for row in self._rows(widget)), 2)
        self.assertEqual(build.call_count, 1)

    def test_widget_has_no_background_worker_and_one_refresh_path(self) -> None:
        import inspect

        source = inspect.getsource(widget_module)
        self.assertNotIn("QThread", source)
        self.assertNotIn("QTimer", source)
        self.assertEqual(source.count("list_model().set_rows("), 1)


if __name__ == "__main__":
    unittest.main()
