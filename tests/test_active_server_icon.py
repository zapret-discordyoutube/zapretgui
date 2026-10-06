"""Активный сервер обновлений помечен векторным значком, а не эмодзи."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QByteArray  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtSvg import QSvgRenderer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.ui_texts import tr as tr_catalog  # noqa: E402
from updater.server_status_table_state import ServerStatusTableState  # noqa: E402
from updater.ui import plans  # noqa: E402
from updater.ui.active_server_icon import (  # noqa: E402
    ACTIVE_SERVER_ICON_SIZE,
    ACTIVE_SERVER_ROLE,
    ActiveServerLegendIcon,
    active_server_icon,
    active_server_pixmap,
    active_server_svg,
)
from updater.ui.main_build import build_servers_header_widgets, build_servers_table_widget  # noqa: E402
from updater.ui.table_view import recolor_active_server_rows, upsert_server_status  # noqa: E402


_APP = QApplication.instance() or QApplication([])

_CYAN = "#3de8e0"
_BLUE = "#0067c0"
_STAR = "⭐"


def _tr(_key: str, default: str) -> str:
    return default


class ActiveServerIconTests(unittest.TestCase):
    def test_svg_is_valid_and_painted_in_requested_color(self) -> None:
        svg = active_server_svg(_CYAN)

        self.assertTrue(QSvgRenderer(QByteArray(svg.encode("utf-8"))).isValid())
        self.assertIn(f'fill="{_CYAN}"', svg)

    def test_pixmap_is_a_filled_circle_with_a_cut_out_check(self) -> None:
        image = active_server_pixmap(_CYAN, size=16, scale=4.0).toImage()
        side = image.width()
        self.assertEqual(side, 64)

        corner = QColor.fromRgba(image.pixel(1, 1))
        rim = QColor.fromRgba(image.pixel(side // 2, 6))
        # Середина нижнего излома галочки: там вырез, виден фон строки.
        check = QColor.fromRgba(image.pixel(29, 38))

        self.assertEqual(corner.alpha(), 0)
        self.assertEqual(rim.alpha(), 255)
        self.assertEqual(rim.name(), _CYAN)
        self.assertLess(check.alpha(), 40)

    def test_invalid_color_falls_back_to_a_visible_one(self) -> None:
        image = active_server_pixmap("не цвет", size=16, scale=1.0).toImage()

        self.assertGreater(QColor.fromRgba(image.pixel(8, 2)).alpha(), 200)

    def test_pictures_are_cached_per_color(self) -> None:
        self.assertIs(active_server_pixmap(_CYAN, size=16), active_server_pixmap("#3DE8E0", size=16))
        self.assertIs(active_server_icon(_CYAN), active_server_icon(_CYAN))
        self.assertIsNot(active_server_icon(_CYAN), active_server_icon(_BLUE))

    def test_icon_has_sharp_pictures_for_scaled_screens(self) -> None:
        sizes = {size.width() for size in active_server_icon(_CYAN).availableSizes()}

        self.assertIn(ACTIVE_SERVER_ICON_SIZE, sizes)


class ActiveServerRowTests(unittest.TestCase):
    def _table(self):
        table = build_servers_table_widget(tr_fn=_tr)
        self.addCleanup(table.deleteLater)
        state = ServerStatusTableState()
        for name, status in (
            ("VPS Update", {"status": "online", "response_time": 0.05, "is_current": True}),
            ("Forgejo API", {"status": "online", "response_time": 0.09}),
        ):
            upsert_server_status(
                table,
                table_state=state,
                server_name=name,
                status=status,
                channel="dev",
                language="ru",
                accent_hex=_CYAN,
            )
        return table

    def test_row_plan_keeps_server_name_clean(self) -> None:
        plan = plans.build_server_row_plan(
            row_server_name="VPS Update",
            status={"status": "online", "is_current": True},
            channel="dev",
            language="ru",
        )

        self.assertEqual(plan.server_text, "VPS Update")
        self.assertTrue(plan.server_accent)

    def test_active_row_shows_icon_and_accent_color_without_emoji(self) -> None:
        table = self._table()
        active = table.item(0, 0)
        other = table.item(1, 0)

        self.assertEqual(active.text(), "VPS Update")
        self.assertNotIn(_STAR, active.text())
        self.assertFalse(active.icon().isNull())
        self.assertEqual(active.foreground().color().name(), _CYAN)
        self.assertTrue(active.data(ACTIVE_SERVER_ROLE))

        self.assertTrue(other.icon().isNull())
        self.assertFalse(other.data(ACTIVE_SERVER_ROLE))
        self.assertEqual(table.iconSize().width(), ACTIVE_SERVER_ICON_SIZE)

    def test_changed_accent_recolors_only_the_active_row(self) -> None:
        table = self._table()

        recolor_active_server_rows(table, _BLUE)

        self.assertEqual(table.item(0, 0).foreground().color().name(), _BLUE)
        self.assertEqual(table.item(0, 0).icon().cacheKey(), active_server_icon(_BLUE).cacheKey())
        self.assertTrue(table.item(1, 0).icon().isNull())

    def test_server_that_stopped_being_active_loses_the_icon(self) -> None:
        table = build_servers_table_widget(tr_fn=_tr)
        self.addCleanup(table.deleteLater)
        state = ServerStatusTableState()
        common = {"table_state": state, "server_name": "VPS Update", "channel": "dev", "language": "ru", "accent_hex": _CYAN}
        upsert_server_status(table, status={"status": "online", "is_current": True}, **common)

        upsert_server_status(table, status={"status": "online", "is_current": False}, **common)

        self.assertTrue(table.item(0, 0).icon().isNull())
        self.assertFalse(table.item(0, 0).data(ACTIVE_SERVER_ROLE))


class ActiveServerLegendTests(unittest.TestCase):
    def test_legend_is_icon_plus_plain_word(self) -> None:
        widgets = build_servers_header_widgets(tr_fn=_tr, parent=None, on_about_clicked=lambda: None)
        self.addCleanup(widgets.header_widget.deleteLater)
        self.addCleanup(widgets.servers_header_widget.deleteLater)

        self.assertEqual(widgets.legend_active_label.text(), "активный")
        self.assertIsInstance(widgets.legend_active_icon, ActiveServerLegendIcon)
        self.assertFalse(widgets.legend_active_icon.pixmap().isNull())
        layout = widgets.servers_header_widget.layout()
        self.assertLess(layout.indexOf(widgets.legend_active_icon), layout.indexOf(widgets.legend_active_label))

    def test_legend_icon_follows_accent_color(self) -> None:
        icon = ActiveServerLegendIcon()
        self.addCleanup(icon.deleteLater)

        icon.set_color(_BLUE)

        self.assertEqual(icon.color(), _BLUE)
        image = icon.pixmap().toImage()
        self.assertEqual(QColor.fromRgba(image.pixel(image.width() // 2, 1)).name(), _BLUE)

    def test_catalog_text_has_no_emoji(self) -> None:
        for language in ("ru", "en"):
            with self.subTest(language=language):
                text = tr_catalog("page.servers.legend.active", language=language, default="")
                self.assertTrue(text)
                self.assertNotIn(_STAR, text)

    def test_page_recolors_legend_and_rows_on_theme_change(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src/updater/ui/page.py").read_text(encoding="utf-8")

        self.assertIn("legend_icon.set_color(tokens.accent_hex)", source)
        self.assertIn("recolor_active_server_rows(self.servers_table, tokens.accent_hex)", source)
        self.assertNotIn(_STAR, source)


if __name__ == "__main__":
    unittest.main()
