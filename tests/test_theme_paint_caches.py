"""Общие кэши отрисовки: разбор цветов темы и значки, которые не удалось нарисовать."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from ui import theme


class ColorCacheTests(unittest.TestCase):
    def test_same_text_is_parsed_once(self) -> None:
        theme._QCOLOR_TEXT_CACHE.clear()
        with patch.object(theme, "_parse_color_text", wraps=theme._parse_color_text) as parse:
            first = theme.to_qcolor("rgba(10, 20, 30, 0.5)")
            second = theme.to_qcolor("rgba(10, 20, 30, 0.5)")

        self.assertEqual(parse.call_count, 1)
        self.assertEqual((first.red(), first.green(), first.blue(), first.alpha()), (10, 20, 30, 128))
        self.assertEqual(first, second)

    def test_caller_may_change_returned_color_without_spoiling_the_cache(self) -> None:
        first = theme.to_qcolor("#102030")
        first.setAlpha(5)

        self.assertEqual(theme.to_qcolor("#102030").alpha(), 255)

    def test_qcolor_argument_is_copied_and_bad_text_falls_back(self) -> None:
        source = QColor(1, 2, 3)
        result = theme.to_qcolor(source)
        result.setAlpha(9)

        self.assertEqual(source.alpha(), 255)
        self.assertEqual(theme.to_qcolor("not a color", "#ff0000"), QColor("#ff0000"))
        self.assertEqual(theme.to_qcolor("not a color", "also bad"), QColor(0, 0, 0))
        self.assertEqual(theme.to_qcolor(""), QColor(0, 0, 0))


class FailedIconCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_icon_that_cannot_be_rendered_is_tried_once(self) -> None:
        """Строка списка рисуется десятки раз в секунду: отсутствующий значок
        не должен каждый раз заново проходить всю загрузку."""
        theme._QTA_PIXMAP_CACHE.clear()
        theme._QTA_FAILED_ICONS.clear()
        with patch.object(theme, "_render_qta_pixmap", side_effect=RuntimeError("нет шрифта")) as render:
            results = [theme.get_cached_qta_pixmap("zz.missing", color="#ffffff", size=14) for _ in range(5)]

        self.assertEqual(render.call_count, 1)
        self.assertTrue(all(pixmap.isNull() for pixmap in results))
        self.assertIn("zz.missing", theme._QTA_FAILED_ICONS)
        theme._QTA_PIXMAP_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
