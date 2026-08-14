"""Полоса маркеров совпадений справа от текста.

Показывает, где в документе находятся найденные совпадения целиком — включая
те, что сейчас за пределами экрана: по ней видно, сколько ещё листать.
Отрисовку и попадания считает сам виджет, редактор передаёт только номера
строк.
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

from ui.accessibility import set_control_accessibility, set_state_text

RULER_WIDTH = 14
MARKER_HEIGHT = 3
MARKER_MARGIN = 3


def marker_offset(line: int, total_lines: int, height: int) -> int:
    """Вертикальная координата метки строки внутри полосы высотой height."""
    usable = max(1, int(height) - MARKER_HEIGHT)
    total = max(1, int(total_lines) - 1)
    ratio = max(0.0, min(1.0, float(max(0, int(line))) / total))
    return int(round(ratio * usable))


def line_at_offset(offset: int, total_lines: int, height: int) -> int:
    """Обратное преобразование: строка под указанной точкой полосы."""
    usable = max(1, int(height) - MARKER_HEIGHT)
    total = max(1, int(total_lines) - 1)
    ratio = max(0.0, min(1.0, float(int(offset)) / usable))
    return int(round(ratio * total))


class MatchRuler(QWidget):
    """Мини-карта совпадений: метка на каждое, рамка на видимую область."""

    lineRequested = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("noDrag", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(RULER_WIDTH)
        # Полоса кликабельна, поэтому обязана работать и с клавиатуры:
        # стрелки водят по совпадениям, Home/End — к первому и последнему.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        ruler_name = "Карта совпадений"
        set_control_accessibility(
            self,
            name=ruler_name,
            description=(
                "Показывает, где расположены найденные совпадения во всём тексте. "
                "Стрелки вверх и вниз переходят к соседнему совпадению, "
                "Home и End — к первому и последнему."
            ),
        )
        set_state_text(self, ruler_name)
        self._lines: tuple[int, ...] = ()
        self._current_line: int | None = None
        self._total_lines = 1
        self._visible_range: tuple[int, int] = (0, 0)
        self._marker_color = QColor(0, 120, 212, 140)
        self._current_color = QColor(0, 120, 212, 255)
        self._viewport_color = QColor(128, 128, 128, 40)

    def set_colors(self, *, marker: QColor, current: QColor, viewport: QColor) -> None:
        self._marker_color = QColor(marker)
        self._current_color = QColor(current)
        self._viewport_color = QColor(viewport)
        self.update()

    def set_matches(self, lines, *, total_lines: int, current_line: int | None = None) -> None:
        self._lines = tuple(sorted({max(0, int(line)) for line in (lines or ())}))
        self._total_lines = max(1, int(total_lines))
        self._current_line = None if current_line is None else max(0, int(current_line))
        self.update()

    def set_visible_range(self, first_line: int, last_line: int) -> None:
        value = (max(0, int(first_line)), max(0, int(last_line)))
        if value == self._visible_range:
            return
        self._visible_range = value
        self.update()

    @property
    def match_lines(self) -> tuple[int, ...]:
        return self._lines

    def has_markers(self) -> bool:
        return bool(self._lines)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        height = self.height()
        width = self.width()
        marker_width = max(1, width - MARKER_MARGIN * 2)

        first, last = self._visible_range
        if last > first:
            top = marker_offset(first, self._total_lines, height)
            bottom = marker_offset(last, self._total_lines, height)
            painter.fillRect(
                QRect(0, top, width, max(MARKER_HEIGHT, bottom - top)),
                self._viewport_color,
            )

        for line in self._lines:
            is_current = self._current_line is not None and line == self._current_line
            offset = marker_offset(line, self._total_lines, height)
            painter.fillRect(
                QRect(MARKER_MARGIN, offset, marker_width, MARKER_HEIGHT),
                self._current_color if is_current else self._marker_color,
            )
        painter.end()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        line = line_at_offset(int(event.position().y()), self._total_lines, self.height())
        self.lineRequested.emit(line)
        event.accept()

    def step_to_neighbour_match(self, *, forward: bool) -> int | None:
        """Соседняя метка относительно текущей; None, если меток нет."""
        if not self._lines:
            return None
        if self._current_line is None:
            return self._lines[0] if forward else self._lines[-1]
        if forward:
            for line in self._lines:
                if line > self._current_line:
                    return line
            return self._lines[0]
        for line in reversed(self._lines):
            if line < self._current_line:
                return line
        return self._lines[-1]

    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        target: int | None = None
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            target = self.step_to_neighbour_match(forward=True)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            target = self.step_to_neighbour_match(forward=False)
        elif key == Qt.Key.Key_Home and self._lines:
            target = self._lines[0]
        elif key == Qt.Key.Key_End and self._lines:
            target = self._lines[-1]
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            target = self._current_line if self._current_line is not None else (
                self._lines[0] if self._lines else None
            )

        if target is None:
            super().keyPressEvent(event)
            return
        self.lineRequested.emit(int(target))
        event.accept()
