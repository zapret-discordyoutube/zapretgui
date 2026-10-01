from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QFontMetrics, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import getFont

from hosts.ui.services_tiles import HostsTile, HostsTilesGrid, split_service_title, wrap_lines
from ui.theme import get_cached_qta_pixmap

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


def _ink_size(pixmap: QPixmap) -> tuple[int, int]:
    """Ширина и высота закрашенной части картинки."""
    image = pixmap.toImage()
    points = [
        (x, y)
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() > 8
    ]
    xs = [x for x, _y in points]
    ys = [y for _x, y in points]
    return max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


class HostsTileIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_wide_font_icon_is_not_cut_by_its_square(self) -> None:
        import qtawesome as qta

        for name in ("fa5s.gamepad", "fa5s.network-wired"):
            for size in (13, 20):
                with self.subTest(name=name, size=size):
                    # Обычный размер значка — на широком холсте, где резать нечему.
                    roomy = QPixmap(size * 3, size)
                    roomy.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(roomy)
                    qta.icon(name, color="#ffffff").paint(painter, QRect(0, 0, size * 3, size))
                    painter.end()
                    natural_width, natural_height = _ink_size(roomy)
                    self.assertGreater(natural_width, size, "значок не широкий — тест ничего не проверяет")

                    pixmap = get_cached_qta_pixmap(name, color="#ffffff", size=size)
                    self.assertEqual((pixmap.width(), pixmap.height()), (size, size))
                    # Срезанный значок сохраняет прежнюю высоту; уместившийся — уменьшен целиком.
                    _width, height = _ink_size(pixmap)
                    self.assertLess(height, natural_height)

    def test_tile_draws_brand_logo_from_bundle(self) -> None:
        import profile.ui.profile_icon as profile_icon

        profile_icon._PROFILE_PIXMAP_CACHE.clear()
        grid = HostsTilesGrid()
        self.addCleanup(grid.deleteLater)
        grid.resize(820, 100)
        grid.set_tiles([
            HostsTile(kind="tile", key="a", title="Discord", has_switch=True, icon_name="simple:discord:DI"),
        ])
        self.assertFalse(grid.grab().isNull())

        kinds = {(key[0], key[1]) for key in profile_icon._PROFILE_PIXMAP_CACHE}
        self.assertIn(("simple", "discord"), kinds)
        self.assertFalse(any(kind == "initials" for kind, _value in kinds))


if __name__ == "__main__":
    unittest.main()
