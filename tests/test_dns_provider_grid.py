from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

from dns.ui.provider_grid import ADD_TILE_KEY, DnsProviderGrid, DnsTile, GridTexts, latency_text, tile_accessible_text


def _tiles() -> list[DnsTile]:
    return [
        DnsTile(kind="group", title="Популярные", counter="2"),
        DnsTile(kind="provider", key="Cloudflare", title="Cloudflare", note="Быстрый", address="1.1.1.1", selected=True),
        DnsTile(kind="provider", key="Google DNS", title="Google DNS", address="8.8.8.8", latency="ok", latency_ms=18.4, fastest=True),
        DnsTile(kind="group", title="Свои DNS"),
        DnsTile(kind="provider", key="Дом", title="Дом", address="192.168.1.1", custom=True),
        DnsTile(kind="add", key=ADD_TILE_KEY, title="Свой DNS", note="Добавить свой адрес"),
    ]


def _key(grid: DnsProviderGrid, key: Qt.Key, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    grid.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, modifiers))


class DnsProviderGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _grid(self) -> DnsProviderGrid:
        grid = DnsProviderGrid()
        self.addCleanup(grid.deleteLater)
        grid.resize(560, 100)
        grid.set_tiles(_tiles())
        return grid

    def test_layout_places_groups_on_own_rows_and_grows_height(self) -> None:
        grid = self._grid()

        cloudflare = grid.tile_rect("Cloudflare")
        google = grid.tile_rect("Google DNS")
        custom = grid.tile_rect("Дом")
        self.assertEqual(cloudflare.top(), google.top())
        self.assertGreater(custom.top(), cloudflare.bottom())
        self.assertGreaterEqual(grid.height(), grid.tile_rect(ADD_TILE_KEY).bottom())

    def test_keyboard_moves_between_tiles_and_activates(self) -> None:
        grid = self._grid()
        activated: list[str] = []
        added: list[bool] = []
        grid.activated.connect(activated.append)
        grid.add_clicked.connect(lambda: added.append(True))

        grid._set_cursor(1)
        _key(grid, Qt.Key.Key_Right)
        _key(grid, Qt.Key.Key_Return)
        _key(grid, Qt.Key.Key_End)
        _key(grid, Qt.Key.Key_Space)

        self.assertEqual(activated, ["Google DNS"])
        self.assertEqual(added, [True])
        self.assertIn("Свой DNS", grid.accessibleDescription())

    def test_focus_starts_on_selected_tile_and_reads_state(self) -> None:
        grid = self._grid()

        grid._cursor = -1
        grid.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))

        self.assertEqual(grid._key_at(grid._cursor), "Cloudflare")
        self.assertIn("Cloudflare, выбран", grid.accessibleDescription())

    def test_context_menu_only_for_custom_servers(self) -> None:
        grid = self._grid()
        wanted: list[str] = []
        grid.context_menu_wanted.connect(lambda key, _pos: wanted.append(key))

        grid._set_cursor(1)
        _key(grid, Qt.Key.Key_Menu)
        grid._set_cursor(4)
        _key(grid, Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
        center = QPointF(grid.tile_rect("Дом").center())
        grid.mouseReleaseEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                center,
                center,
                Qt.MouseButton.RightButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

        self.assertEqual(wanted, ["Дом", "Дом"])

    def test_mouse_click_activates_tile_under_cursor(self) -> None:
        grid = self._grid()
        activated: list[str] = []
        grid.activated.connect(activated.append)
        point = QPointF(grid.tile_rect("Cloudflare").center())

        for event_type, buttons in (
            (QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
        ):
            event = QMouseEvent(event_type, point, point, Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
            if event_type == QEvent.Type.MouseButtonPress:
                grid.mousePressEvent(event)
            else:
                grid.mouseReleaseEvent(event)

        self.assertEqual(activated, ["Cloudflare"])
        self.assertEqual(grid.index_at(QPoint(-5, -5)), -1)

    def test_texts_follow_interface_language(self) -> None:
        tile = DnsTile(kind="provider", key="G", title="Google", address="8.8.8.8", latency="ok", latency_ms=0.4, fastest=True)
        english = GridTexts(ms="{ms} ms", selected="selected", not_selected="not selected", fastest="fastest")

        self.assertEqual(latency_text(tile), "1 мс")
        self.assertEqual(latency_text(tile, english), "1 ms")
        self.assertEqual(latency_text(DnsTile(kind="provider", latency="timeout")), "нет ответа")
        self.assertEqual(latency_text(DnsTile(kind="provider")), "")
        self.assertEqual(tile_accessible_text(tile, english), "Google, not selected, 8.8.8.8, 1 ms, fastest")

    def test_paint_does_not_fail_for_every_tile_state(self) -> None:
        grid = self._grid()
        grid.set_tiles(
            [
                *_tiles(),
                DnsTile(kind="provider", key="P", title="P", pending=True, latency="measuring", has_ipv6=True, has_doh=True),
                DnsTile(kind="provider", key="T", title="T", latency="timeout", color="#673ab7"),
            ]
        )
        grid.flash("Cloudflare")

        image = grid.grab()

        self.assertFalse(image.isNull())


if __name__ == "__main__":
    unittest.main()
