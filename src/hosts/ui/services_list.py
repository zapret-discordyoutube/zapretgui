"""Список сервисов страницы Hosts: один рисуемый виджет вместо сотни строк-виджетов.

Рисуются только видимые строки. Вся строка — одна большая кнопка: у сервиса
«напрямую» щелчок переключает его, у DNS-сервиса открывает выбор профиля.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ui.accessibility import set_control_accessibility
from ui.theme import get_cached_qta_pixmap, get_theme_tokens, to_qcolor


@dataclass(frozen=True, slots=True)
class HostsListRow:
    """Одна строка списка. kind: "group" | "service" | "empty"."""

    kind: str
    title: str
    service_name: str = ""
    icon_name: str = ""
    icon_color: str | None = None
    is_direct: bool = False
    is_on: bool = False
    value_text: str = ""
    hint: str = ""
    changed: bool = False
    enabled: bool = True
    accessible_text: str = ""


class HostsServicesList(QWidget):
    """Лёгкий рисуемый список. Данные задаёт страница через set_rows()."""

    # (имя сервиса, точка на экране для меню профиля)
    activated = pyqtSignal(str, QPoint)

    GROUP_HEIGHT = 40
    ROW_HEIGHT = 44
    HINT_ROW_HEIGHT = 56
    _ICON_SIZE = 18
    _PAD = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[HostsListRow] = []
        self._tops: list[int] = []
        self._heights: list[int] = []
        self._hover = -1
        self._cursor = -1
        self.setObjectName("hostsServicesList")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        set_control_accessibility(self, name="Список сервисов hosts")

    # ── данные ───────────────────────────────────────────────

    def rows(self) -> list[HostsListRow]:
        return list(self._rows)

    def set_rows(self, rows: list[HostsListRow]) -> None:
        cursor_service = self._service_at(self._cursor)
        self._rows = list(rows)
        self._tops = []
        self._heights = []
        y = 0
        for row in self._rows:
            if row.kind == "group":
                height = self.GROUP_HEIGHT
            elif row.kind == "service" and row.hint:
                height = self.HINT_ROW_HEIGHT
            else:
                height = self.ROW_HEIGHT
            self._tops.append(y)
            self._heights.append(height)
            y += height
        self.setFixedHeight(max(y, self.ROW_HEIGHT))
        self._hover = -1
        self._cursor = self._row_of_service(cursor_service)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(480, self.height())

    # ── геометрия ────────────────────────────────────────────

    def _service_at(self, index: int) -> str:
        if 0 <= index < len(self._rows) and self._rows[index].kind == "service":
            return self._rows[index].service_name
        return ""

    def _row_of_service(self, service_name: str) -> int:
        if not service_name:
            return -1
        for index, row in enumerate(self._rows):
            if row.kind == "service" and row.service_name == service_name:
                return index
        return -1

    def _row_rect(self, index: int) -> QRect:
        if not 0 <= index < len(self._rows):
            return QRect()
        return QRect(0, self._tops[index], self.width(), self._heights[index])

    def row_at(self, y: int) -> int:
        for index, top in enumerate(self._tops):
            if top <= y < top + self._heights[index]:
                return index
            if top > y:
                break
        return -1

    def _is_clickable(self, index: int) -> bool:
        return 0 <= index < len(self._rows) and self._rows[index].kind == "service" and self._rows[index].enabled

    def _clickable_rows(self) -> list[int]:
        return [index for index in range(len(self._rows)) if self._is_clickable(index)]

    def _control_rect(self, index: int) -> QRect:
        rect = self._row_rect(index)
        width = 56 if self._rows[index].is_direct else 190
        return QRect(rect.right() - self._PAD - width, rect.top(), width, rect.height())

    # ── отрисовка ────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        tokens = get_theme_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dirty = event.rect()
        for index, top in enumerate(self._tops):
            bottom = top + self._heights[index]
            if bottom < dirty.top():
                continue
            if top > dirty.bottom():
                break
            row = self._rows[index]
            if row.kind == "group":
                self._paint_group(painter, index, row, tokens)
            elif row.kind == "empty":
                self._paint_empty(painter, index, row, tokens)
            else:
                self._paint_service(painter, index, row, tokens)
        painter.end()

    def _paint_group(self, painter: QPainter, index: int, row: HostsListRow, tokens) -> None:
        rect = self._row_rect(index).adjusted(self._PAD, 10, -self._PAD, 0)
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(to_qcolor(tokens.fg_muted))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), row.title)

    def _paint_empty(self, painter: QPainter, index: int, row: HostsListRow, tokens) -> None:
        painter.setFont(self.font())
        painter.setPen(to_qcolor(tokens.fg_faint))
        painter.drawText(self._row_rect(index), int(Qt.AlignmentFlag.AlignCenter), row.title)

    def _paint_service(self, painter: QPainter, index: int, row: HostsListRow, tokens) -> None:
        rect = self._row_rect(index)
        body = rect.adjusted(6, 2, -6, -2)
        highlighted = row.enabled and (index == self._hover or (self.hasFocus() and index == self._cursor))
        if highlighted:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(to_qcolor(tokens.surface_bg_hover, tokens.surface_bg))
            painter.drawRoundedRect(QRectF(body), 6, 6)
        if row.changed:
            # Изменено в черновике: мягкая подложка и метка акцентного цвета.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(to_qcolor(tokens.accent_soft_bg))
            painter.drawRoundedRect(QRectF(body), 6, 6)
            painter.setBrush(to_qcolor(tokens.accent_hex))
            painter.drawRoundedRect(QRectF(body.left(), body.top() + 8, 3, body.height() - 16), 1.5, 1.5)

        dim = not row.enabled
        icon_color = row.icon_color if (row.icon_color and row.is_on and not dim) else tokens.icon_fg_muted
        pixmap = get_cached_qta_pixmap(row.icon_name or "fa5s.globe", color=icon_color, size=self._ICON_SIZE)
        text_left = rect.left() + self._PAD + self._ICON_SIZE + 12
        control = self._control_rect(index)
        text_width = max(40, control.left() - 12 - text_left)

        name_font = QFont(self.font())
        metrics = QFontMetrics(name_font)
        if row.hint:
            name_rect = QRect(text_left, rect.top() + 8, text_width, metrics.height())
            hint_rect = QRect(text_left, name_rect.bottom() + 2, text_width, metrics.height())
        else:
            name_rect = QRect(text_left, rect.top(), text_width, rect.height())
            hint_rect = QRect()
        icon_y = name_rect.center().y() - self._ICON_SIZE // 2
        painter.drawPixmap(rect.left() + self._PAD, icon_y, pixmap)

        painter.setFont(name_font)
        painter.setPen(to_qcolor(tokens.fg_faint if dim else tokens.fg))
        painter.drawText(
            name_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrics.elidedText(row.title, Qt.TextElideMode.ElideRight, name_rect.width()),
        )
        if row.hint:
            hint_font = QFont(self.font())
            hint_font.setPointSizeF(max(7.0, hint_font.pointSizeF() - 1.0))
            painter.setFont(hint_font)
            painter.setPen(to_qcolor(tokens.fg_faint))
            painter.drawText(
                hint_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                QFontMetrics(hint_font).elidedText(row.hint, Qt.TextElideMode.ElideRight, hint_rect.width()),
            )

        if row.is_direct:
            self._paint_switch(painter, control, row, tokens)
        else:
            self._paint_profile(painter, control, row, tokens)

    def _paint_switch(self, painter: QPainter, control: QRect, row: HostsListRow, tokens) -> None:
        track = QRectF(control.right() - 40, control.center().y() - 10, 40, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        if row.is_on:
            painter.setBrush(to_qcolor(tokens.accent_hex if row.enabled else tokens.surface_bg_disabled))
            painter.drawRoundedRect(track, 10, 10)
            knob_x = track.right() - 16
            painter.setBrush(to_qcolor(tokens.accent_fg if row.enabled else tokens.fg_faint))
        else:
            painter.setBrush(to_qcolor(tokens.toggle_off_bg))
            painter.setPen(to_qcolor(tokens.toggle_off_border))
            painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
            painter.setPen(Qt.PenStyle.NoPen)
            knob_x = track.left() + 4
            painter.setBrush(to_qcolor(tokens.fg_muted if row.enabled else tokens.fg_faint))
        painter.drawEllipse(QRectF(knob_x, track.top() + 4, 12, 12))

    def _paint_profile(self, painter: QPainter, control: QRect, row: HostsListRow, tokens) -> None:
        pill = QRectF(control.adjusted(0, 8, 0, -8))
        painter.setPen(Qt.PenStyle.NoPen)
        if row.is_on and row.enabled:
            painter.setBrush(to_qcolor(tokens.accent_soft_bg_hover))
        else:
            painter.setBrush(to_qcolor(tokens.surface_bg))
        painter.drawRoundedRect(pill, 6, 6)
        painter.setFont(self.font())
        color = tokens.accent_hex if (row.is_on and row.enabled) else tokens.fg_muted
        if not row.enabled:
            color = tokens.fg_faint
        painter.setPen(to_qcolor(color))
        text_rect = pill.toRect().adjusted(12, 0, -26, 0)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(self.font()).elidedText(row.value_text, Qt.TextElideMode.ElideRight, text_rect.width()),
        )
        if row.enabled:
            arrow = get_cached_qta_pixmap("fa5s.chevron-down", color=tokens.icon_fg_muted, size=10)
            painter.drawPixmap(int(pill.right()) - 20, int(pill.center().y()) - 5, arrow)

    # ── мышь и клавиатура ────────────────────────────────────

    def _activate(self, index: int) -> None:
        if not self._is_clickable(index):
            return
        self._set_cursor(index)
        control = self._control_rect(index)
        point = self.mapToGlobal(QPoint(control.left(), control.bottom()))
        self.activated.emit(self._rows[index].service_name, point)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._activate(self.row_at(event.position().toPoint().y()))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self.row_at(event.position().toPoint().y())
        self._set_hover(index if self._is_clickable(index) else -1)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._hover >= 0 else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._set_hover(-1)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        if not self._is_clickable(self._cursor):
            clickable = self._clickable_rows()
            self._set_cursor(clickable[0] if clickable else -1)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self.update(self._row_rect(self._cursor))
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        clickable = self._clickable_rows()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._activate(self._cursor)
            event.accept()
            return
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and clickable:
            if self._cursor in clickable:
                position = clickable.index(self._cursor) + (1 if key == Qt.Key.Key_Down else -1)
                position = min(max(position, 0), len(clickable) - 1)
            else:
                position = 0
            self._set_cursor(clickable[position])
            event.accept()
            return
        if key == Qt.Key.Key_Home and clickable:
            self._set_cursor(clickable[0])
            event.accept()
            return
        if key == Qt.Key.Key_End and clickable:
            self._set_cursor(clickable[-1])
            event.accept()
            return
        super().keyPressEvent(event)

    def _set_hover(self, index: int) -> None:
        if index == self._hover:
            return
        old, self._hover = self._hover, index
        self.update(self._row_rect(old))
        self.update(self._row_rect(index))

    def _set_cursor(self, index: int) -> None:
        if index == self._cursor:
            return
        old, self._cursor = self._cursor, index
        self.update(self._row_rect(old))
        self.update(self._row_rect(index))
        if 0 <= index < len(self._rows):
            row = self._rows[index]
            set_control_accessibility(
                self,
                name="Список сервисов hosts",
                description=row.accessible_text or row.title,
            )
            self.ensure_visible_requested(index)

    def ensure_visible_requested(self, index: int) -> None:
        """Прокручивает страницу к строке под клавиатурным курсором."""
        rect = self._row_rect(index)
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "ensureVisible"):
            parent = parent.parentWidget()
        if parent is None:
            return
        center = self.mapTo(parent.widget(), rect.center()) if hasattr(parent, "widget") and parent.widget() else None
        if center is not None:
            parent.ensureVisible(center.x(), center.y(), 0, rect.height())


__all__ = ["HostsListRow", "HostsServicesList"]
