"""Сетка плиток DNS-серверов: одна рисуемая поверхность вместо десятков виджетов.

Плитка выглядит как карточка qfluentwidgets (CardWidget): те же фон,
скругление и шрифты. Слева — значок сервера в круге его цвета, рядом
название и пояснение, внизу — основной адрес, метки IPv6/DoH и результат
замера скорости с цветной точкой. Выбранная плитка мягко подкрашена
акцентом и отмечена галочкой, без рамок. Последняя плитка — «Свой DNS».

Щелчок или Enter/пробел выбирает сервер, правая кнопка мыши (или клавиша
меню) на своём DNS открывает меню правки.
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


ADD_TILE_KEY = "__add__"

# Пороги цветной точки замера, мс.
LATENCY_FAST_MS = 50
LATENCY_OK_MS = 120


@dataclass(frozen=True, slots=True)
class GridTexts:
    """Надписи сетки; страница передаёт их на языке интерфейса."""

    measuring: str = "замер…"
    timeout: str = "нет ответа"
    ms: str = "{ms} мс"
    applying: str = "применяю…"
    selected: str = "выбран"
    not_selected: str = "не выбран"
    fastest: str = "быстрее всех"
    custom_hint: str = "свой DNS, меню правки — клавиша меню"
    add: str = "Свой DNS"
    grid_name: str = "DNS-серверы"
    grid_description: str = "Стрелки — выбор плитки, Enter или пробел — применить DNS."


@dataclass(frozen=True, slots=True)
class DnsTile:
    """Плитка сервера, плитка «Свой DNS» или заголовок группы.

    kind: "provider" | "add" | "group".
    latency: "" — не замеряли, "measuring" — идёт замер, "ok" — есть
    время в latency_ms, "timeout" — сервер не ответил.
    """

    kind: str
    key: str = ""
    title: str = ""
    note: str = ""
    address: str = ""
    icon_name: str = ""
    color: str = ""
    selected: bool = False
    pending: bool = False
    has_ipv6: bool = False
    has_doh: bool = False
    custom: bool = False
    latency: str = ""
    latency_ms: float = 0.0
    fastest: bool = False
    counter: str = ""
    tooltip: str = ""

    @property
    def clickable(self) -> bool:
        return self.kind in ("provider", "add")


def badge_color(color: str, tokens, dark: bool) -> QColor:
    """Цвет значка сервера; тёмные фирменные цвета в тёмной теме осветляются."""
    result = QColor(color) if color else to_qcolor(tokens.fg_muted)
    if dark and result.lightnessF() < 0.55:
        result = result.lighter(150)
    return result


def latency_text(tile: DnsTile, texts: GridTexts = GridTexts()) -> str:
    if tile.latency == "measuring":
        return texts.measuring
    if tile.latency == "timeout":
        return texts.timeout
    if tile.latency == "ok":
        return texts.ms.format(ms=max(1, round(tile.latency_ms)))
    return ""


def tile_accessible_text(tile: DnsTile, texts: GridTexts = GridTexts()) -> str:
    if tile.kind == "add":
        return ", ".join(part for part in (tile.title or texts.add, tile.note) if part)
    parts = [tile.title, texts.selected if tile.selected else texts.not_selected]
    if tile.pending:
        parts.append(texts.applying)
    if tile.note:
        parts.append(tile.note)
    if tile.address:
        parts.append(tile.address)
    speed = latency_text(tile, texts)
    if speed:
        parts.append(speed + (f", {texts.fastest}" if tile.fastest else ""))
    if tile.custom:
        parts.append(texts.custom_hint)
    return ", ".join(parts)


class DnsProviderGrid(QWidget):
    """Сетка плиток. Данные задаёт страница через set_tiles()."""

    activated = pyqtSignal(str)
    add_clicked = pyqtSignal()
    context_menu_wanted = pyqtSignal(str, QPoint)

    TILE_MIN_WIDTH = 250
    TILE_HEIGHT = 92
    GAP = 8
    GROUP_HEIGHT = 38
    SCROLLBAR_GUTTER = 14
    FLASH_MS = 560
    _RADIUS = 6.0
    _BADGE = 38
    _ICON = 18
    _PAD = 14

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tiles: list[DnsTile] = []
        self._rects: list[QRect] = []
        self._columns = 1
        self._hover = -1
        self._cursor = -1
        self._pressed = -1
        self._flash: dict[str, float] = {}
        self._texts = GridTexts()
        self._flash_anim = QVariantAnimation(self)
        self._flash_anim.setStartValue(1.0)
        self._flash_anim.setEndValue(0.0)
        self._flash_anim.setDuration(self.FLASH_MS)
        self._flash_anim.valueChanged.connect(self._on_flash_value)
        self._flash_anim.finished.connect(self._on_flash_finished)
        self.setObjectName("dnsProviderGrid")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        set_control_accessibility(self, name=self._texts.grid_name, description=self._texts.grid_description)

    # ── данные ───────────────────────────────────────────────

    def set_texts(self, texts: GridTexts) -> None:
        self._texts = texts
        self._sync_accessibility()
        self.update()

    def tiles(self) -> list[DnsTile]:
        return list(self._tiles)

    def tile(self, key: str) -> DnsTile | None:
        index = self._index_of(key)
        return self._tiles[index] if index >= 0 else None

    def set_tiles(self, tiles: list[DnsTile]) -> None:
        cursor_key = self._key_at(self._cursor)
        self._tiles = list(tiles)
        self._hover = -1
        self._pressed = -1
        self._relayout()
        self._cursor = self._index_of(cursor_key)
        self._sync_accessibility()
        self.update()

    def flash(self, key: str) -> None:
        """Плитка коротко вспыхивает акцентом — выбор принят."""
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
            if tile.kind != "group":
                if column >= columns:
                    column = 0
                    y += self.TILE_HEIGHT + self.GAP
                self._rects.append(QRect(column * (tile_width + self.GAP), y, tile_width, self.TILE_HEIGHT))
                column += 1
                continue
            if column:
                y += self.TILE_HEIGHT + self.GAP
                column = 0
            if y:
                y += 10
            self._rects.append(QRect(0, y, width, self.GROUP_HEIGHT))
            y += self.GROUP_HEIGHT
        if column:
            y += self.TILE_HEIGHT
        height = max(y, self.TILE_HEIGHT)
        if self.height() != height:
            self.setFixedHeight(height)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self._relayout()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.TILE_MIN_WIDTH * 3, self.height())

    def _key_at(self, index: int) -> str:
        if 0 <= index < len(self._tiles) and self._tiles[index].clickable:
            return self._tiles[index].key
        return ""

    def _index_of(self, key: str) -> int:
        if not key:
            return -1
        for index, tile in enumerate(self._tiles):
            if tile.clickable and tile.key == key:
                return index
        return -1

    def index_at(self, point: QPoint) -> int:
        for index, rect in enumerate(self._rects):
            if self._tiles[index].clickable and rect.contains(point):
                return index
        return -1

    def _clickable(self) -> list[int]:
        return [index for index, tile in enumerate(self._tiles) if tile.clickable]

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
            if tile.kind == "group":
                self._paint_group(painter, rect, tile, tokens)
            elif tile.kind == "add":
                self._paint_add_tile(painter, index, rect, tile, tokens, dark)
            else:
                self._paint_provider(painter, index, rect, tile, tokens, dark)
        painter.end()

    def _paint_group(self, painter: QPainter, rect: QRect, tile: DnsTile, tokens) -> None:
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

    def _card_path(self, rect: QRect) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), self._RADIUS, self._RADIUS)
        return path

    def _paint_card(self, painter: QPainter, index: int, rect: QRect, tile: DnsTile, dark: bool) -> None:
        """Фон и рамка — как у CardWidget; выбранная подкрашена акцентом."""
        hovered = index == self._hover or (index == self._cursor and self.hasFocus())
        pressed = index == self._pressed
        if dark:
            fill = QColor(255, 255, 255, 8 if pressed else (21 if hovered else 13))
            border = QColor(255, 255, 255, 18 if pressed else 13) if (hovered or pressed) else QColor(0, 0, 0, 48)
        else:
            fill = QColor(255, 255, 255, 118 if pressed else (230 if hovered else 170))
            border = QColor(0, 0, 0, 27 if hovered else 15)
        tint_alpha = (22 if dark else 26) if tile.selected else 0
        flash = self._flash.get(tile.key, 0.0)
        if flash > 0:
            tint_alpha += int(70 * flash)
        path = self._card_path(rect)
        painter.fillPath(path, fill)
        if tint_alpha:
            tint = QColor(themeColor())
            tint.setAlpha(min(255, tint_alpha))
            painter.fillPath(path, tint)
        painter.setPen(border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _paint_badge(self, painter: QPainter, center_x: int, center_y: int, tile: DnsTile, tokens, dark: bool) -> None:
        """Значок сервера в мягком круге его фирменного цвета."""
        color = badge_color(tile.color, tokens, dark)
        back = QColor(color)
        back.setAlpha(46 if dark else 34)
        half = self._BADGE / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(back)
        painter.drawEllipse(QRectF(center_x - half, center_y - half, self._BADGE, self._BADGE))
        icon = get_cached_qta_pixmap(tile.icon_name or "fa5s.server", color=color.name(), size=self._ICON)
        painter.drawPixmap(center_x - self._ICON // 2, center_y - self._ICON // 2, icon)

    def _paint_provider(self, painter: QPainter, index: int, rect: QRect, tile: DnsTile, tokens, dark: bool) -> None:
        painter.save()
        self._paint_card(painter, index, rect, tile, dark)
        pad = self._PAD
        self._paint_badge(painter, rect.left() + pad + self._BADGE // 2, rect.top() + pad + self._BADGE // 2, tile, tokens, dark)

        text_left = rect.left() + pad + self._BADGE + 12
        right = rect.right() - pad
        mark_width = 0
        if tile.selected or tile.pending:
            mark_width = 26
            self._paint_mark(painter, QRect(right - 18, rect.top() + pad, 18, 18), tile)

        title_font = getFont(14, QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(to_qcolor(tokens.fg))
        title_rect = QRect(text_left, rect.top() + pad - 3, right - text_left - mark_width, 22)
        painter.drawText(
            title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(title_font).elidedText(tile.title, Qt.TextElideMode.ElideRight, title_rect.width()),
        )
        note = self._texts.applying if tile.pending else tile.note
        self._paint_text(
            painter,
            QRect(text_left, rect.top() + pad + 19, right - text_left - mark_width, 18),
            note,
            size=12,
            color=QColor(themeColor()) if tile.pending else to_qcolor(tokens.fg_muted),
        )
        self._paint_footer(painter, QRect(rect.left() + pad, rect.bottom() - pad - 17, rect.width() - 2 * pad, 18), tile, tokens, dark)
        painter.restore()

    def _paint_mark(self, painter: QPainter, area: QRect, tile: DnsTile) -> None:
        """Галочка выбранного сервера; пока DNS применяется — пустое кольцо."""
        accent = QColor(themeColor())
        box = QRectF(area).adjusted(1, 1, -1, -1)
        if tile.pending:
            pen_color = QColor(accent)
            painter.setPen(pen_color)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(box)
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(box)
        check = box.adjusted(4, 4, -4, -4)
        FluentIcon.ACCEPT.render(painter, check, fill="#000000" if isDarkTheme() else "#ffffff")

    def _paint_footer(self, painter: QPainter, area: QRect, tile: DnsTile, tokens, dark: bool) -> None:
        """Нижняя строка: адрес, метки IPv6/DoH и скорость справа."""
        right = area.right()
        speed = latency_text(tile, self._texts)
        if speed:
            font = getFont(12, QFont.Weight.DemiBold if tile.fastest else QFont.Weight.Normal)
            metrics = QFontMetrics(font)
            text_width = metrics.horizontalAdvance(speed)
            painter.setFont(font)
            painter.setPen(QColor(themeColor()) if tile.fastest else to_qcolor(tokens.fg_muted))
            painter.drawText(
                QRect(right - text_width, area.top(), text_width, area.height()),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                speed,
            )
            dot_color = self._latency_color(tile, tokens, dark)
            if tile.fastest:
                # У самого быстрого вместо точки — молния акцентного цвета.
                bolt = get_cached_qta_pixmap("fa5s.bolt", color=QColor(themeColor()).name(), size=11)
                painter.drawPixmap(right - text_width - 13, area.center().y() - 6, bolt)
                right -= text_width + 22
            elif dot_color is not None:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(dot_color)
                painter.drawEllipse(QRectF(right - text_width - 12, area.center().y() - 3.5, 7, 7))
                right -= text_width + 20
            else:
                right -= text_width + 10

        x = area.left()
        address_font = QFont("Consolas")
        address_font.setPixelSize(12)
        address_font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(address_font)
        painter.setPen(to_qcolor(tokens.fg_muted))
        metrics = QFontMetrics(address_font)
        badges = [label for label, on in (("IPv6", tile.has_ipv6), ("DoH", tile.has_doh)) if on]
        badge_font = getFont(10, QFont.Weight.DemiBold)
        badge_metrics = QFontMetrics(badge_font)
        badges_width = sum(badge_metrics.horizontalAdvance(label) + 14 for label in badges)
        address_width = max(0, min(metrics.horizontalAdvance(tile.address), right - x - badges_width - 8))
        if tile.address and address_width > 12:
            painter.drawText(
                QRect(x, area.top(), address_width, area.height()),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                metrics.elidedText(tile.address, Qt.TextElideMode.ElideRight, address_width),
            )
            x += address_width + 8
        painter.setFont(badge_font)
        for label in badges:
            width = badge_metrics.horizontalAdvance(label) + 10
            if x + width > right:
                break
            box = QRectF(x, area.center().y() - 8, width, 16)
            back = QColor(255, 255, 255, 18) if dark else QColor(0, 0, 0, 12)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(back)
            painter.drawRoundedRect(box, 8, 8)
            painter.setPen(to_qcolor(tokens.fg_muted))
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), label)
            x += width + 4

    @staticmethod
    def _latency_color(tile: DnsTile, tokens, dark: bool) -> QColor | None:
        if tile.latency == "ok":
            if tile.latency_ms <= LATENCY_FAST_MS:
                return QColor("#3fb950" if dark else "#1a7f37")
            if tile.latency_ms <= LATENCY_OK_MS:
                return QColor("#d29922" if dark else "#9a6700")
            return QColor("#f85149" if dark else "#cf222e")
        if tile.latency == "timeout":
            return to_qcolor(tokens.fg_faint)
        return None

    def _paint_add_tile(self, painter: QPainter, index: int, rect: QRect, tile: DnsTile, tokens, dark: bool) -> None:
        """Плитка «Свой DNS»: без подкраски, плюс в круге и подпись по центру."""
        painter.save()
        self._paint_card(painter, index, rect, tile, dark)
        painter.setOpacity(0.9)
        badge_center_x = rect.left() + self._PAD + self._BADGE // 2
        badge_center_y = rect.center().y()
        half = self._BADGE / 2
        ring = QColor(themeColor())
        ring.setAlpha(40 if dark else 30)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ring)
        painter.drawEllipse(QRectF(badge_center_x - half, badge_center_y - half, self._BADGE, self._BADGE))
        plus = QRectF(badge_center_x - 8, badge_center_y - 8, 16, 16)
        FluentIcon.ADD.render(painter, plus, fill=QColor(themeColor()).name())
        text_left = rect.left() + self._PAD + self._BADGE + 12
        text_width = rect.right() - self._PAD - text_left
        self._paint_text(painter, QRect(text_left, badge_center_y - 20, text_width, 22), tile.title, size=14, weight=QFont.Weight.DemiBold, color=to_qcolor(tokens.fg))
        self._paint_text(painter, QRect(text_left, badge_center_y + 1, text_width, 18), tile.note, size=12, color=to_qcolor(tokens.fg_muted))
        painter.restore()

    @staticmethod
    def _paint_text(painter: QPainter, area: QRect, text: str, *, size: int, color: QColor, weight=QFont.Weight.Normal) -> None:
        if not text or area.width() <= 8:
            return
        font = getFont(size, weight)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(
            area,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, area.width()),
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
        if not (0 <= index < len(self._tiles)) or not self._tiles[index].clickable:
            return
        self._set_cursor(index)
        tile = self._tiles[index]
        if tile.kind == "add":
            self.add_clicked.emit()
            return
        self.activated.emit(tile.key)

    def _request_context_menu(self, index: int, global_pos: QPoint) -> bool:
        if not (0 <= index < len(self._tiles)) or not self._tiles[index].custom:
            return False
        self._set_cursor(index)
        self.context_menu_wanted.emit(self._tiles[index].key, global_pos)
        return True

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.index_at(event.position().toPoint())
            if index >= 0:
                self._pressed = index
                self.update(self._rects[index])
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        point = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton:
            pressed, self._pressed = self._pressed, -1
            if 0 <= pressed < len(self._rects):
                self.update(self._rects[pressed])
            index = self.index_at(point)
            if index == pressed:
                self._activate(index)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            if self._request_context_menu(self.index_at(point), self.mapToGlobal(point)):
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self.index_at(event.position().toPoint())
        self._set_hover(index)
        self.setCursor(Qt.CursorShape.PointingHandCursor if index >= 0 else Qt.CursorShape.ArrowCursor)
        self.setToolTip(self._tiles[index].tooltip if index >= 0 else "")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._set_hover(-1)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        if self._key_at(self._cursor) == "":
            selected = [index for index, tile in enumerate(self._tiles) if tile.selected]
            clickable = self._clickable()
            self._set_cursor(selected[0] if selected else (clickable[0] if clickable else -1))
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
        menu_key = key == Qt.Key.Key_Menu or (
            key == Qt.Key.Key_F10 and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        )
        if menu_key and 0 <= self._cursor < len(self._rects):
            anchor = self.mapToGlobal(self._rects[self._cursor].bottomLeft() + QPoint(12, -12))
            if self._request_context_menu(self._cursor, anchor):
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
        self._sync_accessibility()
        if 0 <= index < len(self._rects):
            self._ensure_visible(index)

    def _sync_accessibility(self) -> None:
        if 0 <= self._cursor < len(self._tiles):
            description = tile_accessible_text(self._tiles[self._cursor], self._texts)
        else:
            description = self._texts.grid_description
        set_control_accessibility(self, name=self._texts.grid_name, description=description)

    def _ensure_visible(self, index: int) -> None:
        """Прокручивает страницу к плитке под клавиатурным курсором."""
        rect = self._rects[index]
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "ensureVisible"):
            parent = parent.parentWidget()
        if parent is None or not hasattr(parent, "widget") or parent.widget() is None:
            return
        center = self.mapTo(parent.widget(), rect.center())
        parent.ensureVisible(center.x(), center.y(), 0, rect.height() // 2 + self.GAP)


__all__ = [
    "ADD_TILE_KEY",
    "DnsProviderGrid",
    "DnsTile",
    "GridTexts",
    "badge_color",
    "latency_text",
    "tile_accessible_text",
]
