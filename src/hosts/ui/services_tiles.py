"""Плитки сервисов страницы Hosts: одна рисуемая сетка вместо сотен виджетов.

Плитка выглядит как карточка qfluentwidgets (CardWidget): те же фон, рамка,
скругление и шрифты. Сверху значок и название, под ним пояснение. Справа у
сервисов «напрямую» — тумблер, как SwitchButton; у сервисов с DNS-профилем
второй строкой — поле выбора профиля, как ComboBox. Включённая плитка мягко
подкрашена акцентом, без рамок. Щелчок по плитке сразу меняет выбор
(страница записывает hosts), у DNS-сервисов открывается меню профиля прямо
под полем.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PyQt6.QtWidgets import QSizePolicy, QWidget
from qfluentwidgets import FluentIcon, getFont, isDarkTheme, themeColor

from ui.accessibility import set_control_accessibility
from ui.animation_policy import are_live_animations_enabled
from ui.theme import get_cached_qta_pixmap, get_theme_tokens, to_qcolor


@dataclass(frozen=True, slots=True)
class HostsTile:
    """Одна плитка или заголовок группы. kind: "group" | "tile" | "empty"."""

    kind: str
    title: str
    key: str = ""
    # Пояснение второй строкой (из скобок в названии или подсказка).
    note: str = ""
    # У заголовка группы: «3 из 12».
    counter: str = ""
    icon_name: str = ""
    icon_color: str | None = None
    is_on: bool = False
    # Плитка с тумблером (сервисы «напрямую», Adobe).
    has_switch: bool = False
    # Плитка с полем профиля: текст поля («XBOX DNS», «Выкл.»).
    combo_text: str = ""
    pending: bool = False
    enabled: bool = True
    accessible_text: str = ""

    @property
    def has_combo(self) -> bool:
        return bool(self.combo_text)


class HostsTilesGrid(QWidget):
    """Сетка плиток. Данные задаёт страница через set_tiles()."""

    # (ключ плитки, точка на экране под полем профиля — для меню)
    activated = pyqtSignal(str, QPoint)

    TILE_MIN_WIDTH = 250
    TILE_HEIGHT = 78
    GAP = 8
    GROUP_HEIGHT = 40
    # Место справа под полосу прокрутки, чтобы она не лежала на плитках.
    SCROLLBAR_GUTTER = 14
    FLASH_MS = 520
    _RADIUS = 5.0
    _ICON = 20
    _PAD = 14
    _SWITCH = QSize(40, 20)
    _COMBO_HEIGHT = 28

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tiles: list[HostsTile] = []
        self._rects: list[QRect] = []
        self._columns = 1
        self._hover = -1
        self._cursor = -1
        self._pressed = -1
        self._flash: dict[str, float] = {}
        self._flash_anim = QVariantAnimation(self)
        self._flash_anim.setStartValue(1.0)
        self._flash_anim.setEndValue(0.0)
        self._flash_anim.setDuration(self.FLASH_MS)
        self._flash_anim.valueChanged.connect(self._on_flash_value)
        self._flash_anim.finished.connect(self._on_flash_finished)
        self.setObjectName("hostsTilesGrid")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        set_control_accessibility(self, name="Сервисы hosts")

    # ── данные ───────────────────────────────────────────────

    def tiles(self) -> list[HostsTile]:
        return list(self._tiles)

    def set_tiles(self, tiles: list[HostsTile]) -> None:
        cursor_key = self._key_at(self._cursor)
        self._tiles = list(tiles)
        self._hover = -1
        self._pressed = -1
        self._relayout()
        self._cursor = self._index_of(cursor_key)
        self.update()

    def flash(self, key: str) -> None:
        """Плитка коротко подсвечивается акцентом — выбор принят."""
        if not key or not are_live_animations_enabled():
            return
        self._flash[key] = 1.0
        self._flash_anim.stop()
        self._flash_anim.start()

    def tile_rect(self, key: str) -> QRect:
        index = self._index_of(key)
        return QRect(self._rects[index]) if index >= 0 else QRect()

    # ── раскладка ────────────────────────────────────────────

    def _relayout(self) -> None:
        width = max(self.width() - self.SCROLLBAR_GUTTER, self.TILE_MIN_WIDTH)
        columns = max(1, (width + self.GAP) // (self.TILE_MIN_WIDTH + self.GAP))
        tile_width = (width - self.GAP * (columns - 1)) // columns
        self._columns = columns
        self._rects = []
        y = 0
        column = 0
        for tile in self._tiles:
            if tile.kind == "tile":
                if column >= columns:
                    column = 0
                    y += self.TILE_HEIGHT + self.GAP
                x = column * (tile_width + self.GAP)
                self._rects.append(QRect(x, y, tile_width, self.TILE_HEIGHT))
                column += 1
                continue
            if column:
                y += self.TILE_HEIGHT + self.GAP
                column = 0
            if tile.kind == "group" and y:
                y += 12
            self._rects.append(QRect(0, y, width, self.GROUP_HEIGHT))
            y += self.GROUP_HEIGHT
        if column:
            y += self.TILE_HEIGHT
        height = max(y, self.GROUP_HEIGHT)
        if self.height() != height:
            self.setFixedHeight(height)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self._relayout()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.TILE_MIN_WIDTH * 3, self.height())

    def _key_at(self, index: int) -> str:
        if 0 <= index < len(self._tiles) and self._tiles[index].kind == "tile":
            return self._tiles[index].key
        return ""

    def _index_of(self, key: str) -> int:
        if not key:
            return -1
        for index, tile in enumerate(self._tiles):
            if tile.kind == "tile" and tile.key == key:
                return index
        return -1

    def index_at(self, point: QPoint) -> int:
        for index, rect in enumerate(self._rects):
            if self._tiles[index].kind == "tile" and rect.contains(point):
                return index
        return -1

    def _is_clickable(self, index: int) -> bool:
        return 0 <= index < len(self._tiles) and self._tiles[index].kind == "tile" and self._tiles[index].enabled

    def _clickable(self) -> list[int]:
        return [index for index in range(len(self._tiles)) if self._is_clickable(index)]

    def _switch_rect(self, rect: QRect) -> QRect:
        size = self._SWITCH
        return QRect(rect.right() - self._PAD - size.width(), rect.top() + self._PAD, size.width(), size.height())

    def _combo_rect(self, rect: QRect) -> QRect:
        left = rect.left() + self._PAD
        width = min(rect.width() - 2 * self._PAD, 190)
        return QRect(left, rect.bottom() - self._PAD - self._COMBO_HEIGHT + 4, width, self._COMBO_HEIGHT)

    # ── отрисовка ────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        tokens = get_theme_tokens()
        dark = isDarkTheme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dirty = event.rect()
        for index, rect in enumerate(self._rects):
            if not rect.intersects(dirty):
                continue
            tile = self._tiles[index]
            if tile.kind == "tile":
                self._paint_tile(painter, index, rect, tile, tokens, dark)
            else:
                self._paint_header(painter, rect, tile, tokens)
        painter.end()

    def _paint_header(self, painter: QPainter, rect: QRect, tile: HostsTile, tokens) -> None:
        # Как StrongBodyLabel: 14 px, полужирный; счётчик — приглушённый.
        title_font = getFont(14, QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(to_qcolor(tokens.fg))
        area = rect.adjusted(2, 0, 0, -8)
        align = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        painter.drawText(area, align, tile.title)
        if tile.counter:
            offset = QFontMetrics(title_font).horizontalAdvance(tile.title) + 10
            painter.setFont(getFont(12))
            painter.setPen(to_qcolor(tokens.fg_faint))
            painter.drawText(area.adjusted(offset, 0, 0, -1), align, tile.counter)

    def _card_colors(self, index: int, tile: HostsTile, dark: bool) -> tuple[QColor, QColor, QColor | None]:
        """Фон и рамка — как у CardWidget; включённая подкрашена акцентом."""
        hovered = index == self._hover or (index == self._cursor and self.hasFocus())
        pressed = index == self._pressed
        if dark:
            fill = QColor(255, 255, 255, 8 if pressed else (21 if hovered else 13))
            border = QColor(255, 255, 255, 18 if pressed else (13 if hovered else 0))
            border = border if border.alpha() else QColor(0, 0, 0, 48)
        else:
            fill = QColor(255, 255, 255, 118 if pressed else (230 if hovered else 170))
            border = QColor(0, 0, 0, 27 if hovered else 15)
        tint_alpha = 0
        if tile.is_on:
            tint_alpha = 16 if dark else 20
        flash = self._flash.get(tile.key, 0.0)
        if flash > 0:
            tint_alpha += int(60 * flash)
        tint = None
        if tint_alpha:
            tint = QColor(themeColor())
            tint.setAlpha(min(255, tint_alpha))
        return fill, border, tint

    def _paint_tile(self, painter: QPainter, index: int, rect: QRect, tile: HostsTile, tokens, dark: bool) -> None:
        painter.save()
        if not tile.enabled:
            painter.setOpacity(0.5)

        fill, border, tint = self._card_colors(index, tile, dark)
        box = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(box, self._RADIUS, self._RADIUS)
        painter.fillPath(path, fill)
        if tint is not None:
            painter.fillPath(path, tint)
        painter.setPen(border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        pad = self._PAD
        icon_color = (tile.icon_color or tokens.icon_fg) if tile.is_on else tokens.icon_fg_muted
        icon = get_cached_qta_pixmap(tile.icon_name or "fa5s.globe", color=icon_color, size=self._ICON)
        painter.drawPixmap(rect.left() + pad, rect.top() + pad, icon)

        text_left = rect.left() + pad + self._ICON + 10
        right_edge = rect.right() - pad
        if tile.has_switch:
            right_edge = self._switch_rect(rect).left() - 10

        # Название: как BodyLabel, 14 px; лишнее — многоточием (полное — в подсказке).
        title_font = getFont(14, QFont.Weight.DemiBold if tile.is_on else QFont.Weight.Normal)
        painter.setFont(title_font)
        painter.setPen(to_qcolor(tokens.fg))
        title_rect = QRect(text_left, rect.top() + pad - 2, right_edge - text_left, 22)
        painter.drawText(
            title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(title_font).elidedText(tile.title, Qt.TextElideMode.ElideRight, title_rect.width()),
        )

        if tile.has_switch:
            self._paint_switch(painter, self._switch_rect(rect), tile, dark)

        # Вторая строка: поле профиля или пояснение (как CaptionLabel, 12 px).
        if tile.has_combo:
            self._paint_combo(painter, self._combo_rect(rect), tile, tokens, dark)
            if tile.pending:
                combo = self._combo_rect(rect)
                self._paint_caption(painter, QRect(combo.right() + 10, combo.top(), rect.right() - pad - combo.right() - 10, combo.height()), self._pending_text(), accent=True)
        else:
            caption_rect = QRect(text_left, rect.top() + pad + 22, rect.right() - pad - text_left, 20)
            if tile.pending:
                self._paint_caption(painter, caption_rect, self._pending_text(), accent=True)
            elif tile.note:
                self._paint_caption(painter, caption_rect, tile.note)
        painter.restore()

    @staticmethod
    def _pending_text() -> str:
        return "записываю…"

    def _paint_caption(self, painter: QPainter, area: QRect, text: str, *, accent: bool = False) -> None:
        if area.width() <= 8:
            return
        tokens = get_theme_tokens()
        font = getFont(12)
        painter.setFont(font)
        painter.setPen(QColor(themeColor()) if accent else to_qcolor(tokens.fg_muted))
        painter.drawText(
            area,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, area.width()),
        )

    def _paint_switch(self, painter: QPainter, area: QRect, tile: HostsTile, dark: bool) -> None:
        """Тумблер в цветах SwitchButton."""
        box = QRectF(area).adjusted(1, 1, -1, -1)
        radius = box.height() / 2
        if tile.is_on:
            back = QColor(themeColor())
            painter.setPen(back)
            painter.setBrush(back)
            knob = QColor(0, 0, 0) if dark else QColor(255, 255, 255)
            knob_x = box.right() - 4 - 12
        else:
            painter.setPen(QColor(255, 255, 255, 153) if dark else QColor(0, 0, 0, 133))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            knob = QColor(255, 255, 255, 201) if dark else QColor(0, 0, 0, 156)
            knob_x = box.left() + 4
        painter.drawRoundedRect(box, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob)
        painter.drawEllipse(QRectF(knob_x, box.center().y() - 6, 12, 12))

    def _paint_combo(self, painter: QPainter, area: QRect, tile: HostsTile, tokens, dark: bool) -> None:
        """Поле профиля в цветах ComboBox: подложка, рамка, стрелка вниз."""
        box = QRectF(area).adjusted(0.5, 0.5, -0.5, -0.5)
        if dark:
            back = QColor(255, 255, 255, 15)
            edge = QColor(255, 255, 255, 14)
        else:
            back = QColor(255, 255, 255, 179)
            edge = QColor(0, 0, 0, 19)
        painter.setPen(edge)
        painter.setBrush(back)
        painter.drawRoundedRect(box, 5, 5)

        font = getFont(13)
        painter.setFont(font)
        painter.setPen(QColor(themeColor()) if tile.is_on else to_qcolor(tokens.fg_muted))
        text_area = QRect(area.left() + 10, area.top(), area.width() - 40, area.height())
        painter.drawText(
            text_area,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(font).elidedText(tile.combo_text, Qt.TextElideMode.ElideRight, text_area.width()),
        )
        arrow = QRectF(area.right() - 22, area.center().y() - 4, 9, 9)
        if dark:
            FluentIcon.ARROW_DOWN.render(painter, arrow)
        else:
            FluentIcon.ARROW_DOWN.render(painter, arrow, fill="#646464")

    def _on_flash_value(self, value) -> None:
        for key in list(self._flash):
            self._flash[key] = float(value)
            index = self._index_of(key)
            if index >= 0:
                self.update(self._rects[index])

    def _on_flash_finished(self) -> None:
        keys, self._flash = list(self._flash), {}
        for key in keys:
            index = self._index_of(key)
            if index >= 0:
                self.update(self._rects[index])

    # ── мышь и клавиатура ────────────────────────────────────

    def _activate(self, index: int) -> None:
        if not self._is_clickable(index):
            return
        self._set_cursor(index)
        tile, rect = self._tiles[index], self._rects[index]
        anchor = self._combo_rect(rect).bottomLeft() if tile.has_combo else rect.bottomLeft()
        self.activated.emit(tile.key, self.mapToGlobal(anchor + QPoint(0, 4)))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.index_at(event.position().toPoint())
            if self._is_clickable(index):
                self._pressed = index
                self.update(self._rects[index])
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pressed, self._pressed = self._pressed, -1
            if 0 <= pressed < len(self._rects):
                self.update(self._rects[pressed])
            index = self.index_at(event.position().toPoint())
            if index == pressed:
                self._activate(index)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self.index_at(event.position().toPoint())
        self._set_hover(index if self._is_clickable(index) else -1)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._hover >= 0 else Qt.CursorShape.ArrowCursor)
        if index >= 0:
            tile = self._tiles[index]
            self.setToolTip(f"{tile.title}\n{tile.note}" if tile.note else tile.title)
        else:
            self.setToolTip("")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._set_hover(-1)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        if not self._is_clickable(self._cursor):
            clickable = self._clickable()
            self._set_cursor(clickable[0] if clickable else -1)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        if 0 <= self._cursor < len(self._rects):
            self.update(self._rects[self._cursor])
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        clickable = self._clickable()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._activate(self._cursor)
            event.accept()
            return
        steps = {
            Qt.Key.Key_Right: 1,
            Qt.Key.Key_Left: -1,
            Qt.Key.Key_Down: self._columns,
            Qt.Key.Key_Up: -self._columns,
        }
        if key in steps and clickable:
            position = clickable.index(self._cursor) if self._cursor in clickable else 0
            position = min(max(position + steps[key], 0), len(clickable) - 1)
            self._set_cursor(clickable[position])
            event.accept()
            return
        if key in (Qt.Key.Key_Home, Qt.Key.Key_End) and clickable:
            self._set_cursor(clickable[0] if key == Qt.Key.Key_Home else clickable[-1])
            event.accept()
            return
        super().keyPressEvent(event)

    def _set_hover(self, index: int) -> None:
        if index == self._hover:
            return
        old, self._hover = self._hover, index
        for value in (old, index):
            if 0 <= value < len(self._rects):
                self.update(self._rects[value])

    def _set_cursor(self, index: int) -> None:
        if index == self._cursor:
            return
        old, self._cursor = self._cursor, index
        for value in (old, index):
            if 0 <= value < len(self._rects):
                self.update(self._rects[value])
        if 0 <= index < len(self._tiles):
            tile = self._tiles[index]
            set_control_accessibility(self, name="Сервисы hosts", description=tile.accessible_text or tile.title)
            self._ensure_visible(index)

    def _ensure_visible(self, index: int) -> None:
        """Прокручивает список к плитке под клавиатурным курсором."""
        rect = self._rects[index]
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "ensureVisible"):
            parent = parent.parentWidget()
        if parent is None or not hasattr(parent, "widget") or parent.widget() is None:
            return
        center = self.mapTo(parent.widget(), rect.center())
        parent.ensureVisible(center.x(), center.y(), 0, rect.height() // 2 + self.GAP)


__all__ = ["HostsTile", "HostsTilesGrid", "split_service_title"]


def split_service_title(name: str) -> tuple[str, str]:
    """«YouTube (иногда может не работать…)» → («YouTube», «иногда может не работать…»)."""
    text = str(name or "").strip()
    if text.endswith(")") and " (" in text:
        head, _, tail = text.partition(" (")
        if head.strip():
            return head.strip(), tail[:-1].strip()
    return text, ""
