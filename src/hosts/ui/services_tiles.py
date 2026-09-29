"""Плитки сервисов страницы Hosts: одна рисуемая сетка вместо сотен виджетов.

Каждый сервис — карточка: значок, название и строка состояния внизу. У
сервисов с DNS-профилем это ярлычок профиля («XBOX DNS ▾»). Включённая
карточка мягко залита акцентом. Щелчок по карточке сразу меняет выбор
(страница записывает hosts), у DNS-сервисов открывается меню профиля прямо
под карточкой. Подсветка наведения — только у самой карточки, без полос.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ui.accessibility import set_control_accessibility
from ui.animation_policy import are_live_animations_enabled
from ui.theme import get_cached_qta_pixmap, get_theme_tokens, to_qcolor


@dataclass(frozen=True, slots=True)
class HostsTile:
    """Одна карточка или заголовок группы. kind: "group" | "tile" | "empty"."""

    kind: str
    title: str
    key: str = ""
    icon_name: str = ""
    icon_color: str | None = None
    is_on: bool = False
    # Ярлычок профиля у DNS-сервисов («XBOX DNS», «выкл.»); пусто — у прочих.
    badge: str = ""
    # Подпись внизу у сервисов без профиля («включено», «нужен IPv6»).
    caption: str = ""
    has_menu: bool = False
    pending: bool = False
    enabled: bool = True
    accessible_text: str = ""


class HostsTilesGrid(QWidget):
    """Сетка карточек. Данные задаёт страница через set_tiles()."""

    # (ключ карточки, точка на экране под карточкой — для меню профиля)
    activated = pyqtSignal(str, QPoint)

    TILE_MIN_WIDTH = 220
    TILE_HEIGHT = 68
    GAP = 8
    GROUP_HEIGHT = 34
    # Место справа под полосу прокрутки, чтобы она не лежала на плитках.
    SCROLLBAR_GUTTER = 14
    FLASH_MS = 520
    _RADIUS = 8.0
    _ICON = 20
    _PAD = 12

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tiles: list[HostsTile] = []
        self._rects: list[QRect] = []
        self._columns = 1
        self._hover = -1
        self._cursor = -1
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
        self._relayout()
        self._cursor = self._index_of(cursor_key)
        self.update()

    def flash(self, key: str) -> None:
        """Карточка коротко вспыхивает акцентом — выбор принят."""
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
                y += 8
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

    # ── отрисовка ────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        tokens = get_theme_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dirty = event.rect()
        for index, rect in enumerate(self._rects):
            if not rect.intersects(dirty):
                continue
            tile = self._tiles[index]
            if tile.kind == "tile":
                self._paint_tile(painter, index, rect, tile, tokens)
            else:
                self._paint_header(painter, rect, tile, tokens)
        painter.end()

    def _paint_header(self, painter: QPainter, rect: QRect, tile: HostsTile, tokens) -> None:
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold if tile.kind == "group" else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(to_qcolor(tokens.fg_muted))
        painter.drawText(
            rect.adjusted(2, 0, 0, -6),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
            tile.title,
        )

    def _paint_tile(self, painter: QPainter, index: int, rect: QRect, tile: HostsTile, tokens) -> None:
        accent = QColor(tokens.accent_hex)
        light = tokens.is_light
        box = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.save()
        if not tile.enabled:
            painter.setOpacity(0.45)

        # Подложка: спокойная у выключенных, мягкий акцент у включённых.
        hovered = index == self._hover or (index == self._cursor and self.hasFocus())
        if tile.is_on:
            fill = QColor(accent)
            fill.setAlpha(46 if hovered else 32)
            border = QColor(accent)
            border.setAlpha(150)
        else:
            fill = QColor(0, 0, 0, 16 if hovered else 8) if light else QColor(255, 255, 255, 18 if hovered else 9)
            border = QColor(0, 0, 0, 22) if light else QColor(255, 255, 255, 20)
        flash = self._flash.get(tile.key, 0.0)
        if flash > 0:
            glow = QColor(accent)
            glow.setAlpha(int(fill.alpha() + 90 * flash))
            fill = glow
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, self._RADIUS, self._RADIUS)

        pad = self._PAD
        icon = get_cached_qta_pixmap(
            tile.icon_name or "fa5s.globe",
            color=(tile.icon_color or tokens.icon_fg) if tile.is_on else tokens.icon_fg_muted,
            size=self._ICON,
        )
        painter.drawPixmap(rect.left() + pad, rect.top() + pad, icon)

        # Название: одна строка, лишнее — многоточием (полное — в подсказке).
        title_font = QFont(self.font())
        title_font.setPixelSize(13)
        title_font.setWeight(QFont.Weight.DemiBold if tile.is_on else QFont.Weight.Normal)
        painter.setFont(title_font)
        painter.setPen(to_qcolor(tokens.fg))
        text_left = rect.left() + pad + self._ICON + 10
        title_rect = QRect(text_left, rect.top() + pad - 2, rect.right() - pad - text_left - 10, 22)
        painter.drawText(
            title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(title_font).elidedText(tile.title, Qt.TextElideMode.ElideRight, title_rect.width()),
        )

        # Точка «включено» в углу.
        if tile.is_on:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(QRectF(rect.right() - pad - 6, rect.top() + pad + 2, 7, 7))

        small = QFont(self.font())
        small.setPixelSize(11)
        painter.setFont(small)
        bottom = QRect(text_left, rect.bottom() - pad - 18, rect.right() - pad - text_left, 20)
        if tile.pending:
            painter.setPen(accent)
            painter.drawText(bottom, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), "записываю…")
        elif tile.badge:
            self._paint_badge(painter, bottom, tile, tokens, accent)
        elif tile.caption:
            painter.setPen(to_qcolor(tokens.fg_faint))
            painter.drawText(bottom, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), tile.caption)
        painter.restore()

    def _paint_badge(self, painter: QPainter, area: QRect, tile: HostsTile, tokens, accent: QColor) -> None:
        text = f"{tile.badge}  ▾" if tile.has_menu else tile.badge
        metrics = QFontMetrics(painter.font())
        width = min(area.width(), metrics.horizontalAdvance(text) + 16)
        pill = QRectF(area.left() - 2, area.top() + 1, width, area.height() - 2)
        if tile.is_on:
            bg = QColor(accent)
            bg.setAlpha(60)
            fg = QColor(tokens.fg)
        else:
            bg = QColor(0, 0, 0, 14) if tokens.is_light else QColor(255, 255, 255, 14)
            fg = to_qcolor(tokens.fg_muted)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        painter.setPen(fg)
        painter.drawText(
            pill.toRect(),
            int(Qt.AlignmentFlag.AlignCenter),
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(pill.width()) - 12),
        )

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
        rect = self._rects[index]
        self.activated.emit(self._tiles[index].key, self.mapToGlobal(rect.bottomLeft() + QPoint(0, 4)))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._activate(self.index_at(event.position().toPoint()))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self.index_at(event.position().toPoint())
        self._set_hover(index if self._is_clickable(index) else -1)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._hover >= 0 else Qt.CursorShape.ArrowCursor)
        self.setToolTip(self._tiles[index].title if index >= 0 else "")
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
        """Прокручивает список к карточке под клавиатурным курсором."""
        rect = self._rects[index]
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "ensureVisible"):
            parent = parent.parentWidget()
        if parent is None or not hasattr(parent, "widget") or parent.widget() is None:
            return
        center = self.mapTo(parent.widget(), rect.center())
        parent.ensureVisible(center.x(), center.y(), 0, rect.height() // 2 + self.GAP)


__all__ = ["HostsTile", "HostsTilesGrid"]
