"""Кнопки «назад» и «вперёд» в шапке окна.

«Назад» — стрелка самой библиотеки в углу бокового меню: здесь она отвязана
от библиотечного списка страниц и ходит по журналу экранов окна
(``ui.navigation.history_controller``). «Вперёд» стоит рядом и видна, только
пока есть куда вернуться после «назад». Долгое нажатие или правая кнопка мыши
показывают список экранов, как в браузере.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from qfluentwidgets import Action, FluentIcon, RoundMenu
from qfluentwidgets.common.router import qrouter
from qfluentwidgets.components.navigation.navigation_widget import NavigationToolButton

from app.ui_texts import tr as tr_catalog
from ui.accessibility import set_control_accessibility
from ui.fluent_widgets import set_tooltip
from ui.navigation.history import HistoryEntry
from ui.popup_menu import exec_popup_menu
from ui.window_ui_session import get_window_ui_session


# Столько держат кнопку, чтобы вместо перехода открылся список истории.
LONG_PRESS_MS = 450
# Столько экранов показывает список; остальные достаются повторным «назад».
MENU_LIMIT = 15


class TitleBarHistoryButton(NavigationToolButton):
    """Стрелка в шапке окна.

    Шапка двигает окно за любое место, где событие мыши никто не забрал, а двойным
    нажатием разворачивает его. Кнопка забирает свои события, иначе нажатие на неё
    с малейшим сдвигом мыши начинало бы перетаскивание окна.
    """

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().mousePressEvent(event)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().mouseReleaseEvent(event)
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt override)
        event.accept()


class HistoryButtonGesture(QObject):
    """Отличает на кнопке долгое нажатие и правую кнопку мыши от обычного нажатия."""

    def __init__(self, button, show_menu: Callable[[], None]) -> None:
        super().__init__(button)
        self._button = button
        self._show_menu = show_menu
        self._menu_shown = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(LONG_PRESS_MS)
        self._timer.timeout.connect(self._open_menu)
        button.installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802 (Qt override)
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.RightButton:
                self._open_menu()
                return True
            if event.button() == Qt.MouseButton.LeftButton:
                self._menu_shown = False
                self._timer.start()
        elif event_type == QEvent.Type.MouseButtonRelease:
            self._timer.stop()
            if self._menu_shown:
                # Список уже показан: отпускание кнопки не должно ещё и перейти назад.
                self._menu_shown = False
                return True
        elif event_type in (QEvent.Type.Hide, QEvent.Type.EnabledChange):
            self._timer.stop()
        return super().eventFilter(obj, event)

    def _open_menu(self) -> None:
        self._timer.stop()
        if not self._button.isEnabled():
            return
        self._menu_shown = True
        self._button.isPressed = False
        self._button.update()
        self._show_menu()


class HistoryButtons(QObject):
    def __init__(self, window, navigation_history) -> None:
        super().__init__(window)
        self._window = window
        self._navigation = navigation_history
        self.back_button = window.navigationInterface.panel.returnButton
        self.forward_button = None
        self._take_over_back_button()
        self._build_forward_button()
        self._gestures = [HistoryButtonGesture(self.back_button, lambda: self.show_menu(forward=False))]
        if self.forward_button is not None:
            self._gestures.append(HistoryButtonGesture(self.forward_button, lambda: self.show_menu(forward=True)))
        self._shortcuts = []
        for keys, handler in (("Alt+Left", self._navigation.go_back), ("Alt+Right", self._navigation.go_forward)):
            shortcut = QShortcut(QKeySequence(keys), window)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)
        navigation_history.history.subscribe(self.sync)
        self.sync()

    def _take_over_back_button(self) -> None:
        # Библиотека вела свой список страниц (qrouter) и сама решала, активна ли стрелка.
        # Теперь это делает журнал экранов окна; библиотечный список ни на что не влияет.
        for signal, slot in ((self.back_button.clicked, qrouter.pop), (qrouter.emptyChanged, self.back_button.setDisabled)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self.back_button.clicked.connect(lambda _by_user=True: self._navigation.go_back())

    def _build_forward_button(self) -> None:
        title_bar = getattr(self._window, "titleBar", None)
        layout = getattr(title_bar, "hBoxLayout", None)
        if layout is None:
            return
        button = TitleBarHistoryButton(FluentIcon.RIGHT_ARROW, title_bar)
        button.clicked.connect(lambda _by_user=True: self._navigation.go_forward())
        layout.insertWidget(0, button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        button.setVisible(False)
        self.forward_button = button

    def _text(self, key: str, default: str) -> str:
        session = get_window_ui_session(self._window)
        language = None if session is None else session.ui_language
        return tr_catalog(key, language=language, default=default)

    def sync(self) -> None:
        """Приводит кнопки к журналу: «назад» активна, «вперёд» видна — когда есть куда идти."""
        self.back_button.setEnabled(self._navigation.can_go_back())
        back_text = self._text("nav.history.back.tooltip", "Назад. Удерживайте, чтобы увидеть историю")
        self.back_button.setToolTip(back_text)
        set_control_accessibility(self.back_button, name=back_text)
        button = self.forward_button
        if button is None:
            return
        forward_text = self._text("nav.history.forward.tooltip", "Вперёд. Удерживайте, чтобы увидеть историю")
        set_tooltip(button, forward_text)
        set_control_accessibility(button, name=forward_text)
        visible = self._navigation.can_go_forward()
        if button.isHidden() == visible:
            button.setVisible(visible)
            # Поиск в шапке стоит по центру между соседями: их ширина изменилась.
            from ui.window_adapter import refresh_titlebar_layout

            refresh_titlebar_layout(self._window)

    def menu_entries(self, *, forward: bool) -> tuple[HistoryEntry, ...]:
        self._navigation.commit()
        history = self._navigation.history
        pairs = history.forward_entries() if forward else history.back_entries()
        return tuple(entry for _index, entry in pairs[:MENU_LIMIT])

    def show_menu(self, *, forward: bool) -> None:
        entries = self.menu_entries(forward=forward)
        button = self.forward_button if forward else self.back_button
        if not entries or button is None:
            return
        session = get_window_ui_session(self._window)
        menu = RoundMenu(parent=self._window)
        targets: dict[object, HistoryEntry] = {}
        for entry in entries:
            icon = None
            if session is not None:
                icon = session.nav_icons.get(entry.page) or session.default_nav_icon
            text = self._navigation.describe(entry)
            action = Action(icon, text, menu) if icon is not None else Action(text, menu)
            menu.addAction(action)
            targets[action] = entry
        position = button.mapToGlobal(QPoint(0, button.height() + 2))
        chosen = exec_popup_menu(menu, position, owner=self._window, capture_action=True)
        entry = targets.get(chosen)
        if entry is not None:
            self._navigation.go_to(entry)


def install_history_buttons(window) -> HistoryButtons | None:
    session = get_window_ui_session(window)
    if session is None or getattr(window, "navigationInterface", None) is None:
        return None
    buttons = HistoryButtons(window, session.page_host.navigation_history)
    session.history_buttons = buttons
    return buttons


__all__ = ["HistoryButtons", "HistoryButtonGesture", "TitleBarHistoryButton", "install_history_buttons"]
