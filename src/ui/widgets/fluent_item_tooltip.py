from __future__ import annotations

from PyQt6.QtCore import QObject, QPoint, QEvent, Qt
from PyQt6.QtWidgets import QApplication, QAbstractItemView, QWidget
from qfluentwidgets import ToolTip


FLUENT_ITEM_TOOLTIP_ROLE = int(Qt.ItemDataRole.UserRole) + 610


class FluentItemToolTipController(QObject):
    """Fluent-подсказка для строк, которые рисуются через delegate."""

    def __init__(self, owner: QWidget, *, duration: int = 6000, offset: QPoint | None = None):
        super().__init__(owner)
        self._owner = owner
        self._duration = int(duration)
        self._offset = offset or QPoint(12, 18)
        self._tooltip: ToolTip | None = None
        owner.installEventFilter(self)

    def show_text(self, text: str, global_pos: QPoint) -> None:
        text = str(text or "").strip()
        if not text:
            self.hide()
            return

        if self._tooltip is None:
            parent = self._owner.window() if self._owner is not None else None
            self._tooltip = ToolTip("", parent)
            self._tooltip.setDuration(self._duration)

        self._tooltip.setText(text)
        self._tooltip.adjustSize()
        self._tooltip.move(self._bounded_pos(global_pos + self._offset))
        self._tooltip.show()

    def hide(self) -> None:
        if self._tooltip is not None:
            self._tooltip.hide()

    def is_visible(self) -> bool:
        return self._tooltip is not None and self._tooltip.isVisible()

    def eventFilter(self, obj, event):  # noqa: N802
        _ = obj
        if event.type() in {
            QEvent.Type.Hide,
            QEvent.Type.Leave,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
        }:
            self.hide()
        return super().eventFilter(obj, event)

    def _bounded_pos(self, pos: QPoint) -> QPoint:
        tooltip = self._tooltip
        if tooltip is None:
            return pos

        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        if screen is None:
            return pos

        rect = screen.availableGeometry()
        x = max(rect.left(), min(pos.x(), rect.right() - tooltip.width() - 4))
        y = max(rect.top(), min(pos.y(), rect.bottom() - tooltip.height() - 4))
        return QPoint(x, y)


class FluentItemViewToolTipController(QObject):
    """Fluent-подсказка для item-view: таблиц, списков и похожих виджетов."""

    def __init__(
        self,
        view: QAbstractItemView,
        *,
        role: int = FLUENT_ITEM_TOOLTIP_ROLE,
        duration: int = 6000,
    ):
        super().__init__(view)
        self._view = view
        self._viewport = view.viewport()
        self._role = int(role)
        self._tooltip = FluentItemToolTipController(self._viewport, duration=duration)
        self._viewport.installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._viewport and event.type() == QEvent.Type.ToolTip:
            index = self._view.indexAt(event.pos())
            text = str(index.data(self._role) or "").strip() if index.isValid() else ""
            if text:
                self._tooltip.show_text(text, event.globalPos())
                return True

            self._tooltip.hide()
            return True

        if event.type() in {
            QEvent.Type.Hide,
            QEvent.Type.Leave,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
        }:
            self._tooltip.hide()
        return super().eventFilter(obj, event)


def install_fluent_item_tooltips(
    view: QAbstractItemView,
    *,
    role: int = FLUENT_ITEM_TOOLTIP_ROLE,
) -> FluentItemViewToolTipController:
    """Включает fluent-подсказки для item-view один раз."""

    controller = getattr(view, "_fluent_item_tooltip_controller", None)
    if controller is None:
        controller = FluentItemViewToolTipController(view, role=role)
        view._fluent_item_tooltip_controller = controller  # type: ignore[attr-defined]
    return controller


def set_fluent_item_tooltip(item, text: str) -> None:
    """Сохраняет текст подсказки в data-role без старой системной подсказки."""

    item.setData(FLUENT_ITEM_TOOLTIP_ROLE, str(text or "").strip())


__all__ = [
    "FLUENT_ITEM_TOOLTIP_ROLE",
    "FluentItemToolTipController",
    "FluentItemViewToolTipController",
    "install_fluent_item_tooltips",
    "set_fluent_item_tooltip",
]
