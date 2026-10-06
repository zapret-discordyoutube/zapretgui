from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QFontMetrics, QImage, QPainter
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from ui.presets_menu import delegate as delegate_module
from ui.presets_menu.delegate import PresetListDelegate
from ui.presets_menu.model import PresetListModel, repeated_folder_prefix_length
from ui.presets_menu.view import LinkedWheelListView


DATE = "05.10.2026 17:46"


def _rows() -> list[dict[str, object]]:
    return [
        {"kind": "folder", "folder_key": "pinned", "name": "Закрепленные", "is_collapsed": False, "count": 1},
        {
            "kind": "preset",
            "file_name": "pinned.txt",
            "name": "ALL TCP & UDP v1",
            "folder_key": "all",
            "is_pinned": True,
            "date": DATE,
        },
        {"kind": "folder", "folder_key": "all", "name": "ALL TCP & UDP", "is_collapsed": False, "count": 2},
        {"kind": "preset", "file_name": "a.txt", "name": "ALL TCP & UDP multisplit_sni", "folder_key": "all", "date": DATE},
        {
            "kind": "preset",
            "file_name": "b.txt",
            "name": "Default v1 (game filter)",
            "folder_key": "all",
            "date": DATE,
            "rating": 4,
        },
    ]


class RepeatedFolderPrefixTests(unittest.TestCase):
    def test_prefix_is_the_folder_name_with_its_space(self) -> None:
        self.assertEqual(repeated_folder_prefix_length("ALL TCP & UDP v3_1", "ALL TCP & UDP"), 14)
        self.assertEqual(repeated_folder_prefix_length("all tcp & udp v3_1", "ALL TCP & UDP"), 14)

    def test_no_prefix_when_name_only_looks_similar(self) -> None:
        self.assertEqual(repeated_folder_prefix_length("ALL TCP & UDPv2", "ALL TCP & UDP"), 0)
        self.assertEqual(repeated_folder_prefix_length("ALL TCP & UDP", "ALL TCP & UDP"), 0)
        self.assertEqual(repeated_folder_prefix_length("ALL TCP & UDP ", "ALL TCP & UDP"), 0)
        self.assertEqual(repeated_folder_prefix_length("general ALT10 1.10.3", "1.10.3"), 0)
        self.assertEqual(repeated_folder_prefix_length("Default v1", ""), 0)

    def test_model_compares_with_the_header_shown_above_the_row(self) -> None:
        model = PresetListModel()
        model.set_rows(_rows())
        role = PresetListModel.RepeatedPrefixLengthRole

        # Закреплённый пресет стоит под «Закрепленные»: повтора над ним нет.
        self.assertEqual(model.index(1, 0).data(role), 0)
        self.assertEqual(model.index(3, 0).data(role), 14)
        self.assertEqual(model.index(4, 0).data(role), 0)
        self.assertEqual(model.index(2, 0).data(role), 0)


class _SpyMetrics(QFontMetrics):
    elided: list[str] = []

    def elidedText(self, text, mode, width, flags=0):  # noqa: N802
        type(self).elided.append(str(text))
        return super().elidedText(text, mode, width, flags)


class PresetRowQuietActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.view = LinkedWheelListView()
        self.addCleanup(self.view.deleteLater)
        self.model = PresetListModel(self.view)
        self.model.set_rows(_rows())
        self.view.setModel(self.model)
        self.delegate = PresetListDelegate(self.view)
        self.view.setItemDelegate(self.delegate)

    def _paint(self, row: int, state: QStyle.StateFlag | None = None) -> tuple[list[str], list[str]]:
        """Рисует строку и возвращает (нарисованные значки, тексты, прошедшие через сокращение)."""
        index = self.model.index(row, 0)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 900, self.delegate.sizeHint(option, index).height())
        option.state = QStyle.StateFlag.State_Enabled
        if state is not None:
            option.state |= state
        image = QImage(900, 60, QImage.Format.Format_ARGB32_Premultiplied)
        icons: list[str] = []
        real_cached_icon = delegate_module.cached_icon

        def spy_icon(name: str, color: str):
            icons.append(name)
            return real_cached_icon(name, color)

        _SpyMetrics.elided = []
        painter = QPainter(image)
        try:
            with patch.object(delegate_module, "cached_icon", spy_icon), patch.object(
                delegate_module, "QFontMetrics", _SpyMetrics
            ):
                self.delegate.paint(painter, option, index)
        finally:
            painter.end()
        return icons, list(_SpyMetrics.elided)

    def test_row_is_one_line_high(self) -> None:
        self.assertEqual(self.delegate.sizeHint(QStyleOptionViewItem(), self.model.index(3, 0)).height(), 36)

    def test_idle_row_shows_only_icon_and_name(self) -> None:
        icons, texts = self._paint(3)

        self.assertEqual(icons, ["fa5s.file-alt"])
        self.assertNotIn(DATE, texts)

    def test_hovered_row_reveals_date_pin_and_buttons(self) -> None:
        icons, texts = self._paint(3, QStyle.StateFlag.State_MouseOver)

        self.assertIn(DATE, texts)
        self.assertIn("fa5s.thumbtack", icons)
        self.assertIn("fa5s.star-half-alt", icons)
        self.assertIn("fa5s.ellipsis-v", icons)

    def test_keyboard_focus_reveals_the_same_controls(self) -> None:
        icons, texts = self._paint(3, QStyle.StateFlag.State_HasFocus)

        self.assertIn(DATE, texts)
        self.assertIn("fa5s.ellipsis-v", icons)

    def test_pin_of_pinned_preset_and_user_rating_stay_visible(self) -> None:
        pinned_icons, pinned_texts = self._paint(1)
        rated_icons, rated_texts = self._paint(4)

        self.assertIn("fa5s.thumbtack", pinned_icons)
        self.assertNotIn("fa5s.ellipsis-v", pinned_icons)
        self.assertNotIn(DATE, pinned_texts)
        self.assertIn("fa5s.star", rated_icons)
        self.assertNotIn("fa5s.thumbtack", rated_icons)
        self.assertNotIn(DATE, rated_texts)

    def test_hidden_button_place_still_answers_clicks(self) -> None:
        # Кнопки скрыты только на вид: их место занято всегда, и наведённая
        # туда мышь попадает в ту же кнопку.
        row_rect = QRect(0, 0, 900, 36)
        actions = dict(self.delegate._action_rects(row_rect, "preset", False, False))

        self.assertEqual(
            self.delegate._action_at(row_rect, "preset", False, False, 0, actions["edit"].center()),
            "edit",
        )
        self.assertEqual(
            self.delegate._action_at(row_rect, "preset", False, False, 0, actions["rating"].center()),
            "rating",
        )


if __name__ == "__main__":
    unittest.main()
