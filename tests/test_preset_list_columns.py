from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem

from ui.presets_menu.common import (
    PRESET_COLUMN_MAX_COUNT,
    PRESET_TILE_MAX_WIDTH,
    PRESET_TILE_MIN_WIDTH,
    preset_columns_for_width,
    preset_full_row_width,
)
from ui.presets_menu.delegate import PresetListDelegate
from ui.presets_menu.model import PresetListModel
from ui.presets_menu.view import LinkedWheelListView


# Имена короткие, плитка минимальной ширины: три столбца в широком списке и один в узком.
WIDE = 3 * PRESET_TILE_MIN_WIDTH + 40
NARROW = 2 * PRESET_TILE_MIN_WIDTH - 20


def _preset(file_name: str, folder_key: str) -> dict[str, object]:
    return {"kind": "preset", "file_name": file_name, "name": file_name[:-4], "folder_key": folder_key, "depth": 1}


def _rows() -> list[dict[str, object]]:
    return [
        {"kind": "folder", "folder_key": "all", "name": "ALL", "is_collapsed": False, "count": 5},
        *[_preset(f"a{number}.txt", "all") for number in range(1, 6)],
        {"kind": "folder", "folder_key": "common", "name": "Общие", "is_collapsed": False, "count": 2},
        *[_preset(f"c{number}.txt", "common") for number in range(1, 3)],
    ]


class PresetColumnMathTests(unittest.TestCase):
    def test_list_narrower_than_two_tiles_keeps_one_column(self) -> None:
        self.assertEqual(preset_columns_for_width(0, 200), (1, 0))
        self.assertEqual(preset_columns_for_width(400, 200), (1, 399))

    def test_columns_are_as_many_as_tiles_fit_and_share_the_leftover(self) -> None:
        self.assertEqual(preset_columns_for_width(401, 200), (2, 200))
        count, width = preset_columns_for_width(1391, 250)
        self.assertEqual((count, width), (5, 278))
        # Столбцы вместе не дотягиваются до края: иначе QListView перенёс бы последний.
        self.assertLess(count * width, 1391)

    def test_wanted_tile_width_is_kept_within_sane_bounds(self) -> None:
        self.assertEqual(preset_columns_for_width(1001, 10), preset_columns_for_width(1001, PRESET_TILE_MIN_WIDTH))
        self.assertEqual(preset_columns_for_width(1001, 9000), preset_columns_for_width(1001, PRESET_TILE_MAX_WIDTH))

    def test_column_count_is_capped(self) -> None:
        self.assertEqual(preset_columns_for_width(10000, PRESET_TILE_MIN_WIDTH)[0], PRESET_COLUMN_MAX_COUNT)

    def test_full_row_is_one_point_narrower_than_the_list(self) -> None:
        self.assertEqual(preset_full_row_width(900), 899)
        self.assertEqual(preset_full_row_width(0), 0)


class PresetListColumnsTests(unittest.TestCase):
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
        self.actions: list[tuple[str, str]] = []
        self.delegate.action_triggered.connect(lambda action, name: self.actions.append((action, name)))

    def _show(self, width: int) -> None:
        self.view.resize(width, 600)
        self.view.show()
        QApplication.processEvents()
        self.view.doItemsLayout()

    def _rect(self, row: int):
        return self.view.visualRect(self.model.index(row, 0))

    def _file(self, index) -> str:
        return str(index.data(PresetListModel.FileNameRole) or "")

    def test_wide_list_puts_presets_side_by_side_under_a_full_width_header(self) -> None:
        self._show(WIDE)
        self.assertEqual(self.view.preset_column_count(), 3)

        header = self._rect(0)
        first, second, third, fourth = (self._rect(row) for row in range(1, 5))
        self.assertEqual(header.left(), 0)
        self.assertEqual(header.width(), preset_full_row_width(self.view.viewport().width()))
        self.assertGreaterEqual(first.top(), header.bottom())
        self.assertEqual(first.top(), second.top())
        self.assertEqual(second.top(), third.top())
        self.assertLess(first.left(), second.left())
        self.assertLess(second.left(), third.left())
        # Четвёртый пресет не поместился в линию и начал следующую.
        self.assertGreater(fourth.top(), first.top())
        self.assertEqual(fourth.left(), first.left())
        # Следующая папка начинается с новой линии, а не встаёт в свободный столбец.
        next_header = self._rect(6)
        self.assertEqual(next_header.left(), 0)
        self.assertGreater(next_header.top(), self._rect(5).top())

    def test_narrow_list_stays_a_plain_list(self) -> None:
        self._show(NARROW)
        self.assertEqual(self.view.preset_column_count(), 1)
        tops = [self._rect(row).top() for row in range(self.model.rowCount())]
        self.assertEqual(tops, sorted(set(tops)))
        self.assertEqual({self._rect(row).left() for row in range(self.model.rowCount())}, {0})

    def test_columns_follow_the_list_width(self) -> None:
        self._show(WIDE)
        self.assertEqual(self._rect(1).top(), self._rect(2).top())
        self._show(NARROW)
        self.assertGreater(self._rect(2).top(), self._rect(1).top())

    def test_drop_side_is_chosen_by_left_or_right_half_in_columns(self) -> None:
        self._show(WIDE)
        rect = self._rect(2)
        left_target, left_id, _folder = self.view._drop_target_at(QPoint(rect.left() + 10, rect.center().y()))
        self.assertEqual(left_target["destination_kind"], "preset")
        self.assertEqual(left_id, "a2.txt")
        # Правая половина — «после a2», что то же самое, что «перед a3».
        right_target, right_id, _folder = self.view._drop_target_at(QPoint(rect.right() - 10, rect.center().y()))
        self.assertEqual(right_target["destination_kind"], "preset")
        self.assertEqual(right_id, "a3.txt")
        # У последнего пресета папки соседа нет: остаётся «после него».
        last = self._rect(5)
        last_target, last_id, _folder = self.view._drop_target_at(QPoint(last.right() - 10, last.center().y()))
        self.assertEqual(last_target["destination_kind"], "preset_after")
        self.assertEqual(last_id, "a5.txt")

    def test_drop_side_is_chosen_by_upper_or_lower_half_in_one_column(self) -> None:
        self._show(NARROW)
        rect = self._rect(2)
        upper, upper_id, _folder = self.view._drop_target_at(QPoint(rect.right() - 10, rect.top() + 3))
        self.assertEqual((upper["destination_kind"], upper_id), ("preset", "a2.txt"))
        lower, lower_id, _folder = self.view._drop_target_at(QPoint(rect.left() + 10, rect.bottom() - 3))
        self.assertEqual((lower["destination_kind"], lower_id), ("preset", "a3.txt"))

    def _press(self, key: Qt.Key) -> None:
        self.view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))

    def test_arrows_walk_the_grid_in_columns(self) -> None:
        self._show(WIDE)
        self.view.setCurrentIndex(self.model.index(2, 0))  # a2: первая линия, второй столбец

        self._press(Qt.Key.Key_Right)
        self.assertEqual(self._file(self.view.currentIndex()), "a3.txt")
        self._press(Qt.Key.Key_Left)
        self._press(Qt.Key.Key_Down)
        self.assertEqual(self._file(self.view.currentIndex()), "a5.txt")
        # Ниже — следующая папка, через её заголовок стрелка перешагивает.
        self._press(Qt.Key.Key_Down)
        self.assertEqual(self._file(self.view.currentIndex()), "c2.txt")
        self._press(Qt.Key.Key_Down)
        self.assertEqual(self._file(self.view.currentIndex()), "c2.txt")
        self._press(Qt.Key.Key_Up)
        self._press(Qt.Key.Key_Up)
        self.assertEqual(self._file(self.view.currentIndex()), "a2.txt")

    def test_arrows_keep_list_order_in_one_column(self) -> None:
        self._show(NARROW)
        self.view.setCurrentIndex(self.model.index(5, 0))
        self._press(Qt.Key.Key_Down)
        self.assertEqual(self._file(self.view.currentIndex()), "c1.txt")
        self._press(Qt.Key.Key_Up)
        self.assertEqual(self._file(self.view.currentIndex()), "a5.txt")

    def _click(self, row: int, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        index = self.model.index(row, 0)
        option = QStyleOptionViewItem()
        option.rect = self._rect(row)
        # Середина строки: мимо булавки слева и кнопок справа.
        point = QPointF(option.rect.center())
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            point,
            point,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            modifiers,
        )
        self.assertTrue(self.delegate.editorEvent(event, self.model, option, index))

    def test_single_click_activates_after_the_double_click_wait(self) -> None:
        self._show(WIDE)
        self._click(2)
        self.assertEqual(self.actions, [])
        self.assertTrue(self.delegate._activation_timer.isActive())
        self.delegate._activation_timer.stop()
        self.delegate._emit_pending_activation()
        self.assertEqual(self.actions, [("activate", "a2.txt")])

    def test_double_click_opens_the_preset_without_activating_it(self) -> None:
        self._show(WIDE)
        self._click(2)
        self._click(2)
        self.assertEqual(self.actions, [("open", "a2.txt")])
        self.assertFalse(self.delegate._activation_timer.isActive())

    def test_shift_click_opens_the_preset_without_activating_it(self) -> None:
        self._show(WIDE)
        self._click(2, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.actions, [("open", "a2.txt")])
        self.assertFalse(self.delegate._activation_timer.isActive())

    def test_quick_clicks_on_two_presets_activate_only_the_last_one(self) -> None:
        self._show(WIDE)
        self._click(2)
        self._click(3)
        self.delegate._activation_timer.stop()
        self.delegate._emit_pending_activation()
        self.assertEqual(self.actions, [("activate", "a3.txt")])


if __name__ == "__main__":
    unittest.main()
