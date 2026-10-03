"""Защита от «родных» подсказок Qt.

Обычная подсказка Qt (текст виджета или строки списка/таблицы) рисуется
системным окошком. В оформлении программы оно выходит чёрным прямоугольником
без текста. Здесь один фильтр на всё приложение: он получает событие
«показать подсказку» раньше самого виджета и показывает тот же текст
fluent-подсказкой у курсора, а родное окошко так и не создаётся.

Подсказки, у которых уже есть fluent-показ (``set_tooltip``, таблицы
qfluentwidgets, ``install_fluent_item_tooltips``, свои delegate), фильтр не
трогает. Если родное окошко всё же появилось другим путём, оно сразу
закрывается, а его текст переносится в fluent-подсказку.
"""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QWidget


_NATIVE_TIP_CLASS = "QTipLabel"
_TIP_DURATION_MS = 10000
_APP_ATTR = "_zapret_native_tooltip_guard"
_WINDOW_TIP_ATTR = "_native_tooltip_guard_tip"

# WindowDeactivate сюда не входит: окошко подсказки при показе само может на
# миг забрать активность у главного окна, и подсказка прятала бы сама себя.
_HIDE_ON_ANY_OBJECT = frozenset(
    {
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.Wheel,
        QEvent.Type.KeyPress,
        QEvent.Type.ApplicationDeactivate,
    }
)
_HIDE_ON_SOURCE = frozenset({QEvent.Type.Leave, QEvent.Type.Hide})


class NativeToolTipGuard(QObject):
    """Показывает любую обычную подсказку Qt fluent-подсказкой."""

    def __init__(self, app: QApplication):
        super().__init__(app)
        self._app = app
        self._source: QWidget | None = None
        self._text = ""
        self._controller = None
        self._native_sweep_pending = False
        app.installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802 (Qt override)
        try:
            kind = event.type()
            if kind == QEvent.Type.ToolTip:
                return self._show_instead_of_native(obj, event)
            if kind == QEvent.Type.Show:
                if obj.inherits(_NATIVE_TIP_CLASS):
                    self._schedule_native_sweep()
                return False
            if self._source is None:
                return False
            if kind in _HIDE_ON_ANY_OBJECT:
                self.hide()
            elif obj is self._source:
                if kind in _HIDE_ON_SOURCE:
                    self.hide()
                elif kind == QEvent.Type.ToolTipChange and obj.toolTip() != self._text:
                    # Текст сменился (курсор ушёл на другую область виджета).
                    self.hide()
        except Exception:
            pass
        return False

    def hide(self) -> None:
        controller, self._controller = self._controller, None
        self._source = None
        self._text = ""
        if controller is not None and not sip.isdeleted(controller):
            controller.hide()

    def cleanup(self) -> None:
        self.hide()
        app, self._app = self._app, None
        if app is None:
            return
        app.removeEventFilter(self)
        if getattr(app, _APP_ATTR, None) is self:
            setattr(app, _APP_ATTR, None)

    # ── показ вместо родной подсказки ────────────────────────

    def _show_instead_of_native(self, obj, event) -> bool:
        if not isinstance(obj, QWidget):
            return False
        text = self._native_text(obj, event.pos())
        if not text:
            return False
        self._show(obj, text, event.globalPos())
        return True

    def _native_text(self, widget: QWidget, pos: QPoint) -> str:
        """Текст, который Qt показал бы родной подсказкой; пусто — не наш случай."""
        view = widget.parent()
        if isinstance(view, QAbstractItemView) and view.viewport() is widget:
            if _has_fluent_tooltip(view):
                return ""
            index = view.indexAt(pos)
            if not index.isValid():
                return ""
            return str(index.data(Qt.ItemDataRole.ToolTipRole) or "")
        if _has_fluent_tooltip(widget):
            return ""
        return str(widget.toolTip() or "")

    def _show(self, source: QWidget, text: str, global_pos: QPoint) -> None:
        if source is self._source and text == self._text and self._is_shown():
            return
        from ui.widgets.fluent_item_tooltip import FluentItemToolTipController

        window = source.window()
        controller = getattr(window, _WINDOW_TIP_ATTR, None)
        if controller is None or sip.isdeleted(controller):
            controller = FluentItemToolTipController(window, duration=_TIP_DURATION_MS)
            setattr(window, _WINDOW_TIP_ATTR, controller)
        if self._controller is not None and self._controller is not controller:
            self.hide()
        controller.show_text(text, global_pos)
        self._source, self._text, self._controller = source, text, controller

    def _is_shown(self) -> bool:
        controller = self._controller
        return controller is not None and not sip.isdeleted(controller) and controller.is_visible()

    # ── запасной путь: родное окошко всё же появилось ────────

    def _schedule_native_sweep(self) -> None:
        if self._native_sweep_pending:
            return
        self._native_sweep_pending = True
        QTimer.singleShot(0, self._replace_shown_native_tips)

    def _replace_shown_native_tips(self) -> None:
        self._native_sweep_pending = False
        text = ""
        for label in QApplication.topLevelWidgets():
            if not label.inherits(_NATIVE_TIP_CLASS):
                continue
            text = text or str(label.property("text") or "")
            # Так же родную подсказку закрывает сам Qt.
            label.close()
            label.deleteLater()
        if not text:
            return
        global_pos = QCursor.pos()
        source = QApplication.widgetAt(global_pos) or QApplication.activeWindow()
        if source is not None:
            self._show(source, text, global_pos)


def _has_fluent_tooltip(widget: QWidget) -> bool:
    """У виджета уже стоит fluent-подсказка qfluentwidgets — она покажет текст сама."""
    from qfluentwidgets import ToolTipFilter

    return any(isinstance(child, ToolTipFilter) for child in widget.children())


def install_native_tooltip_guard(app: QApplication | None = None) -> NativeToolTipGuard | None:
    qapp = app or QApplication.instance()
    if qapp is None:
        return None
    existing = getattr(qapp, _APP_ATTR, None)
    if isinstance(existing, NativeToolTipGuard):
        return existing
    guard = NativeToolTipGuard(qapp)
    setattr(qapp, _APP_ATTR, guard)
    return guard


__all__ = ["NativeToolTipGuard", "install_native_tooltip_guard"]
