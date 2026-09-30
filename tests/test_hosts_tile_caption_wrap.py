from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import getFont

from hosts.ui.services_tiles import wrap_two_lines

YOUTUBE_NOTE = "иногда может не работать с ним! Отключите тумблер если YouTube не работает с пресетами"


class HostsTileCaptionWrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        cls.metrics = QFontMetrics(getFont(12))

    def test_long_note_takes_two_lines_split_by_words(self) -> None:
        width = 220

        lines = wrap_two_lines(YOUTUBE_NOTE, self.metrics, width)

        self.assertEqual(len(lines), 2)
        self.assertTrue(YOUTUBE_NOTE.startswith(lines[0]))
        self.assertFalse(lines[0].endswith("…"))
        self.assertTrue(lines[1].endswith("…"))
        for line in lines:
            self.assertLessEqual(self.metrics.horizontalAdvance(line), width)

    def test_short_note_stays_on_one_line(self) -> None:
        self.assertEqual(wrap_two_lines("работает если есть IPv6", self.metrics, 220), ["работает если есть IPv6"])

    def test_note_that_fits_two_lines_has_no_ellipsis(self) -> None:
        lines = wrap_two_lines("включить обход по IPv4 для этого сервиса", self.metrics, 150)

        self.assertEqual(" ".join(lines), "включить обход по IPv4 для этого сервиса")

    def test_single_huge_word_is_cut_on_one_line(self) -> None:
        lines = wrap_two_lines("Оченьдлинноесловобезпробелов" * 3, self.metrics, 80)

        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("…"))
        self.assertEqual(wrap_two_lines("", self.metrics, 200), [])


if __name__ == "__main__":
    unittest.main()
