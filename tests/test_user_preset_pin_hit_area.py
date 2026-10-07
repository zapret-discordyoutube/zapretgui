from __future__ import annotations

import unittest

from PyQt6.QtCore import QPoint, QRect

from ui.presets_menu.delegate import PresetListDelegate


class UserPresetPinHitAreaTests(unittest.TestCase):
    def test_pin_and_menu_buttons_sit_at_the_right_edge_of_the_tile(self) -> None:
        delegate = PresetListDelegate.__new__(PresetListDelegate)
        row_rect = QRect(100, 0, 240, 32)
        actions = dict(PresetListDelegate._action_rects(delegate, row_rect))

        self.assertEqual(list(actions), ["pin", "edit"])
        self.assertLess(actions["pin"].right(), actions["edit"].left())
        self.assertLessEqual(actions["edit"].right(), row_rect.right())
        for action, rect in actions.items():
            # Кнопка не мельче 24 точек: в неё легко попасть мышью.
            self.assertGreaterEqual(rect.width(), 24)
            self.assertEqual(PresetListDelegate._action_at(delegate, row_rect, rect.center()), action)

    def test_click_on_the_name_is_not_a_button(self) -> None:
        delegate = PresetListDelegate.__new__(PresetListDelegate)
        row_rect = QRect(100, 0, 240, 32)

        self.assertIsNone(PresetListDelegate._action_at(delegate, row_rect, QPoint(140, 16)))


if __name__ == "__main__":
    unittest.main()
