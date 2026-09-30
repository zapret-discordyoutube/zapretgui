from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import getFont

from hosts.ui.services_tiles import HostsTile, HostsTilesGrid, split_service_title, wrap_lines

YOUTUBE = "YouTube (иногда может не работать с ним! Отключите тумблер если YouTube не работает с пресетами)"
FLOWSEAL = "Решение от Flowseal для стабильной работы голосовых серверов в Discord"


def _tile(name: str, key: str) -> HostsTile:
    title, note = split_service_title(name)
    return HostsTile(kind="tile", key=key, title=title, note=note, has_switch=True)


class HostsTileTextWrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        cls.metrics = QFontMetrics(getFont(12))

    def test_whole_text_is_kept_and_fits_width(self) -> None:
        _title, note = split_service_title(YOUTUBE)

        lines = wrap_lines(note, self.metrics, 180)

        self.assertGreaterEqual(len(lines), 3)
        self.assertEqual(" ".join(lines), note)
        for line in lines:
            self.assertLessEqual(self.metrics.horizontalAdvance(line), 180)

    def test_first_line_can_be_shorter_because_of_switch(self) -> None:
        lines = wrap_lines(FLOWSEAL, self.metrics, 200, first_width=80)

        self.assertLessEqual(self.metrics.horizontalAdvance(lines[0]), 80)
        self.assertEqual(" ".join(lines), FLOWSEAL)

    def test_long_word_breaks_after_slash_and_keeps_spaces(self) -> None:
        self.assertEqual(wrap_lines("x.com / Twitter", self.metrics, 300), ["x.com / Twitter"])
        lines = wrap_lines("загрузки/картинки", self.metrics, self.metrics.horizontalAdvance("загрузки/") + 2)
        self.assertEqual(lines, ["загрузки/", "картинки"])
        self.assertEqual("".join(wrap_lines("а" * 60, self.metrics, 60)), "а" * 60)
        self.assertEqual(wrap_lines("", self.metrics, 200), [])


class HostsTileRowHeightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_row_grows_for_long_text_and_short_rows_stay_compact(self) -> None:
        grid = HostsTilesGrid()
        self.addCleanup(grid.deleteLater)
        grid.resize(820, 100)
        grid.set_tiles([
            _tile("Discord", "a"), _tile(YOUTUBE, "b"), _tile("GitHub", "c"),
            _tile("Rutor", "d"), _tile("WhatsApp (работает обход если есть IPv6)", "e"), _tile("Supercell", "f"),
        ])

        first_row = {grid.tile_rect(key).height() for key in "abc"}
        second_row = {grid.tile_rect(key).height() for key in "def"}
        self.assertEqual(len(first_row), 1)
        self.assertGreater(first_row.pop(), HostsTilesGrid.TILE_HEIGHT)
        self.assertEqual(second_row, {HostsTilesGrid.TILE_HEIGHT})
        self.assertGreater(grid.tile_rect("d").top(), grid.tile_rect("a").bottom())
        self.assertFalse(grid.grab().isNull())


if __name__ == "__main__":
    unittest.main()
