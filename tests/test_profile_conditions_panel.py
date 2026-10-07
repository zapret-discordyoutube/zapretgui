"""Панель «Условия» на странице profile и сводка условий словами."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication, QWidget
from qfluentwidgets import BodyLabel, ComboBox, LineEdit

from profile.conditions_text import conditions_summary, range_hint, range_phrase
from profile.ui.profile_conditions_flyout import (
    RANGE_VALUE_WIDTH,
    ProfileConditionsFlyout,
    ProfileConditionsView,
)


class ConditionsTextTests(unittest.TestCase):
    def test_range_phrase_reads_first_packets(self) -> None:
        self.assertEqual(range_phrase("a"), "все пакеты")
        self.assertEqual(range_phrase("x"), "не обрабатываются")
        self.assertEqual(range_phrase("-d8"), "первые 8 пакетов с данными")
        self.assertEqual(range_phrase("-n1"), "первый пакет")
        self.assertEqual(range_phrase("-n3"), "первые 3 пакета")
        self.assertEqual(range_phrase("-d12"), "первые 12 пакетов с данными")
        self.assertEqual(range_phrase("-d21"), "первый 21 пакет с данными")

    def test_range_phrase_keeps_custom_expression_as_is(self) -> None:
        self.assertEqual(range_phrase("s1<d1"), "s1<d1")
        self.assertEqual(range_phrase(""), "")

    def test_summary_does_not_repeat_protocol(self) -> None:
        text = conditions_summary(
            match_lines=("--filter-tcp=80,443", "--hostlist=lists/steam.txt"),
            match_summary="TCP • TCP 80,443 • hostlist",
            filter_value="lists/steam.txt",
            in_range="x",
            out_range="-d8",
        )

        self.assertEqual(text, "TCP 80,443 · lists/steam.txt · первые 8 пакетов с данными")

    def test_summary_mentions_incoming_only_when_handled(self) -> None:
        text = conditions_summary(
            match_lines=("--filter-udp=443",),
            match_summary="",
            filter_value="",
            in_range="-n2",
            out_range="x",
        )

        self.assertEqual(text, "UDP 443 · исходящие не обрабатываются · входящие: первые 2 пакета")

    def test_summary_without_ranges_for_winws1(self) -> None:
        text = conditions_summary(
            match_lines=(),
            match_summary="TCP 443",
            filter_value="",
            in_range="",
            out_range="",
        )

        self.assertEqual(text, "TCP 443")

    def test_range_hint_asks_for_number(self) -> None:
        self.assertIn("Укажите число", range_hint("d", ""))
        self.assertIn("первые 8 пакетов с данными", range_hint("d", "8"))
        self.assertIn("не трогает", range_hint("x", ""))


class ConditionsViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _view(self):
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        combo = ComboBox()
        for text, data in (("a — всегда", "a"), ("x — никогда", "x"), ("d — первые пакеты с данными", "d")):
            combo.addItem(text, userData=data)
        value = LineEdit()
        filter_combo = ComboBox()
        filter_value = LineEdit()
        view = ProfileConditionsView()
        view.add_filter_section(BodyLabel("Список адресов"), filter_combo, filter_value)
        view.add_range_section(BodyLabel("Пакеты от вас к сайту"), "--out-range", combo, value)
        flyout = ProfileConditionsFlyout(view, parent)
        return view, flyout, combo, value, filter_combo

    def test_value_field_hidden_for_modes_without_number(self) -> None:
        view, _flyout, combo, value, _filter_combo = self._view()

        combo.setCurrentIndex(1)
        view.sync_ranges()
        self.assertTrue(value.isHidden())

        combo.setCurrentIndex(2)
        value.setText("8")
        view.sync_ranges()
        self.assertFalse(value.isHidden())
        self.assertEqual(value.maximumWidth(), RANGE_VALUE_WIDTH)
        self.assertIn("первые 8 пакетов с данными", view._range_rows[0].hint.text())

    def test_sections_follow_profile_capabilities(self) -> None:
        view, _flyout, combo, _value, filter_combo = self._view()

        view.set_sections_visible(filter_visible=False, ranges_visible=True)

        self.assertTrue(filter_combo.parentWidget().isHidden())
        self.assertFalse(combo.parentWidget().isHidden())

    def test_flyout_keeps_page_fields_alive_after_close(self) -> None:
        _view, flyout, combo, _value, _filter_combo = self._view()

        self.assertFalse(flyout.isDeleteOnClose)
        flyout.close()
        self._app.processEvents()

        self.assertEqual(combo.count(), 3)


if __name__ == "__main__":
    unittest.main()
