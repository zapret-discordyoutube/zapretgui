"""Окошко меню трея.

Одно окно на всю жизнь программы: оно создаётся при первом открытии меню и
дальше только показывается и прячется. Строки не являются виджетами — окно
само рисует те из них, что видны, поэтому длинный список пресетов стоит столько
же, сколько короткий. Подменю нет: «Пресет» и «Прозрачность» открываются
страницей в том же окне, со строкой «назад» сверху.
"""

from __future__ import annotations

import sys
import time
from bisect import bisect_right

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

from ui.popup_menu import VK_LBUTTON, VK_RBUTTON, _global_mouse_button_down
from ui.tray_menu import style
from ui.tray_menu.model import (
    MAIN_PAGE,
    ROW_BACK,
    ROW_ITEM,
    ROW_NOTE,
    ROW_SEARCH,
    ROW_SEPARATOR,
    ROW_STATUS,
    MenuPage,
    MenuRow,
    TrayMenuModel,
)


_HEAD_KINDS = frozenset({ROW_BACK, ROW_SEARCH})
_SELECTABLE_KINDS = frozenset({ROW_ITEM, ROW_STATUS, ROW_BACK})
# Сразу после показа Windows может на миг отдать активность обратно панели задач.
ACTIVATION_GRACE_SEC = 0.2
OUTSIDE_CLICK_POLL_MS = 50
WHEEL_ROWS = 3


def row_height(row: MenuRow) -> int:
    if row.kind == ROW_SEPARATOR:
        return style.SEPARATOR_HEIGHT
    if row.kind == ROW_STATUS:
        return style.STATUS_HEIGHT
    return style.ROW_HEIGHT


def menu_origin(*, anchor: QPoint, edge_y: int, opens_up: bool, width: int, height: int, available: QRect) -> QPoint:
    """Левый верхний угол меню размера width×height внутри области available.

    edge_y — край, который стоит на месте при смене страницы: нижний, когда
    меню раскрыто вверх от значка, и верхний, когда вниз.
    """
    gap = style.SCREEN_GAP
    x = min(anchor.x(), available.right() + 1 - width - gap)
    x = max(available.left() + gap, x)
    y = edge_y - height if opens_up else edge_y
    y = min(y, available.bottom() + 1 - height - gap)
    y = max(available.top() + gap, y)
    return QPoint(x, y)


class TrayMenuPopup(QWidget):
    """Меню трея: показывает страницы модели и сообщает выбранную команду."""

    commandTriggered = pyqtSignal(str, object)

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Меню Zapret")

        self._model = TrayMenuModel()
        self._page_key = MAIN_PAGE
        self._query = ""
        self._head: list[MenuRow] = []
        self._body: list[MenuRow] = []
        self._head_tops: list[int] = [0]
        self._body_tops: list[int] = [0]
        self._view_height = 0
        self._scroll = 0
        self._current = -1
        self._pressed = -1
        self._has_icons = True
        self._anchor = QPoint()
        self._edge_y = 0
        self._opens_up = True
        self._opened_at = 0.0
        self._palette = style.menu_palette()
        self._mouse_was_down = False
        self._outside_timer = QTimer(self)
        self._outside_timer.setInterval(OUTSIDE_CLICK_POLL_MS)
        self._outside_timer.timeout.connect(self._poll_outside_click)

        base = QFont(QApplication.font())
        base.setPixelSize(style.FONT_PX)
        self._font = base
        self._font_strong = QFont(base)
        self._font_strong.setWeight(QFont.Weight.DemiBold)
        self._font_small = QFont(base)
        self._font_small.setPixelSize(style.FONT_SMALL_PX)

    # ---- содержимое ------------------------------------------------------

    def set_model(self, model: TrayMenuModel) -> None:
        """Подменяет содержимое; открытое меню остаётся на своей странице."""
        self._model = model
        if self._page_key not in model.pages:
            self._page_key = MAIN_PAGE
            self._query = ""
        self._relayout()
        if self.isVisible():
            self._place()
            self._set_current(self._index_at(self.mapFromGlobal(QCursor.pos())))
            self.update()

    def page_key(self) -> str:
        return self._page_key

    def query(self) -> str:
        return self._query

    def rows(self) -> list[MenuRow]:
        """Строки текущей страницы в том виде, как они показаны (с учётом поиска)."""
        return [*self._head, *self._body]

    def current_row(self) -> MenuRow | None:
        rows = self.rows()
        return rows[self._current] if 0 <= self._current < len(rows) else None

    def open_page(self, key: str) -> None:
        self._page_key = key if key in self._model.pages else MAIN_PAGE
        self._query = ""
        self._scroll = 0
        self._current = -1
        self._relayout()
        self._scroll_to_checked()
        if self.isVisible():
            self._place()
        self.update()

    def set_query(self, text: str) -> None:
        self._query = str(text or "")
        self._scroll = 0
        self._current = -1
        self._relayout()
        self.update()

    def _page(self) -> MenuPage:
        return self._model.page(self._page_key)

    def _relayout(self) -> None:
        page = self._page()
        head = [row for row in page.rows if row.kind in _HEAD_KINDS]
        full_body = [row for row in page.rows if row.kind not in _HEAD_KINDS]
        body = full_body
        if self._query:
            needle = self._query.casefold()
            body = [row for row in full_body if row.kind == ROW_ITEM and needle in row.text.casefold()]
            if not body:
                body = [MenuRow(kind=ROW_NOTE, text=page.empty_text, enabled=False)]
        self._head, self._body = head, body
        self._head_tops = self._tops(head)
        self._body_tops = self._tops(body)
        self._has_icons = any(row.icon for row in full_body)
        # Высота окна считается по полному списку: при наборе в поиске оно не прыгает.
        full_height = sum(row_height(row) for row in full_body)
        self._view_height = min(full_height, self._max_view_height())
        self._scroll = max(0, min(self._scroll, self._body_tops[-1] - self._view_height))
        if self._current >= len(head) + len(body):
            self._current = -1

    @staticmethod
    def _tops(rows: list[MenuRow]) -> list[int]:
        tops = [0]
        for row in rows:
            tops.append(tops[-1] + row_height(row))
        return tops

    def _max_view_height(self) -> int:
        limit = self._available().height() - 2 * style.SCREEN_GAP - 2 * style.PADDING - self._head_tops[-1]
        if self._page_key != MAIN_PAGE:
            limit = min(limit, style.MAX_LIST_ROWS * style.ROW_HEIGHT)
        return max(style.ROW_HEIGHT, limit)

    def _scroll_to_checked(self) -> None:
        for index, row in enumerate(self._body):
            if row.checked:
                middle = self._body_tops[index] + row_height(row) // 2
                scroll = max(0, min(middle - self._view_height // 2, self._body_tops[-1] - self._view_height))
                # По границе строки: сверху не должно торчать полстроки.
                self._scroll = self._body_tops[bisect_right(self._body_tops, scroll) - 1]
                return

    # ---- показ и положение -------------------------------------------------

    def open_at(self, anchor: QPoint) -> None:
        self._palette = style.menu_palette()
        self._anchor = QPoint(anchor)
        available = self._available()
        self._opens_up = anchor.y() > available.center().y()
        gap = style.SCREEN_GAP
        self._edge_y = anchor.y() - gap if self._opens_up else anchor.y() + gap
        self._pressed = -1
        self.open_page(MAIN_PAGE)
        self._place()
        self._opened_at = time.monotonic()
        self.show()
        self.raise_()
        self.activateWindow()
        self._take_foreground()
        self.setFocus(Qt.FocusReason.PopupFocusReason)
        self._mouse_was_down = self._any_mouse_button_down()
        self._outside_timer.start()

    def _available(self) -> QRect:
        screen = QGuiApplication.screenAt(self._anchor) or QGuiApplication.primaryScreen()
        return QRect(0, 0, 1280, 720) if screen is None else screen.availableGeometry()

    def _content_height(self) -> int:
        return 2 * style.PADDING + self._head_tops[-1] + self._view_height

    def _place(self) -> None:
        height = self._content_height()
        origin = menu_origin(
            anchor=self._anchor,
            edge_y=self._edge_y,
            opens_up=self._opens_up,
            width=style.WIDTH,
            height=height,
            available=self._available(),
        )
        shadow = style.SHADOW
        self.setGeometry(origin.x() - shadow, origin.y() - shadow, style.WIDTH + 2 * shadow, height + 2 * shadow)

    def _take_foreground(self) -> None:
        # Без этого окно другой программы остаётся активным, и меню не узнаёт,
        # что пользователь щёлкнул мимо. Щелчок по значку даёт на это право.
        if sys.platform != "win32":
            return
        try:
            import ctypes

            ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
        except Exception:
            pass

    # ---- закрытие ------------------------------------------------------------

    def event(self, event) -> bool:  # noqa: N802 (Qt override)
        if (
            event.type() == QEvent.Type.WindowDeactivate
            and time.monotonic() - self._opened_at > ACTIVATION_GRACE_SEC
        ):
            self.hide()
        return super().event(event)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._outside_timer.stop()
        self._pressed = -1
        self._current = -1
        super().hideEvent(event)

    @staticmethod
    def _any_mouse_button_down() -> bool:
        return _global_mouse_button_down(VK_LBUTTON) or _global_mouse_button_down(VK_RBUTTON)

    def _poll_outside_click(self) -> None:
        """Страховка: щелчок мимо меню закрывает его, даже если окно не стало активным."""
        down = self._any_mouse_button_down()
        new_press = down and not self._mouse_was_down
        self._mouse_was_down = down
        if new_press and not self._content_rect().contains(self.mapFromGlobal(QCursor.pos())):
            self.hide()

    # ---- геометрия строк -------------------------------------------------------

    def _content_rect(self) -> QRect:
        shadow = style.SHADOW
        return QRect(shadow, shadow, style.WIDTH, self._content_height())

    def _body_rect(self) -> QRect:
        top = style.SHADOW + style.PADDING + self._head_tops[-1]
        return QRect(style.SHADOW, top, style.WIDTH, self._view_height)

    def _row_rect(self, index: int) -> QRect:
        head_count = len(self._head)
        if index < head_count:
            top = style.SHADOW + style.PADDING + self._head_tops[index]
            return QRect(style.SHADOW, top, style.WIDTH, row_height(self._head[index]))
        body_index = index - head_count
        top = self._body_rect().top() + self._body_tops[body_index] - self._scroll
        return QRect(style.SHADOW, top, style.WIDTH, row_height(self._body[body_index]))

    def _index_at(self, pos: QPoint) -> int:
        if not self._content_rect().contains(pos):
            return -1
        head_y = pos.y() - style.SHADOW - style.PADDING
        if 0 <= head_y < self._head_tops[-1]:
            return bisect_right(self._head_tops, head_y) - 1
        body = self._body_rect()
        if not body.contains(pos):
            return -1
        body_y = pos.y() - body.top() + self._scroll
        if body_y >= self._body_tops[-1]:
            return -1
        return len(self._head) + bisect_right(self._body_tops, body_y) - 1

    @staticmethod
    def _selectable(row: MenuRow) -> bool:
        return row.enabled and row.kind in _SELECTABLE_KINDS

    def _set_current(self, index: int) -> None:
        rows = self.rows()
        if not (0 <= index < len(rows)) or not self._selectable(rows[index]):
            index = -1
        if index == self._current:
            return
        self._current = index
        self.setAccessibleDescription(rows[index].text if index >= 0 else "")
        self.update()

    def _ensure_visible(self, index: int) -> None:
        body_index = index - len(self._head)
        if body_index < 0:
            return
        top = self._body_tops[body_index]
        bottom = self._body_tops[body_index + 1]
        if top < self._scroll:
            self._scroll = top
        elif bottom > self._scroll + self._view_height:
            self._scroll = bottom - self._view_height

    def _scroll_by(self, delta: int) -> None:
        limit = max(0, self._body_tops[-1] - self._view_height)
        scroll = max(0, min(self._scroll + delta, limit))
        if scroll != self._scroll:
            self._scroll = scroll
            self.update()

    # ---- действия --------------------------------------------------------------

    def activate_row(self, row: MenuRow) -> None:
        if not self._selectable(row):
            return
        if row.kind == ROW_BACK:
            self.open_page(MAIN_PAGE)
            return
        if row.page:
            self.open_page(row.page)
            return
        if row.command:
            self.hide()
            # Команда выполняется уже после того, как меню исчезло с экрана.
            QTimer.singleShot(0, lambda: self.commandTriggered.emit(row.command, row.arg))

    def _move_current(self, step: int) -> None:
        rows = self.rows()
        if not rows:
            return
        index = self._current
        if index < 0 and step < 0:
            index = 0
        for _ in rows:
            index = (index + step) % len(rows)
            if self._selectable(rows[index]):
                self._ensure_visible(index)
                self._set_current(index)
                self.update()
                return

    # ---- мышь и клавиатура -----------------------------------------------------

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._set_current(self._index_at(event.position().toPoint()))

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._set_current(-1)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = self._index_at(event.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        index = self._index_at(event.position().toPoint())
        pressed, self._pressed = self._pressed, -1
        self.update()
        rows = self.rows()
        if index == pressed and 0 <= index < len(rows):
            self.activate_row(rows[index])

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt override)
        steps = event.angleDelta().y() / 120.0
        self._scroll_by(-round(steps * WHEEL_ROWS * style.ROW_HEIGHT))
        self._set_current(self._index_at(event.position().toPoint()))

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        key = event.key()
        row = self.current_row()
        searchable = any(item.kind == ROW_SEARCH for item in self._head)
        typed = event.text()
        if key == Qt.Key.Key_Escape:
            if self._query:
                self.set_query("")
            elif self._page_key != MAIN_PAGE:
                self.open_page(MAIN_PAGE)
            else:
                self.hide()
        elif key == Qt.Key.Key_Down:
            self._move_current(1)
        elif key == Qt.Key.Key_Up:
            self._move_current(-1)
        elif key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
            self._scroll_by(self._view_height if key == Qt.Key.Key_PageDown else -self._view_height)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if row is not None:
                self.activate_row(row)
        elif key == Qt.Key.Key_Backspace and self._query:
            self.set_query(self._query[:-1])
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace):
            if self._page_key != MAIN_PAGE:
                self.open_page(MAIN_PAGE)
        elif key == Qt.Key.Key_Right:
            if row is not None and row.page:
                self.activate_row(row)
        elif searchable and typed and typed.isprintable() and (typed.strip() or self._query):
            self.set_query(self._query + typed)
        elif key == Qt.Key.Key_Space and row is not None:
            self.activate_row(row)
        else:
            super().keyPressEvent(event)

    # ---- рисование -------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self._palette
        content = QRectF(self._content_rect())

        self._paint_shadow(painter, content)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(palette.background)
        painter.drawRoundedRect(content, style.RADIUS, style.RADIUS)
        painter.setPen(QPen(palette.border, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(content.adjusted(0.5, 0.5, -0.5, -0.5), style.RADIUS, style.RADIUS)

        for index, row in enumerate(self._head):
            self._paint_row(painter, row, self._row_rect(index), index)

        body = self._body_rect()
        painter.save()
        painter.setClipRect(body)
        first = max(0, bisect_right(self._body_tops, self._scroll) - 1)
        for body_index in range(first, len(self._body)):
            if self._body_tops[body_index] - self._scroll >= self._view_height:
                break
            index = len(self._head) + body_index
            self._paint_row(painter, self._body[body_index], self._row_rect(index), index)
        painter.restore()
        self._paint_scroll_thumb(painter, body)
        painter.end()

    @staticmethod
    def _paint_shadow(painter: QPainter, content: QRectF) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for step in range(1, style.SHADOW):
            fade = 1.0 - step / style.SHADOW
            color = QColor(0, 0, 0)
            color.setAlphaF(0.085 * fade * fade)
            painter.setPen(QPen(color, 1.0))
            # Тень смещена вниз: свет «сверху», как у окон Windows.
            rect = content.adjusted(-step, -step * 0.6, step, step * 1.2).translated(0, 2)
            painter.drawRoundedRect(rect, style.RADIUS + step, style.RADIUS + step)

    def _paint_scroll_thumb(self, painter: QPainter, body: QRect) -> None:
        total = self._body_tops[-1]
        if total <= self._view_height:
            return
        height = max(24.0, self._view_height * self._view_height / total)
        travel = self._view_height - height
        top = body.top() + travel * self._scroll / (total - self._view_height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._palette.scroll_thumb)
        painter.drawRoundedRect(QRectF(body.right() - 5.0, top + 2.0, 3.0, height - 4.0), 1.5, 1.5)

    def _paint_row(self, painter: QPainter, row: MenuRow, rect: QRect, index: int) -> None:
        palette = self._palette
        if row.kind == ROW_SEPARATOR:
            y = rect.center().y() + 0.5
            painter.setPen(QPen(palette.separator, 1.0))
            painter.drawLine(QPointF(rect.left() + 1, y), QPointF(rect.right(), y))
            return

        if index == self._current and self._selectable(row):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(palette.pressed if index == self._pressed else palette.hover)
            inset = style.HOVER_INSET
            painter.drawRoundedRect(
                QRectF(rect).adjusted(inset, 1, -inset, -1), style.HOVER_RADIUS, style.HOVER_RADIUS
            )

        if row.kind == ROW_STATUS:
            self._paint_status(painter, row, rect)
            return

        text_color = palette.text if row.enabled else palette.disabled
        left = rect.left() + (style.TEXT_LEFT if self._has_icons else style.TEXT_LEFT_PLAIN)
        right = rect.right() - style.SIDE_GAP
        icon_rect = QRectF(
            rect.left() + style.ICON_LEFT,
            rect.top() + (rect.height() - style.ICON_SIZE) / 2,
            style.ICON_SIZE,
            style.ICON_SIZE,
        )
        font = self._font

        if row.kind == ROW_BACK:
            style.paint_tray_icon(painter, "chevron_left", icon_rect, palette.muted)
            left = rect.left() + style.TEXT_LEFT
            font = self._font_strong
        elif row.kind == ROW_SEARCH:
            style.paint_tray_icon(painter, "search", icon_rect, palette.muted)
            left = rect.left() + style.TEXT_LEFT
            self._draw_text(
                painter,
                self._query or row.text,
                QRect(left, rect.top(), right - left, rect.height()),
                self._font,
                palette.text if self._query else palette.muted,
            )
            line_y = rect.bottom() - 2.5
            painter.setPen(QPen(palette.accent if self._query else palette.separator, 1.0))
            painter.drawLine(QPointF(left, line_y), QPointF(right, line_y))
            return
        elif row.kind == ROW_NOTE:
            text_color = palette.muted
        elif row.icon:
            style.paint_tray_icon(painter, row.icon, icon_rect, text_color)

        if row.checked:
            # Активный пресет: цветная метка слева и более плотный шрифт.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(palette.accent)
            painter.drawRoundedRect(QRectF(rect.left() + 6.0, rect.center().y() - 7.0, 3.0, 15.0), 1.5, 1.5)
            font = self._font_strong

        if row.page:
            chevron = QRectF(right - 12, rect.top() + (rect.height() - 12) / 2, 12, 12)
            style.paint_tray_icon(painter, "chevron_right", chevron, palette.muted)
            right -= 18
        if row.detail:
            # Справа — подсказка, например имя выбранного пресета; ей до половины строки.
            metrics = QFontMetrics(self._font_small)
            detail_width = min(metrics.horizontalAdvance(row.detail), (right - left) // 2)
            detail_rect = QRect(right - detail_width, rect.top(), detail_width, rect.height())
            self._draw_text(painter, row.detail, detail_rect, self._font_small, palette.muted, align_right=True)
            right -= detail_width + 10
        self._draw_text(painter, row.text, QRect(left, rect.top(), right - left, rect.height()), font, text_color)

    def _paint_status(self, painter: QPainter, row: MenuRow, rect: QRect) -> None:
        palette = self._palette
        color = QColor(row.color) if row.color else palette.idle_dot
        center = QPointF(rect.left() + style.ICON_LEFT + style.ICON_SIZE / 2, rect.center().y() + 0.5)
        halo = QColor(color)
        halo.setAlphaF(0.28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, 6.5, 6.5)
        painter.setBrush(color)
        painter.drawEllipse(center, 3.8, 3.8)

        left = rect.left() + style.TEXT_LEFT
        width = rect.right() - style.SIDE_GAP - left
        if not row.detail:
            self._draw_text(painter, row.text, QRect(left, rect.top(), width, rect.height()), self._font_strong, palette.text)
            return
        middle = rect.center().y() + 1
        self._draw_text(painter, row.text, QRect(left, middle - 19, width, 19), self._font_strong, palette.text)
        self._draw_text(painter, row.detail, QRect(left, middle, width, 17), self._font_small, palette.muted)

    @staticmethod
    def _draw_text(
        painter: QPainter,
        text: str,
        rect: QRect,
        font: QFont,
        color: QColor,
        *,
        align_right: bool = False,
    ) -> None:
        if rect.width() <= 0 or not text:
            return
        painter.setFont(font)
        painter.setPen(color)
        elided = QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
        horizontal = Qt.AlignmentFlag.AlignRight if align_right else Qt.AlignmentFlag.AlignLeft
        painter.drawText(rect, int(horizontal | Qt.AlignmentFlag.AlignVCenter), elided)


__all__ = ["TrayMenuPopup", "menu_origin", "row_height"]
