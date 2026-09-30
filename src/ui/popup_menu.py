from __future__ import annotations

import ctypes
import sys
from typing import Callable

from PyQt6.QtCore import QEvent, QEventLoop, QObject, QPoint, QTimer, Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QWidget


if sys.platform == "win32":
    user32 = ctypes.windll.user32
    VK_LBUTTON = 0x01
    VK_RBUTTON = 0x02
    try:
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
    except Exception:
        pass
else:
    user32 = None
    VK_LBUTTON = 0
    VK_RBUTTON = 0


def _widget_contains_global_pos(widget: QWidget | None, global_pos: QPoint | None) -> bool:
    if widget is None or global_pos is None:
        return False

    try:
        if not widget.isVisible():
            return False
        top_left = widget.mapToGlobal(widget.rect().topLeft())
        bottom_right = widget.mapToGlobal(widget.rect().bottomRight())
        return (
            top_left.x() <= global_pos.x() <= bottom_right.x()
            and top_left.y() <= global_pos.y() <= bottom_right.y()
        )
    except Exception:
        return False


def _global_mouse_button_down(vk_code: int) -> bool:
    if sys.platform != "win32" or user32 is None:
        return False

    try:
        return bool(user32.GetAsyncKeyState(int(vk_code)) & 0x8000)
    except Exception:
        return False


class PopupMenuLifecycleFilter(QObject):
    """Закрывает popup-меню при потере активности окна или приложения."""

    def __init__(self, menu: QWidget, owner_window: QWidget | None, app: QApplication | None, on_close: Callable[[], None]) -> None:
        super().__init__(menu)
        self._menu = menu
        self._owner_window = owner_window
        self._app = app
        self._on_close = on_close

    def _event_global_pos(self, event) -> QPoint | None:
        try:
            global_pos = event.globalPosition()
            return global_pos.toPoint()
        except Exception:
            pass

        try:
            global_pos = event.globalPos()
            return QPoint(int(global_pos.x()), int(global_pos.y()))
        except Exception:
            return None

    def _menu_contains_global_pos(self, global_pos: QPoint | None) -> bool:
        return _widget_contains_global_pos(self._menu, global_pos)

    def eventFilter(self, obj, event):  # noqa: N802 (Qt override)
        event_type = event.type()

        if obj is self._menu and event_type in (
            QEvent.Type.Hide,
            QEvent.Type.Close,
            QEvent.Type.FocusOut,
            QEvent.Type.WindowDeactivate,
        ):
            self._on_close()
            return False

        if obj is self._app and event_type in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
        ):
            if self._menu.isVisible() and not self._menu_contains_global_pos(self._event_global_pos(event)):
                self._on_close()
            return False

        if obj is self._owner_window and event_type in (
            QEvent.Type.Hide,
            QEvent.Type.Close,
            QEvent.Type.WindowDeactivate,
        ):
            self._on_close()
            return False

        return super().eventFilter(obj, event)


def exec_popup_menu(
    menu: QWidget,
    pos: QPoint | None = None,
    *,
    owner: QWidget | None,
    capture_action: bool = False,
    monitor_global_mouse: bool = False,
) -> object | None:
    """Показывает одноразовое popup-меню, закрывает его при потере активности и удаляет после закрытия."""

    chosen_action: dict[str, object | None] = {"value": None}
    finished = {"value": False}
    loop = QEventLoop(menu)

    def _finish(action: object | None = None) -> None:
        if action is not None and chosen_action["value"] is None:
            chosen_action["value"] = action
        if finished["value"]:
            return
        finished["value"] = True
        try:
            menu.hide()
        except Exception:
            pass
        if loop.isRunning():
            loop.quit()

    owner_window = owner.window() if owner is not None else None
    app = QApplication.instance()
    lifecycle_filter = PopupMenuLifecycleFilter(menu, owner_window, app, on_close=_finish)
    menu.installEventFilter(lifecycle_filter)
    if owner_window is not None and owner_window is not menu:
        owner_window.installEventFilter(lifecycle_filter)

    state_handler = None
    mouse_watch_timer = None
    if app is not None:
        app.installEventFilter(lifecycle_filter)

        def _on_ui_state_changed(state) -> None:
            if state != Qt.ApplicationState.ApplicationActive:
                _finish()

        state_handler = _on_ui_state_changed
        app.applicationStateChanged.connect(state_handler)

    if monitor_global_mouse and sys.platform == "win32":
        mouse_watch_timer = QTimer(menu)
        mouse_watch_timer.setInterval(35)

        mouse_state = {
            "left": _global_mouse_button_down(VK_LBUTTON),
            "right": _global_mouse_button_down(VK_RBUTTON),
        }

        def _poll_global_mouse() -> None:
            if finished["value"] or not menu.isVisible():
                _finish()
                return

            cursor_pos = QCursor.pos()
            inside_menu = _widget_contains_global_pos(menu, cursor_pos)

            left_down = _global_mouse_button_down(VK_LBUTTON)
            right_down = _global_mouse_button_down(VK_RBUTTON)

            new_left_click = left_down and not mouse_state["left"]
            new_right_click = right_down and not mouse_state["right"]

            mouse_state["left"] = left_down
            mouse_state["right"] = right_down

            if (new_left_click or new_right_click) and not inside_menu:
                _finish()

        mouse_watch_timer.timeout.connect(_poll_global_mouse)
        mouse_watch_timer.start()

    for action in menu.actions():
        try:
            if capture_action:
                action.triggered.connect(lambda _checked=False, action=action: _finish(action))
            else:
                action.triggered.connect(lambda _checked=False: _finish())
        except Exception:
            pass

    final_pos = pos or QCursor.pos()

    try:
        menu.exec(final_pos)
        if not finished["value"] and menu.isVisible():
            loop.exec()
    finally:
        try:
            menu.removeEventFilter(lifecycle_filter)
        except Exception:
            pass
        if owner_window is not None and owner_window is not menu:
            try:
                owner_window.removeEventFilter(lifecycle_filter)
            except Exception:
                pass
        if app is not None:
            try:
                app.removeEventFilter(lifecycle_filter)
            except Exception:
                pass
        if app is not None and state_handler is not None:
            try:
                app.applicationStateChanged.disconnect(state_handler)
            except Exception:
                pass
        if mouse_watch_timer is not None:
            try:
                mouse_watch_timer.stop()
            except Exception:
                pass
        # Меню создаётся на каждый показ с родителем-страницей или окном, а
        # RoundMenu сам себя не удаляет: без этого каждое открытие оставляло бы
        # в памяти меню со всеми пунктами до закрытия программы.
        try:
            menu.deleteLater()
        except RuntimeError:
            pass

    return chosen_action["value"]
