from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

import ui.widgets.opaque_scroll as opaque_scroll_module
from ui.presets_menu.delegate import PresetListDelegate
from ui.presets_menu.model import PresetListModel
from ui.presets_menu.view import LinkedWheelListView
from ui.widgets.fluent_scrollbar import install_fluent_scrollbars

BACKDROP = QColor(31, 41, 59)


class _FlatHost(QWidget):
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), BACKDROP)
        painter.end()


class _StripedHost(QWidget):
    """Фон с картинкой: полосы разного цвета (как фоновое изображение окна)."""

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        for x in range(0, self.width(), 20):
            painter.fillRect(x, 0, 10, self.height(), QColor(31, 41, 59) if (x // 20) % 2 else QColor(60, 80, 100))
        painter.end()


def _rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for group in range(4):
        rows.append({"kind": "folder", "folder_key": f"f{group}", "name": f"Группа {group}", "is_collapsed": False, "count": 24})
        for number in range(24):
            rows.append({"kind": "preset", "name": f"general ALT{group}-{number}", "file_name": f"p{group}-{number}.txt",
                         "folder_key": f"f{group}", "depth": 1, "icon_color": ["", "#e0a030", "#40c080"][number % 3]})
    return rows


class _Case(unittest.TestCase):
    host_class = _FlatHost

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.host = self.host_class()
        self.addCleanup(self.host.deleteLater)
        layout = QVBoxLayout(self.host)
        layout.setContentsMargins(20, 20, 20, 20)
        self.busy = False
        self.view = LinkedWheelListView(self.host)
        self.view._opaque_scroll._is_busy = lambda: self.busy
        self.model = PresetListModel(self.view)
        self.model.set_rows(_rows())
        self.view.setModel(self.model)
        self.view.setItemDelegate(PresetListDelegate(self.view))
        # Как в настоящей странице: место под полосу прокрутки отведено, она не
        # лежит поверх списка (иначе Qt не двигает готовую картинку).
        install_fluent_scrollbars(self.view, vertical=True, horizontal=False, reserve_vertical_space=True)
        layout.addWidget(self.view)
        self.host.resize(900, 600)
        self.host.show()
        self._settle()
        self.view.doItemsLayout()
        self._settle()
        self.scroll = self.view._opaque_scroll
        self.viewport = self.view.viewport()
        self.addCleanup(self.scroll.end)

    @staticmethod
    def _settle() -> None:
        for _ in range(4):
            QApplication.processEvents()

    def _scroll_to(self, value: int) -> None:
        self.view.verticalScrollBar().setValue(value)
        self._settle()


class ActivationTests(_Case):
    def test_flat_backdrop_is_measured_and_the_viewport_becomes_opaque(self) -> None:
        self._scroll_to(40)

        self.assertTrue(self.scroll.is_active)
        self.assertTrue(self.viewport.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent))
        measured = QColor.fromRgba(self.scroll._backdrop.pixel(0, 0))
        self.assertEqual((measured.red(), measured.green(), measured.blue(), measured.alpha()), (31, 41, 59, 255))

    def test_the_mode_ends_after_the_scroll_stops_and_the_list_is_repainted_normally(self) -> None:
        self._scroll_to(40)
        with mock.patch.object(self.viewport, "update") as update:
            self.scroll._idle.timeout.emit()

        self.assertFalse(self.scroll.is_active)
        self.assertFalse(self.viewport.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent))
        update.assert_called_once_with()

    def test_ending_twice_is_harmless(self) -> None:
        self._scroll_to(40)
        self.scroll.end()
        self.scroll.end()
        self.assertFalse(self.viewport.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent))

    def test_the_idle_timer_is_restarted_by_every_step(self) -> None:
        self._scroll_to(20)
        with mock.patch.object(self.scroll._idle, "start") as restart:
            self._scroll_to(30)
        restart.assert_called()

    def test_another_animation_over_the_list_keeps_the_normal_path(self) -> None:
        self.busy = True
        self._scroll_to(40)
        self.assertFalse(self.scroll.is_active)
        self.assertFalse(self.viewport.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent))

    def test_hiding_the_list_ends_the_mode(self) -> None:
        self._scroll_to(40)
        self.assertTrue(self.scroll.is_active)
        self.view.hide()
        self.assertFalse(self.scroll.is_active)
        self.assertFalse(self.viewport.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent))

    def test_a_hidden_list_does_not_start_it(self) -> None:
        self.view.hide()
        self.scroll.on_scroll()
        self.assertFalse(self.scroll.is_active)

    def test_the_marker_slide_ends_the_mode_and_blocks_it(self) -> None:
        from ui.widgets.active_row_motion import attach_active_row_motion

        motion = attach_active_row_motion(self.view, PresetListModel.ActiveRole)
        self._scroll_to(40)
        self.assertTrue(self.scroll.is_active)
        self.model.set_active_preset("p0-3.txt")
        self.model.set_active_preset("p0-7.txt")
        self._settle()
        if motion.is_running():
            self.assertFalse(self.scroll.is_active)
            self._scroll_to(60)
            self.assertFalse(self.scroll.is_active)

    def test_the_list_paints_nothing_while_the_backdrop_is_being_measured(self) -> None:
        painted: list[int] = []
        original = self.view.itemDelegate()._paint_preset_row
        self.view.itemDelegate()._paint_preset_row = lambda *args: (painted.append(1), original(*args))[1]
        self.assertFalse(self.scroll.painting_suppressed)
        self.scroll._measuring = True
        try:
            self.view.paintEvent(mock.Mock(region=lambda: None))
        finally:
            self.scroll._measuring = False
        self.assertEqual(painted, [])


class StripedBackgroundTests(_Case):
    host_class = _StripedHost

    def test_a_background_that_is_not_flat_keeps_the_normal_path(self) -> None:
        self._scroll_to(40)

        self.assertFalse(self.scroll.is_active)
        self.assertFalse(self.viewport.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent))

    def test_the_failed_measurement_is_not_repeated_on_every_step(self) -> None:
        self._scroll_to(40)
        with mock.patch.object(self.scroll, "_measure_flat_backdrop") as measure:
            self._scroll_to(50)
            self._scroll_to(60)
        measure.assert_not_called()


class DrawingTests(_Case):
    def _count_tiles(self, steps) -> int:
        drawn: list[int] = []
        delegate = self.view.itemDelegate()
        original = delegate._paint_preset_row
        delegate._paint_preset_row = lambda *args: (drawn.append(1), original(*args))[1]
        try:
            for value in steps:
                self._scroll_to(value)
        finally:
            delegate._paint_preset_row = original
        return len(drawn)

    def test_scrolling_draws_far_fewer_tiles_than_the_normal_path(self) -> None:
        steps = [30, 45, 60, 75, 60, 45, 30, 15]
        with_opaque = self._count_tiles(steps)
        with mock.patch.object(opaque_scroll_module.OpaqueScroll, "_begin", return_value=False):
            self.scroll.end()
            self._scroll_to(0)
            without = self._count_tiles(steps)
        self.assertGreater(without, 150)
        self.assertLess(with_opaque * 2, without)

    @staticmethod
    def _worst_difference(first: QImage, second: QImage, area) -> int:
        worst = 0
        for y in range(area.top(), area.bottom() + 1):
            for x in range(area.left(), area.right() + 1):
                a, b = QColor(first.pixel(x, y)), QColor(second.pixel(x, y))
                worst = max(worst, abs(a.red() - b.red()), abs(a.green() - b.green()), abs(a.blue() - b.blue()))
        return worst

    def test_the_picture_in_the_middle_of_a_scroll_matches_a_fresh_render(self) -> None:
        # Значки у краёв допускают расхождение в один уровень цвета из 255
        # (округление сглаживания), остальное совпадает точка в точку.
        window = self.host.windowHandle()
        for value in (23, 41, 33, 58, 71, 49, 12, 66, 3, 5, 14, 9, 80, 11):
            self._scroll_to(value)
            self.assertTrue(self.scroll.is_active, value)
            onscreen = window.screen().grabWindow(self.host.winId()).toImage().convertToFormat(QImage.Format.Format_ARGB32)
            fresh = self.host.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
            area = self.viewport.rect().translated(self.viewport.mapTo(self.host, self.viewport.rect().topLeft()))
            self.assertLessEqual(self._worst_difference(onscreen, fresh, area), 2, value)

    def test_after_the_scroll_the_list_is_what_the_normal_path_draws(self) -> None:
        for value in (23, 41, 33, 58, 71):
            self._scroll_to(value)
        self.scroll.end()
        self._settle()
        onscreen = self.host.windowHandle().screen().grabWindow(self.host.winId()).toImage().convertToFormat(QImage.Format.Format_ARGB32)
        fresh = self.host.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
        area = self.viewport.rect().translated(self.viewport.mapTo(self.host, self.viewport.rect().topLeft()))
        self.assertLessEqual(self._worst_difference(onscreen, fresh, area), 2)


if __name__ == "__main__":
    unittest.main()
