"""Обычная подсказка Qt нигде не показывается родным чёрным окошком."""

from __future__ import annotations

import inspect
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint
from PyQt6.QtGui import QHelpEvent
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QPushButton, QWidget
from PyQt6.QtWidgets import QToolTip as NativeTip
from qfluentwidgets import TableWidget, ToolTip

from ui.fluent_widgets import set_area_tooltip, set_tooltip
from ui.native_tooltip_guard import NativeToolTipGuard, install_native_tooltip_guard
from ui.widgets.fluent_item_tooltip import install_fluent_item_tooltips, set_fluent_item_tooltip


def _native_tips() -> list[QWidget]:
    return [w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()]


def _fluent_tips(window: QWidget) -> list[ToolTip]:
    return [tip for tip in window.findChildren(ToolTip) if tip.isVisible()]


def _send_tooltip_event(widget: QWidget, pos: QPoint) -> None:
    QCoreApplication.sendEvent(widget, QHelpEvent(QEvent.Type.ToolTip, pos, widget.mapToGlobal(pos)))


class NativeToolTipGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.guard = install_native_tooltip_guard(self._app)
        self.addCleanup(self.guard.cleanup)
        self.window = QWidget()
        self.window.resize(400, 300)
        self.window.show()
        self.addCleanup(self.window.deleteLater)

    def _button(self, text: str = "x") -> QPushButton:
        button = QPushButton(text, self.window)
        button.setGeometry(20, 20, 120, 30)
        button.show()
        return button

    def test_plain_widget_tooltip_is_shown_as_fluent(self) -> None:
        button = self._button()
        button.setToolTip("XBOX DNS")

        _send_tooltip_event(button, QPoint(5, 5))
        self._app.processEvents()

        self.assertEqual(_native_tips(), [])
        self.assertEqual([tip.text() for tip in _fluent_tips(self.window)], ["XBOX DNS"])

    def test_without_guard_the_same_tooltip_is_native(self) -> None:
        # Проверка самой проверки: без защиты Qt рисует родное окошко.
        self.guard.cleanup()
        button = self._button()
        button.setToolTip("XBOX DNS")

        _send_tooltip_event(button, QPoint(5, 5))

        self.assertEqual(len(_native_tips()), 1)
        NativeTip.hideText()
        for tip in _native_tips():
            tip.close()

    def test_area_tooltip_follows_text_under_cursor(self) -> None:
        tiles = QWidget(self.window)
        tiles.setGeometry(0, 60, 300, 100)
        tiles.show()

        set_area_tooltip(tiles, "XBOX DNS")
        _send_tooltip_event(tiles, QPoint(10, 10))
        self.assertEqual([tip.text() for tip in _fluent_tips(self.window)], ["XBOX DNS"])

        # Курсор ушёл на соседний значок: старая подсказка убирается сразу.
        set_area_tooltip(tiles, "Comss DNS")
        self.assertEqual(_fluent_tips(self.window), [])

        _send_tooltip_event(tiles, QPoint(40, 10))
        self.assertEqual([tip.text() for tip in _fluent_tips(self.window)], ["Comss DNS"])
        self.assertEqual(_native_tips(), [])

    def test_hosts_dns_profile_icon_tooltip_is_fluent(self) -> None:
        # Тот самый случай: значок DNS-профиля на плитке в «Редакторе hosts».
        from PyQt6.QtCore import QPointF, Qt
        from PyQt6.QtGui import QMouseEvent

        from hosts.ui.services_tiles import HostsChoice, HostsTile, HostsTilesGrid

        choices = (
            HostsChoice("xbox", "XBOX DNS", "fa5s.gamepad", "#107c10"),
            HostsChoice("comss", "Comss DNS", "fa5s.shield-alt", "#0078d4"),
        )
        grid = HostsTilesGrid(self.window)
        grid.set_tiles([HostsTile(kind="tile", key="gemini", title="Gemini AI", choices=choices, selected="xbox")])
        grid.setGeometry(0, 0, 400, 300)
        grid.show()
        self._app.processEvents()
        point = grid._choice_rect(grid._rects[0], 1).center()

        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(point),
            QPointF(grid.mapToGlobal(point)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QCoreApplication.sendEvent(grid, move)
        _send_tooltip_event(grid, point)
        self._app.processEvents()

        self.assertEqual(_native_tips(), [])
        self.assertEqual([tip.text() for tip in _fluent_tips(self.window)], ["Comss DNS"])

    def test_tooltip_hides_when_cursor_leaves_widget(self) -> None:
        button = self._button()
        button.setToolTip("подсказка")
        _send_tooltip_event(button, QPoint(5, 5))
        self.assertEqual(len(_fluent_tips(self.window)), 1)

        QCoreApplication.sendEvent(button, QEvent(QEvent.Type.Leave))

        self.assertEqual(_fluent_tips(self.window), [])

    def test_plain_item_view_tooltip_is_shown_as_fluent(self) -> None:
        view = QListWidget(self.window)
        view.setGeometry(0, 60, 300, 120)
        item = QListWidgetItem("строка")
        item.setData(3, "подсказка строки")  # Qt.ItemDataRole.ToolTipRole
        view.addItem(item)
        view.show()

        _send_tooltip_event(view.viewport(), view.visualItemRect(item).center())
        self._app.processEvents()

        self.assertEqual(_native_tips(), [])
        self.assertEqual([tip.text() for tip in _fluent_tips(self.window)], ["подсказка строки"])

    def test_widget_with_set_tooltip_gets_no_second_tooltip(self) -> None:
        button = self._button()
        set_tooltip(button, "уже fluent")

        _send_tooltip_event(button, QPoint(5, 5))
        self._app.processEvents()

        self.assertEqual(_native_tips(), [])
        self.assertIsNone(getattr(self.window, "_native_tooltip_guard_tip", None))

    def test_fluent_item_tooltips_keep_working(self) -> None:
        table = TableWidget(self.window)
        table.setGeometry(0, 60, 300, 160)
        table.setRowCount(1)
        table.setColumnCount(1)
        install_fluent_item_tooltips(table)
        from PyQt6.QtWidgets import QTableWidgetItem

        item = QTableWidgetItem("ячейка")
        set_fluent_item_tooltip(item, "подробности")
        table.setItem(0, 0, item)
        table.show()

        _send_tooltip_event(table.viewport(), table.visualItemRect(item).center())
        self._app.processEvents()

        self.assertEqual(_native_tips(), [])
        self.assertEqual([tip.text() for tip in _fluent_tips(self.window)], ["подробности"])
        self.assertIsNone(getattr(self.window, "_native_tooltip_guard_tip", None))

    def test_native_tip_shown_directly_is_closed(self) -> None:
        self.window.activateWindow()
        NativeTip.showText(self.window.mapToGlobal(QPoint(30, 30)), "мимо события", self.window)
        self.assertEqual(len(_native_tips()), 1)

        self._app.processEvents()

        self.assertEqual(_native_tips(), [])

    def test_install_is_idempotent(self) -> None:
        self.assertIs(install_native_tooltip_guard(self._app), self.guard)
        self.assertIsInstance(self.guard, NativeToolTipGuard)

    def test_guard_is_installed_at_qt_startup(self) -> None:
        from main import qt_runtime

        source = inspect.getsource(qt_runtime.ensure_qt_runtime)

        self.assertIn("install_native_tooltip_guard(app)", source)


if __name__ == "__main__":
    unittest.main()
