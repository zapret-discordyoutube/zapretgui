from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

import ui.widgets.line_icons as icons_module
from presets.ui.control.quick_actions import quick_action_specs
from presets.ui.control.top_summary_widget import ControlTopSummaryWidget
from ui.widgets.line_icons import line_icon_names, line_icon_pixmap


def _painted_share(pixmap) -> float:
    image = pixmap.toImage()
    painted = sum(
        1 for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x, y).alpha() > 40
    )
    return painted / float(image.width() * image.height())


class LineIconsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_every_icon_draws_something_inside_its_square(self) -> None:
        for name in line_icon_names():
            with self.subTest(icon=name):
                pixmap = line_icon_pixmap(name, color="#3ee0e8", size=24)
                share = _painted_share(pixmap)
                # Линейный значок: заметен, но не сплошной квадрат.
                self.assertGreater(share, 0.08)
                self.assertLess(share, 0.75)

    def test_icon_takes_the_requested_color(self) -> None:
        image = line_icon_pixmap("profiles", color="#f5c04d", size=48).toImage()
        strongest = max(
            (image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())),
            key=lambda color: color.alpha(),
        )
        self.assertEqual(strongest.alpha(), 255)
        self.assertEqual((strongest.red(), strongest.green(), strongest.blue()), (0xF5, 0xC0, 0x4D))

    def test_sharp_on_high_dpi_and_cached(self) -> None:
        icons_module._CACHE.clear()
        crisp = line_icon_pixmap("mode", color="#3ee0e8", size=22, ratio=2.0)
        self.assertEqual((crisp.width(), crisp.height()), (44, 44))
        self.assertEqual(crisp.devicePixelRatio(), 2.0)
        again = line_icon_pixmap("mode", color="#3ee0e8", size=22, ratio=2.0)
        self.assertEqual(crisp.cacheKey(), again.cacheKey())

    def test_unknown_name_gives_an_empty_transparent_icon(self) -> None:
        self.assertEqual(_painted_share(line_icon_pixmap("нет-такого", color="#ffffff", size=16)), 0.0)

    def test_home_page_uses_only_drawn_icons(self) -> None:
        names = set(line_icon_names())
        for spec in quick_action_specs("page.winws2_control"):
            self.assertIn(spec.icon_name, names)

        summary = ControlTopSummaryWidget(language="ru", mode_value="Zapret 2")
        self.addCleanup(summary.deleteLater)
        for item in (summary.preset_item, summary.profiles_item, summary.mode_item):
            self.assertIn(item._icon_name, names)
            item._refresh_icon()
            self.assertFalse(item._icon_label.pixmap().isNull())


if __name__ == "__main__":
    unittest.main()
