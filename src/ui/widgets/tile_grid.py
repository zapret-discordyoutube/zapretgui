"""Плитки: мягкая карточка-кнопка и сетка, которая раскладывает их в ряд.

``SoftTile`` выглядит как карточка qfluentwidgets (тот же фон, край и
скругление), но ведёт себя как кнопка: под мышью и при фокусе с клавиатуры
плавно подкрашивается акцентом, справа проявляется стрелка «›», при
нажатии слегка темнеет. Рамок фокуса нет: интерфейс frameless.

``TileGrid`` ставит плитки в ряд на всю ширину. Если в один ряд плитки
стали бы у́же допустимого, сетка переносит их на следующие ряды. Высота
сетки всегда ровно по содержимому.
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QEvent, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from qfluentwidgets import isDarkTheme

from ui.animation_policy import are_live_animations_enabled


TILE_RADIUS = 6.0
TILE_HOVER_MS = 150
# Сколько акцента подмешивается в фон под мышью и при нажатии.
TILE_HOVER_ALPHA = 0.13
TILE_PRESS_ALPHA = 0.08
CHEVRON_ROOM = 22


class SoftTile(QWidget):
    clicked = pyqtSignal()

    def __init__(self, parent=None, *, clickable: bool = True, chevron: bool = True):
        super().__init__(parent)
        self._clickable = bool(clickable)
        self._chevron = bool(chevron) and self._clickable
        self._hovered = False
        self._pressed = False
        self._hover_t = 0.0
        # QVariantAnimation, а не QPropertyAnimation: при выключенных
        # анимациях WinUI общий fallback подменяет QPropertyAnimation.start.
        self._hover_fade = QVariantAnimation(self)
        self._hover_fade.setDuration(TILE_HOVER_MS)
        self._hover_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_fade.valueChanged.connect(self._on_hover_value)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, self._clickable)
        if self._clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def is_clickable(self) -> bool:
        return self._clickable

    def click(self) -> None:
        if self._clickable and self.isEnabled():
            self.clicked.emit()

    def hover_progress(self) -> float:
        return self._hover_t

    def accent_color(self) -> QColor:
        from ui.theme import get_theme_tokens

        return QColor(str(getattr(get_theme_tokens(), "accent_hex", "") or "#5caee8"))

    # ---- наведение и нажатие -------------------------------------------

    def _hover_target(self) -> float:
        return 1.0 if self._clickable and self.isEnabled() and (self._hovered or self.hasFocus()) else 0.0

    def _animate_hover(self) -> None:
        target = self._hover_target()
        if abs(target - self._hover_t) < 0.001:
            return
        self._hover_fade.stop()
        if self.isVisible() and are_live_animations_enabled():
            self._hover_fade.setStartValue(self._hover_t)
            self._hover_fade.setEndValue(target)
            self._hover_fade.start()
        else:
            self._hover_t = target
            self.update()

    def _on_hover_value(self, value) -> None:
        try:
            self._hover_t = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self._hovered = True
        self._animate_hover()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self._hovered = False
        self._pressed = False
        self._animate_hover()

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self._animate_hover()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self._animate_hover()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._pressed = False
            self._animate_hover()
            self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._hover_fade.stop()
        self._hovered = False
        self._pressed = False
        self._hover_t = 0.0
        super().hideEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._pressed and event.button() == Qt.MouseButton.LeftButton:
            self._pressed = False
            self.update()
            event.accept()
            if self.rect().contains(event.position().toPoint()):
                self.click()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._clickable and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    # ---- отрисовка -----------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_tile_background(painter)
        painter.end()

    def paint_tile_background(self, painter: QPainter) -> None:
        dark = isDarkTheme()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # Те же фон и край, что у CardWidget, чтобы плитки не выбивались из страницы.
        painter.setPen(QPen(QColor(0, 0, 0, 48) if dark else QColor(0, 0, 0, 20), 1.0))
        painter.setBrush(QColor(255, 255, 255, 13) if dark else QColor(255, 255, 255, 170))
        painter.drawRoundedRect(rect, TILE_RADIUS, TILE_RADIUS)

        strength = self._hover_t
        if strength > 0.0:
            fill = self.accent_color()
            alpha = TILE_PRESS_ALPHA if self._pressed else TILE_HOVER_ALPHA
            fill.setAlphaF(alpha * strength)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, TILE_RADIUS, TILE_RADIUS)

        if self._chevron and strength > 0.0:
            # Стрелка выезжает слева направо и проявляется.
            pen = QPen(self.accent_color())
            pen.setWidthF(1.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            color = pen.color()
            color.setAlphaF(strength)
            pen.setColor(color)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            x = rect.right() - 15.0 + 4.0 * (strength - 1.0)
            y = rect.center().y()
            painter.drawPolyline([QPointF(x - 3.5, y - 5.0), QPointF(x + 1.5, y), QPointF(x - 3.5, y + 5.0)])
        painter.setPen(Qt.PenStyle.NoPen)


class TileGrid(QWidget):
    """Раскладывает дочерние плитки в ряды на всю ширину."""

    def __init__(self, parent=None, *, min_tile_width: int = 200, spacing: int = 12, row_height: int = 0):
        super().__init__(parent)
        self._tiles: list[tuple[QWidget, float]] = []
        self._min_tile_width = max(40, int(min_tile_width))
        self._spacing = max(0, int(spacing))
        self._row_height = max(0, int(row_height))
        self._columns = 0
        self._in_relayout = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def add_tile(self, tile: QWidget, *, weight: float = 1.0) -> None:
        """weight — во сколько раз плитка шире соседей, пока все стоят в один ряд."""
        tile.setParent(self)
        self._tiles.append((tile, max(0.1, float(weight))))
        tile.installEventFilter(self)
        self.relayout()

    def tiles(self) -> list[QWidget]:
        return [tile for tile, _weight in self._tiles]

    def columns(self) -> int:
        return self._columns

    def _visible_tiles(self) -> list[tuple[QWidget, float]]:
        return [(tile, weight) for tile, weight in self._tiles if not tile.isHidden()]

    def _rows(self, tiles: list[tuple[QWidget, float]], width: int) -> list[list[tuple[QWidget, int]]]:
        """Самое большое число столбцов, при котором ни одна плитка не у́же допустимого."""
        for columns in range(len(tiles), 0, -1):
            # Одинокая плитка в последнем ряду выглядит оборванной: такие раскладки пропускаем.
            if columns > 1 and len(tiles) % columns == 1:
                continue
            room = width - self._spacing * (columns - 1)
            rows: list[list[tuple[QWidget, int]]] = []
            for start in range(0, len(tiles), columns):
                chunk = tiles[start : start + columns]
                if columns == len(tiles):
                    # Всё в один ряд: плитки делят ширину по своим долям.
                    total = sum(weight for _tile, weight in chunk)
                    rows.append([(tile, int(room * weight / total)) for tile, weight in chunk])
                else:
                    # В несколько рядов плитки равной ширины, иначе края рядов не совпадут.
                    rows.append([(tile, room // columns) for tile, _weight in chunk])
            if columns == 1 or all(
                tile_width >= max(self._min_tile_width, tile.minimumWidth())
                for row in rows
                for tile, tile_width in row
            ):
                self._columns = columns
                return rows
        self._columns = 0
        return []

    def relayout(self) -> None:
        if self._in_relayout:
            return
        self._in_relayout = True
        try:
            tiles = self._visible_tiles()
            width = max(0, self.width())
            y = 0
            for row in self._rows(tiles, width) if width > 0 else []:
                height = self._row_height or max(
                    max(tile.sizeHint().height(), tile.minimumSizeHint().height(), tile.minimumHeight())
                    for tile, _tile_width in row
                )
                x = 0
                for tile, tile_width in row:
                    tile.setGeometry(QRect(x, y, tile_width, height))
                    x += tile_width + self._spacing
                y += height + self._spacing
            total = max(0, y - self._spacing)
            if total != self.minimumHeight() or total != self.maximumHeight():
                self.setFixedHeight(total)
        finally:
            self._in_relayout = False

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(max(self._min_tile_width, self.width()), self.maximumHeight())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.relayout()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        # Плитку скрыли или показали (например, «Профили» у circular-пресета) —
        # соседи занимают освободившееся место.
        if event.type() in (QEvent.Type.ShowToParent, QEvent.Type.HideToParent):
            self.relayout()
        return super().eventFilter(watched, event)

    def event(self, event) -> bool:  # noqa: A003
        if event.type() == QEvent.Type.LayoutRequest:
            self.relayout()
        return super().event(event)


__all__ = ["CHEVRON_ROOM", "SoftTile", "TileGrid"]
