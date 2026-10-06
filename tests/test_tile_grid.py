from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, QSize, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

import ui.widgets.tile_grid as tile_module
from ui.widgets.tile_grid import SoftTile, TileGrid


class _Tile(QWidget):
    def __init__(self, min_width: int = 0, height: int = 40) -> None:
        super().__init__()
        self._hint = QSize(max(1, min_width), height)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self._hint

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self._hint


class TileGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _grid(self, count: int, *, width: int, weights=(), **kwargs) -> tuple[TileGrid, list[_Tile]]:
        grid = TileGrid(min_tile_width=200, spacing=12, **kwargs)
        self.addCleanup(grid.deleteLater)
        tiles = [_Tile() for _ in range(count)]
        for index, tile in enumerate(tiles):
            grid.add_tile(tile, weight=weights[index] if weights else 1.0)
        grid.resize(width, 10)
        grid.relayout()
        return grid, tiles

    def test_wide_grid_puts_all_tiles_in_one_row(self) -> None:
        grid, tiles = self._grid(4, width=1000)

        self.assertEqual(grid.columns(), 4)
        self.assertEqual({tile.y() for tile in tiles}, {0})
        self.assertEqual([tile.width() for tile in tiles], [241] * 4)
        self.assertEqual(tiles[1].x(), 241 + 12)
        self.assertEqual(grid.maximumHeight(), 40)
        self.assertEqual(grid.minimumHeight(), 40)

    def test_narrow_grid_wraps_without_a_lonely_last_tile(self) -> None:
        # В три столбца плитки поместились бы, но четвёртая осталась бы одна.
        grid, tiles = self._grid(4, width=700)

        self.assertEqual(grid.columns(), 2)
        self.assertEqual([tile.y() for tile in tiles], [0, 0, 52, 52])
        self.assertEqual(grid.maximumHeight(), 40 + 12 + 40)

    def test_lonely_last_tile_beats_a_single_column(self) -> None:
        # Три столбца не влезают, два без одинокой плитки невозможны (5 = 2+2+1):
        # лучше одинокая плитка на всю ширину, чем столбик из пяти.
        grid, tiles = self._grid(5, width=500)

        self.assertEqual(grid.columns(), 2)
        self.assertEqual([tile.y() for tile in tiles], [0, 0, 52, 52, 104])
        self.assertEqual(tiles[4].width(), 500)
        self.assertEqual(tiles[0].width(), (500 - 12) // 2)

    def test_very_narrow_grid_falls_back_to_one_column(self) -> None:
        grid, tiles = self._grid(3, width=300)

        self.assertEqual(grid.columns(), 1)
        self.assertEqual([tile.width() for tile in tiles], [300] * 3)

    def test_weights_make_a_tile_wider_in_a_single_row(self) -> None:
        grid, tiles = self._grid(2, width=1012, weights=(1.5, 1.0))

        self.assertEqual(tiles[0].width(), 600)
        self.assertEqual(tiles[1].width(), 400)

    def test_wrapped_rows_use_equal_widths_so_edges_line_up(self) -> None:
        grid, tiles = self._grid(4, width=700, weights=(1.9, 1.4, 0.85, 1.05))

        self.assertEqual(grid.columns(), 2)
        self.assertEqual({tile.width() for tile in tiles}, {344})
        self.assertEqual(tiles[1].x(), tiles[3].x())

    def test_tile_minimum_width_forces_a_wrap(self) -> None:
        grid = TileGrid(min_tile_width=100, spacing=12)
        self.addCleanup(grid.deleteLater)
        tiles = [_Tile(), _Tile(), _Tile(), _Tile()]
        tiles[0].setMinimumWidth(380)
        for tile in tiles:
            grid.add_tile(tile)
        grid.resize(1000, 10)
        grid.relayout()

        self.assertEqual(grid.columns(), 2)

    def test_hidden_tile_gives_its_place_to_neighbours(self) -> None:
        grid, tiles = self._grid(4, width=1000)
        grid.show()

        tiles[1].setVisible(False)
        QApplication.processEvents()

        self.assertEqual(grid.columns(), 3)
        self.assertEqual(tiles[2].x(), tiles[0].width() + 12)

    def test_fixed_row_height(self) -> None:
        grid, tiles = self._grid(2, width=600, row_height=72)

        self.assertEqual({tile.height() for tile in tiles}, {72})
        self.assertEqual(grid.maximumHeight(), 72)


class SoftTileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._enabled = True
        patcher = mock.patch.object(tile_module, "are_live_animations_enabled", side_effect=lambda: self._enabled)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _tile(self, **kwargs) -> tuple[SoftTile, list[int]]:
        tile = SoftTile(**kwargs)
        tile.resize(220, 72)
        self.addCleanup(tile.deleteLater)
        clicks: list[int] = []
        tile.clicked.connect(lambda: clicks.append(1))
        return tile, clicks

    @staticmethod
    def _mouse(tile, kind, x: float, y: float) -> None:
        event = QMouseEvent(
            kind,
            QPointF(x, y),
            QPointF(x, y),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        if kind == QEvent.Type.MouseButtonPress:
            tile.mousePressEvent(event)
        else:
            tile.mouseReleaseEvent(event)

    def test_click_needs_press_and_release_inside(self) -> None:
        tile, clicks = self._tile()

        self._mouse(tile, QEvent.Type.MouseButtonPress, 10, 10)
        self._mouse(tile, QEvent.Type.MouseButtonRelease, 10, 10)
        self.assertEqual(clicks, [1])

        self._mouse(tile, QEvent.Type.MouseButtonPress, 10, 10)
        self._mouse(tile, QEvent.Type.MouseButtonRelease, 900, 10)
        self.assertEqual(clicks, [1])

    def test_keyboard_click_and_focus_policy(self) -> None:
        tile, clicks = self._tile()
        self.assertEqual(tile.focusPolicy(), Qt.FocusPolicy.StrongFocus)

        for key in (Qt.Key.Key_Return, Qt.Key.Key_Space):
            tile.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(clicks, [1, 1])

    def test_info_tile_is_not_a_button(self) -> None:
        tile, clicks = self._tile(clickable=False)
        self.assertEqual(tile.focusPolicy(), Qt.FocusPolicy.NoFocus)

        tile.click()
        tile.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
        tile.show()
        tile.enterEvent(None)

        self.assertEqual(clicks, [])
        self.assertEqual(tile._hover_target(), 0.0)

    def test_disabled_tile_does_not_click(self) -> None:
        tile, clicks = self._tile()
        tile.setEnabled(False)
        tile.click()
        self.assertEqual(clicks, [])

    def test_focus_from_a_mouse_click_does_not_keep_the_tile_lit(self) -> None:
        from PyQt6.QtGui import QFocusEvent

        tile, _clicks = self._tile()
        tile.show()
        tile.focusInEvent(QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.MouseFocusReason))
        self.assertEqual(tile._hover_target(), 0.0)
        tile.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.MouseFocusReason))

        # Табуляция с клавиатуры подсвечивает, а уход фокуса гасит.
        tile.focusInEvent(QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
        self.assertEqual(tile._hover_target(), 1.0)
        tile.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.TabFocusReason))
        self.assertEqual(tile._hover_target(), 0.0)

    def test_hover_is_dropped_when_the_cursor_is_really_elsewhere(self) -> None:
        from PyQt6.QtCore import QPoint

        tile, _clicks = self._tile()
        tile.show()
        tile.enterEvent(None)
        self.assertTrue(tile._hovered)

        # Qt не прислал «курсор ушёл» (например, открылось окно подтверждения).
        with mock.patch.object(tile_module, "QCursor") as cursor:
            cursor.pos.return_value = tile.mapToGlobal(QPoint(-500, -500))
            tile.sync_hover_with_cursor()
        self.assertFalse(tile._hovered)
        self.assertEqual(tile._hover_target(), 0.0)

        tile.enterEvent(None)
        with mock.patch.object(tile_module, "QCursor") as cursor:
            cursor.pos.return_value = tile.mapToGlobal(QPoint(5, 5))
            tile.sync_hover_with_cursor()
        self.assertTrue(tile._hovered)

    def test_hover_fades_in_and_resets_when_hidden(self) -> None:
        tile, _clicks = self._tile()
        tile.show()
        tile.enterEvent(None)
        self.assertEqual(tile._hover_fade.state(), tile._hover_fade.State.Running)
        tile._hover_fade.setCurrentTime(tile._hover_fade.duration())
        self.assertEqual(tile.hover_progress(), 1.0)

        tile.hide()
        self.assertEqual(tile.hover_progress(), 0.0)
        self.assertNotEqual(tile._hover_fade.state(), tile._hover_fade.State.Running)

    def test_hover_is_instant_without_live_animations(self) -> None:
        self._enabled = False
        tile, _clicks = self._tile()
        tile.show()
        tile.enterEvent(None)

        self.assertEqual(tile.hover_progress(), 1.0)
        self.assertNotEqual(tile._hover_fade.state(), tile._hover_fade.State.Running)

    def test_paints_resting_hovered_and_pressed(self) -> None:
        tile, _clicks = self._tile()
        for hover, pressed in ((0.0, False), (0.6, False), (1.0, True)):
            tile._hover_t = hover
            tile._pressed = pressed
            image = QPixmap(tile.size())
            image.fill(QColor(0, 0, 0, 0))
            tile.render(image)
            self.assertFalse(image.isNull())


if __name__ == "__main__":
    unittest.main()
