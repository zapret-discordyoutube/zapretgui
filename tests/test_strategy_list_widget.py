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
from profile.strategy_usage import StrategyPlace, StrategyUsage
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
        # Обычный щелчок применяет стратегию после ожидания второго щелчка — тест не ждёт.
        widget._list.flush_pending_choice()


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
        self.assertTrue(widget._try_panel.isHidden())
        self.assertTrue(widget._toolbar.search_row.isHidden())

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
        chip, _badge = widget._list.itemDelegate().hit_rects(widget._list.visualRect(index), head)
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
    def test_search_is_hidden_until_ctrl_f_and_escape_hides_it_again(self) -> None:
        widget = self._widget()
        self.assertTrue(widget._toolbar.search_row.isHidden())
        self.assertFalse(widget._toolbar.filter_row.isHidden())
        self.assertFalse(widget._grouping_combo.isHidden())

        widget._focus_search()
        self.assertFalse(widget._toolbar.search_row.isHidden())
        widget._search.setText("beta v1")
        narrowed = widget._plan.visible_count

        QTest.keyClick(widget._search, Qt.Key.Key_Escape)
        self.assertTrue(widget._toolbar.search_row.isHidden())
        self.assertEqual(widget._search.text(), "")
        # Спрятанный поиск не сужает список.
        self.assertGreater(widget._plan.visible_count, narrowed)

        widget._focus_search()
        widget._focus_search()
        self.assertTrue(widget._toolbar.search_row.isHidden())

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


class DetailsTests(_WidgetCase):
    PLACES = {
        "fake-05": (
            StrategyPlace(name="YouTube · видео", presets=9, profile_key="profile:3", icon_name="simple:youtube:YT", icon_color="#FF0000"),
            StrategyPlace(name="Discord", presets=2),
        )
    }

    def _with_details(self, **kwargs):
        widget = self._widget(**kwargs)
        widget.set_rows(
            entries=widget._entries,
            states=kwargs.get("states") or {},
            current_strategy_id=kwargs.get("current", "fake-05"),
            places=self.PLACES,
            experience={"fake-05": (2, 1)},
        )
        return widget

    def _labels(self, widget) -> str:
        from PyQt6.QtWidgets import QLabel

        return "\n".join(label.text() for label in widget._details_view.findChildren(QLabel) if not label.isHidden() or label.text())

    def test_click_on_badge_opens_details_and_click_on_name_applies(self) -> None:
        widget = self._with_details(current="split-00")
        opened = QSignalSpy(widget.details_changed)
        applied = QSignalSpy(widget.strategy_activated)
        widget._on_group_toggle("recommended", True)
        row = next(row for row in self._rows(widget) if row.strategy_id == "fake-05")
        model = widget._list.list_model()
        index = model.index(model.row_of_key(row.key), 0)
        _chip, badge = widget._list.itemDelegate().hit_rects(widget._list.visualRect(index), row)
        self.assertGreater(badge.width(), 0)

        self._click_row(widget, row.key, at=badge.center())
        self.assertEqual([list(call) for call in opened], [["Alpha v5"]])
        self.assertEqual(len(applied), 0)
        self.assertTrue(widget.details_open())
        self.assertIs(widget._pages.currentWidget(), widget._details_view)

        widget.close_details()
        self.assertEqual(list(opened[-1]), [""])
        self.assertEqual(widget._list.current_row().strategy_id, "fake-05")
        self._click_row(widget, row.key)
        self.assertEqual([list(call) for call in applied], [["fake-05"]])

    def test_details_tell_what_strategy_does_where_it_is_used_and_personal_experience(self) -> None:
        widget = self._with_details()
        widget.show_details("fake-05")
        text = self._labels(widget)

        self.assertIn("Alpha v5", text)
        self.assertIn("1. Подделка", text)
        self.assertIn("Перед вашим настоящим запросом программа отправляет ещё один — поддельный", text)
        self.assertIn("Фильтр провайдера первым читает подделку", text)
        self.assertIn("В подделке: x", text)
        # У подделки нет защиты — предупреждение стоит отдельной строкой.
        self.assertIn("У подделки нет защиты от сайта", text)
        self.assertIn("YouTube · видео", text)
        self.assertIn("в 9 готовых пресетах · открыть", text)
        self.assertIn("Слева вы, посередине проверка у провайдера, справа сайт.", text)
        self.assertEqual(widget._details_view._illustration.scene_key(), "fake")
        self.assertIn("На других профилях", text)
        self.assertIn("работает — 2, не работает — 1", text)
        self.assertIn("--lua-desync=fake:blob=x", text)
        self.assertEqual(widget._details_view._apply_button.text(), "Выбрана")
        self.assertFalse(widget._details_view._apply_button.isEnabled())

    def _press(self, widget, key: str, *, modifier=Qt.KeyboardModifier.NoModifier, double: bool = False) -> None:
        model = widget._list.list_model()
        index = model.index(model.row_of_key(key), 0)
        widget._list.scrollTo(index)
        rect = widget._list.visualRect(index)
        point = QPoint(rect.left() + 60, rect.center().y())
        if double:
            QTest.mouseDClick(widget._list.viewport(), Qt.MouseButton.LeftButton, modifier, point)
        else:
            QTest.mouseClick(widget._list.viewport(), Qt.MouseButton.LeftButton, modifier, point)

    def test_shift_click_and_double_click_open_details_without_applying(self) -> None:
        widget = self._with_details(current="split-00")
        widget._on_group_toggle("recommended", True)
        applied = QSignalSpy(widget.strategy_activated)

        self._press(widget, "i:fake-05", modifier=Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(widget._details_view.strategy_id(), "fake-05")
        widget.close_details()

        self._press(widget, "i:fake-01", double=True)
        self.assertEqual(widget._details_view.strategy_id(), "fake-01")
        widget._list.flush_pending_choice()
        self.assertEqual(len(applied), 0)

    def test_single_click_applies_only_after_waiting_for_second_click(self) -> None:
        widget = self._with_details(current="split-00")
        widget._on_group_toggle("recommended", True)
        applied = QSignalSpy(widget.strategy_activated)

        self._press(widget, "i:fake-05")
        self.assertEqual(len(applied), 0)
        widget._list.flush_pending_choice()
        self.assertEqual([list(call) for call in applied], [["fake-05"]])
        self.assertFalse(widget.details_open())

    def test_blocks_take_only_the_height_their_content_needs(self) -> None:
        """Блок без сервисов — одна строка текста, а не полэкрана пустоты; строки
        «Ваш опыт» прижаты к заголовку, даже когда соседняя карточка выше."""
        widget = self._with_details()
        view = widget._details_view

        widget.show_details("fake-05")
        self._app.processEvents()
        with_places = view._places_card.height()
        widget.show_details("fake-01")
        self._app.processEvents()

        self.assertIsNone(view._places_host)
        self.assertLess(view._places_card.height(), 90)
        self.assertLess(view._places_card.height(), with_places)
        rows = [child for child in view._experience_card.children() if type(child).__name__ == "_Row"]
        self.assertLess(rows[0].y(), 60)
        self.assertEqual(view._experience_card.height(), view._facts_card.height())

    def test_service_card_opens_its_profile_only_when_preset_has_one(self) -> None:
        from profile.ui.strategy_list.details import _PlaceCard

        widget = self._with_details()
        widget.show_details("fake-05")
        chosen = QSignalSpy(widget.profile_chosen)
        cards = {card.place.name: card for card in widget._details_view.findChildren(_PlaceCard)}

        cards["Discord"].clicked.emit()
        self.assertEqual(len(chosen), 0)
        cards["YouTube · видео"].clicked.emit()
        self.assertEqual([list(call) for call in chosen], [["profile:3"]])

    def test_click_on_step_switches_the_animated_scheme(self) -> None:
        entries = _entries()
        entries["two-steps"] = _entry("Two steps", "--lua-desync=fake:blob=x\n--lua-desync=multidisorder:pos=2\n--lua-desync=wssize:wsize=1")
        widget = self._widget(entries=entries)
        widget.show_details("two-steps")
        view = widget._details_view

        self.assertEqual([row.step.scene for row in view._step_rows], ["fake", "multidisorder", ""])
        self.assertEqual(view._illustration.scene_key(), "fake")
        # Кнопка «показать на схеме» есть у шагов со схемой и нет у шага без неё.
        self.assertEqual([row.scene_button is not None for row in view._step_rows], [True, True, False])
        view._step_rows[1].scene_button.click()
        self.assertEqual(view._illustration.scene_key(), "multidisorder")
        self.assertIn("Шаг 2: перестановка.", view._scene_caption.text())

    def test_single_scheme_has_no_show_button_and_steps_are_not_nested_cards(self) -> None:
        from qfluentwidgets import CardWidget

        widget = self._with_details()
        widget.show_details("fake-05")
        view = widget._details_view

        self.assertEqual([row.scene_button for row in view._step_rows], [None])
        self.assertFalse(isinstance(view._step_rows[0], CardWidget))
        self.assertNotIn("Шаг 1", view._scene_caption.text())
        # Настройка шага — короткая метка, а пояснение к ней — в подсказке.
        chips = [chip for chip in view._step_rows[0].findChildren(QWidget) if chip.objectName() == "strategyChip"]
        self.assertEqual([chip.accessibleName() for chip in chips], ["В подделке: x"])
        self.assertEqual(chips[0].accessibleDescription(), "Что в подделке.")

    def test_details_buttons_ask_the_page_and_follow_new_state(self) -> None:
        widget = self._with_details()
        widget.show_details("fake-01")
        ratings = QSignalSpy(widget.strategy_rating_requested)
        favorites = QSignalSpy(widget.strategy_favorite_requested)
        applied = QSignalSpy(widget.strategy_activated)

        widget._details_view._works_button.click()
        widget._details_view._favorite_button.click()
        widget._details_view._apply_button.click()
        self.assertEqual([list(call) for call in ratings], [["fake-01", "work"]])
        self.assertEqual([list(call) for call in favorites], [["fake-01", True]])
        self.assertEqual([list(call) for call in applied], [["fake-01"]])

        widget.set_rows(
            entries=widget._entries,
            states={"fake-01": ProfileStrategyState(rating="work", favorite=True)},
            current_strategy_id="fake-01",
        )
        self.assertEqual(widget._details_view._works_button.text(), "Снять «Работает»")
        self.assertEqual(widget._details_view._favorite_button.text(), "Убрать из избранного")
        self.assertEqual(widget._details_view._apply_button.text(), "Выбрана")
        widget._details_view._works_button.click()
        self.assertEqual(list(ratings[-1]), ["fake-01", ""])

    def test_another_profile_closes_details(self) -> None:
        widget = self._with_details()
        widget.show_details("fake-05")

        widget.set_rows(entries=widget._entries, states={}, current_strategy_id="fake-05", open_group_token="uid:other", open_group=None)

        self.assertFalse(widget.details_open())
        self.assertEqual(widget._pages.currentIndex(), 0)

    def test_f1_and_context_menu_open_details(self) -> None:
        widget = self._with_details()
        widget._list.setFocus()
        widget._list.set_current_key("i:fake-05")

        QTest.keyClick(widget._list, Qt.Key.Key_F1)
        self.assertEqual(widget._details_view.strategy_id(), "fake-05")
        widget.close_details()
        with patch.object(widget_module, "show_strategy_context_menu", return_value=("details", True)):
            widget._show_strategy_menu("fake-01", QPoint(0, 0))
        self.assertEqual(widget._details_view.strategy_id(), "fake-01")


class LearningTests(_WidgetCase):
    def test_strategy_that_worked_on_other_profiles_gets_personal_badge_and_goes_first(self) -> None:
        widget = self._widget(current="fake-05", usage={})
        widget.set_rows(entries=widget._entries, states={}, current_strategy_id="fake-05", experience={"fake-12": (2, 0), "fake-03": (0, 3)})
        fake = [row for row in self._rows(widget) if row.kind == ROW_STRATEGY and row.group_key == "fake"]
        omega = [row for row in fake if row.section.title == "Omega"]
        alpha = [row for row in fake if row.section.title == "Alpha"]

        # Внутри своей серии помогавшая стратегия первая, подводившая — последняя.
        self.assertEqual(omega[0].strategy_id, "fake-12")
        self.assertEqual((omega[0].item.badge_text, omega[0].item.badge_tone), ("у вас работает · 2", "personal"))
        self.assertEqual(alpha[-1].strategy_id, "fake-03")
        self.assertEqual(widget._plan.queue[0], "fake-12")
        self.assertEqual(widget._plan.queue[-1], "fake-03")
        self.assertIn("Вы отметили её рабочей на 2 других профилях.", omega[0].item.tooltip)


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
