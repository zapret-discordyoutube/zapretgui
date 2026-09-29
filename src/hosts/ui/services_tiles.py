"""Плитки сервисов страницы Hosts: одна рисуемая сетка вместо сотен виджетов.

Плитка выглядит как карточка qfluentwidgets (CardWidget): те же фон, рамка,
скругление и шрифты. Сверху значок и название. У сервисов «напрямую» справа
тумблер в стиле переключателя qfluentwidgets, а под названием — пояснение. У сервисов с
DNS-профилем под названием ряд иконок провайдеров: щелчок по иконке сразу
выбирает профиль, щелчок по выбранной — выключает, полное имя — в подсказке.
Включённая плитка мягко подкрашена акцентом, без рамок.

Каждая плитка рисуется один раз в готовую картинку; прокрутка и перерисовка
только копируют картинки, пока плитка не изменилась.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QSizePolicy, QWidget
from qfluentwidgets import getFont, isDarkTheme, themeColor

from ui.accessibility import set_control_accessibility
from ui.animation_policy import are_live_animations_enabled
from ui.theme import get_cached_qta_pixmap, get_theme_tokens, to_qcolor
from ui.widgets.stagger_float_in import (
    FLOAT_IN_DURATION_MS,
    FLOAT_IN_RISE_PX,
    FLOAT_IN_STEP_MS,
    float_in_progress,
)


@dataclass(frozen=True, slots=True)
class HostsChoice:
    """Один DNS-профиль в ряду иконок плитки."""

    profile_id: str
    label: str
    icon_name: str
    color: str
    # Профиль есть у этого сервиса. Недоступный не рисуется, но место
    # остаётся, чтобы иконки стояли ровными столбцами по всей странице.
    available: bool = True


@dataclass(frozen=True, slots=True)
class _Change:
    """Идущая анимация смены: kind — "pick" | "drop" | "switch"."""

    kind: str
    profile_id: str
    started: float


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
    # Плитка с DNS-профилем: ряд иконок всех профилей каталога.
    choices: tuple[HostsChoice, ...] = ()
    selected: str | None = None
    # Справа от названия у DNS-плитки: имя выбранного профиля или «Выкл.».
    state_text: str = ""
    pending: bool = False
    enabled: bool = True
    accessible_text: str = ""
    # У заголовка группы DNS-сервисов: легенда «иконка — провайдер».
    legend: tuple[HostsChoice, ...] = ()

    @property
    def has_choices(self) -> bool:
        return bool(self.choices)


class HostsTilesGrid(QWidget):
    """Сетка плиток. Данные задаёт страница через set_tiles()."""

    # Тумблер: сервис «напрямую» или Adobe (ключ плитки).
    activated = pyqtSignal(str)
    # Иконка профиля: (ключ сервиса, id профиля или None — выключить).
    profile_chosen = pyqtSignal(str, object)

    TILE_MIN_WIDTH = 250
    TILE_HEIGHT = 78
    GAP = 8
    GROUP_HEIGHT = 40
    # Строка легенды под заголовком группы DNS-сервисов.
    LEGEND_HEIGHT = 24
    # Место справа под полосу прокрутки, чтобы она не лежала на плитках.
    SCROLLBAR_GUTTER = 14
    FLASH_MS = 520
    # Смена выбора: оборот иконки и расходящееся свечение.
    CHANGE_SECONDS = 0.65
    FRAME_MS = 16
    # Свой вход страницы: плитки выплывают по очереди чаще, чем карточки.
    ENTRANCE_STEP_MS = FLOAT_IN_STEP_MS // 2
    ENTRANCE_MAX_STEPS = 16
    _RADIUS = 5.0
    _ICON = 20
    _PAD = 14
    _SWITCH = QSize(40, 20)
    _CHOICE = 24
    _CHOICE_ICON = 13
    _CHOICE_GAP = 6
    _LEGEND_ICON = 13

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tiles: list[HostsTile] = []
        self._rects: list[QRect] = []
        self._columns = 1
        self._hover = -1
        self._hover_choice = -1
        self._cursor = -1
        self._pressed = -1
        self._pressed_choice = -1
        self._flash: dict[str, float] = {}
        self._flash_anim = QVariantAnimation(self)
        self._flash_anim.setStartValue(1.0)
        self._flash_anim.setEndValue(0.0)
        self._flash_anim.setDuration(self.FLASH_MS)
        self._flash_anim.valueChanged.connect(self._on_flash_value)
        self._flash_anim.finished.connect(self._on_flash_finished)
        # Готовые картинки плиток: ключ — всё, от чего зависит вид плитки.
        self._pixmaps: dict[tuple, QPixmap] = {}
        # Сколько плиток нарисовано заново (а не взято готовыми) — для тестов.
        self.rendered_tiles = 0
        # Анимации смены по ключу плитки; кадры идут, только пока они есть.
        self._changes: dict[str, _Change] = {}
        self._live_keys: set[str] = set()
        # Вход при показе страницы: начало (сек) и очередь видимых плиток.
        self._entrance_start: float | None = None
        self._entrance_order: dict[int, int] = {}
        self._now = time.monotonic
        self._frames = QTimer(self)
        self._frames.setInterval(self.FRAME_MS)
        self._frames.timeout.connect(self._on_frame)
        self._title_font = getFont(14)
        self._title_font_on = getFont(14, QFont.Weight.DemiBold)
        self._caption_font = getFont(12)
        self._header_font = getFont(14, QFont.Weight.DemiBold)
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
        tiles = list(tiles)
        if tiles == self._tiles:
            return
        # Перерисовываются только изменившиеся плитки.
        old_by_key = {tile.key: (tile, QRect(rect)) for tile, rect in zip(self._tiles, self._rects) if tile.key}
        old_layout = [(tile.kind, tile.key) for tile in self._tiles]
        self._start_changes(old_by_key, tiles)
        self._tiles = tiles
        self._hover = -1
        self._hover_choice = -1
        self._pressed = -1
        self._pressed_choice = -1
        self._relayout()
        self._cursor = self._index_of(cursor_key)
        in_use = {tile for tile in self._tiles}
        self._pixmaps = {key: pixmap for key, pixmap in self._pixmaps.items() if key[0] in in_use}
        self._sync_frames()
        if old_layout != [(tile.kind, tile.key) for tile in self._tiles]:
            self.update()
            return
        for tile, rect in zip(self._tiles, self._rects):
            previous = old_by_key.get(tile.key)
            if previous is None or previous[0] != tile or previous[1] != rect:
                self.update(rect)

    # ── вход при показе страницы ─────────────────────────────

    def play_float_in(self, delay_ms: int) -> None:
        """Свой вход (см. ui.widgets.stagger_float_in): видимые плитки и
        заголовки выплывают по очереди. Рисуются готовые картинки с
        прозрачностью и подъёмом — без графического эффекта на всю сетку."""
        self.finish_float_in()
        if not are_live_animations_enabled():
            return
        visible = self.visibleRegion().boundingRect()
        order = [index for index, rect in enumerate(self._rects) if rect.intersects(visible)]
        if not order:
            return
        self._entrance_order = {index: position for position, index in enumerate(order)}
        self._entrance_start = self._now() + max(0, int(delay_ms)) / 1000.0
        self._sync_frames()
        self.update(visible)

    def finish_float_in(self) -> None:
        """Сразу поставить всё на место (страницу скрыли или показали заново)."""
        if self._entrance_start is None:
            return
        self._entrance_start = None
        self._entrance_order = {}
        self._sync_frames()
        self.update()

    def _entrance_progress(self, index: int, now: float) -> float:
        """0..1 — насколько плитка уже выплыла; 1 — вход не идёт."""
        if self._entrance_start is None or index not in self._entrance_order:
            return 1.0
        step = min(self._entrance_order[index], self.ENTRANCE_MAX_STEPS) * self.ENTRANCE_STEP_MS
        return float_in_progress((now - self._entrance_start) * 1000.0 - step)

    def _entrance_finished(self, now: float) -> bool:
        last = min(len(self._entrance_order), self.ENTRANCE_MAX_STEPS + 1) * self.ENTRANCE_STEP_MS
        return (now - self._entrance_start) * 1000.0 >= last + FLOAT_IN_DURATION_MS

    # ── анимации смены ───────────────────────────────────────

    def _start_changes(self, old_by_key: dict, tiles: list[HostsTile]) -> None:
        """Плитка, у которой сменился выбор, крутит иконку и светится.

        Первая загрузка не анимируется: старой плитки с тем же ключом нет.
        """
        if not are_live_animations_enabled():
            self._changes.clear()
            return
        now = self._now()
        for tile in tiles:
            if tile.kind != "tile" or not tile.key or tile.key not in old_by_key:
                continue
            old = old_by_key[tile.key][0]
            if tile.has_choices and old.selected != tile.selected:
                if tile.selected:
                    self._changes[tile.key] = _Change("pick", tile.selected, now)
                elif old.selected:
                    self._changes[tile.key] = _Change("drop", old.selected, now)
            elif tile.has_switch and old.is_on != tile.is_on:
                self._changes[tile.key] = _Change("switch", "", now)

    def _spinning_keys(self) -> set[str]:
        """Плитки, которые сейчас рисуются по кадрам, а не из кэша."""
        keys = set(self._changes)
        if are_live_animations_enabled():
            keys.update(tile.key for tile in self._tiles if tile.pending and tile.has_choices and tile.selected)
        return keys

    def _change_progress(self, key: str) -> tuple[_Change | None, float]:
        change = self._changes.get(key)
        if change is None:
            return None, 1.0
        return change, min(1.0, max(0.0, (self._now() - change.started) / self.CHANGE_SECONDS))

    def _sync_frames(self) -> None:
        if self._spinning_keys() or self._entrance_start is not None:
            if not self._frames.isActive():
                self._frames.start()
        elif self._frames.isActive():
            self._frames.stop()

    def _on_frame(self) -> None:
        now = self._now()
        if self._entrance_start is not None:
            self.update(self.visibleRegion())
            if self._entrance_finished(now):
                self._entrance_start = None
                self._entrance_order = {}
        finished = [key for key, change in self._changes.items() if now - change.started >= self.CHANGE_SECONDS]
        for key in finished:
            del self._changes[key]
        for key in self._spinning_keys() | set(finished):
            index = self._index_of(key)
            if index >= 0:
                self.update(self._rects[index])
        self._sync_frames()

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

    def choice_rect(self, key: str, profile_id: str) -> QRect:
        """Где на сетке иконка профиля (для тестов и подсказок)."""
        index = self._index_of(key)
        if index < 0:
            return QRect()
        for slot, choice in enumerate(self._tiles[index].choices):
            if choice.profile_id == profile_id:
                return self._choice_rect(self._rects[index], slot)
        return QRect()

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
            height = self.GROUP_HEIGHT + (self.LEGEND_HEIGHT if tile.legend else 0)
            self._rects.append(QRect(0, y, width, height))
            y += height
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

    def _choice_at(self, index: int, point: QPoint) -> int:
        """Номер иконки профиля под точкой (только доступные), иначе -1."""
        if not self._is_clickable(index):
            return -1
        tile, rect = self._tiles[index], self._rects[index]
        for slot, choice in enumerate(tile.choices):
            if choice.available and self._choice_rect(rect, slot).contains(point):
                return slot
        return -1

    def _is_clickable(self, index: int) -> bool:
        return 0 <= index < len(self._tiles) and self._tiles[index].kind == "tile" and self._tiles[index].enabled

    def _clickable(self) -> list[int]:
        return [index for index in range(len(self._tiles)) if self._is_clickable(index)]

    def _switch_rect(self, rect: QRect) -> QRect:
        size = self._SWITCH
        return QRect(rect.right() - self._PAD - size.width(), rect.top() + self._PAD, size.width(), size.height())

    def _choice_rect(self, rect: QRect, slot: int) -> QRect:
        size = self._CHOICE
        left = rect.left() + self._PAD + slot * (size + self._CHOICE_GAP)
        return QRect(left, rect.bottom() - self._PAD - size + 3, size, size)

    def _visible_slots(self, rect: QRect) -> int:
        """Сколько иконок влезает в ширину плитки."""
        room = rect.width() - 2 * self._PAD + self._CHOICE_GAP
        return max(0, room // (self._CHOICE + self._CHOICE_GAP))

    # ── отрисовка ────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        tokens = get_theme_tokens()
        dark = isDarkTheme()
        accent = QColor(themeColor()).name()
        dpr = self.devicePixelRatioF()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dirty = event.rect()
        self._live_keys = self._spinning_keys()
        now = self._now()
        for index, rect in enumerate(self._rects):
            if not rect.adjusted(0, 0, 0, int(FLOAT_IN_RISE_PX)).intersects(dirty):
                continue
            entrance = self._entrance_progress(index, now)
            if entrance <= 0.0:
                continue
            if entrance < 1.0:
                painter.save()
                painter.setOpacity(entrance)
                painter.translate(0.0, FLOAT_IN_RISE_PX * (1.0 - entrance))
            tile = self._tiles[index]
            if tile.kind == "tile":
                painter.drawPixmap(rect.topLeft(), self._tile_pixmap(index, rect, tile, tokens, dark, accent, dpr))
            else:
                self._paint_header(painter, rect, tile, tokens)
            if entrance < 1.0:
                painter.restore()
        painter.end()

    def _tile_state(self, index: int) -> tuple:
        hovered = index == self._hover or (index == self._cursor and self.hasFocus())
        return (
            hovered,
            index == self._pressed,
            self._hover_choice if index == self._hover else -1,
            self._pressed_choice if index == self._pressed else -1,
        )

    def _tile_pixmap(self, index: int, rect: QRect, tile: HostsTile, tokens, dark: bool, accent: str, dpr: float) -> QPixmap:
        flash = self._flash.get(tile.key, 0.0)
        # Во время вспышки и кручения плитка рисуется по кадрам, без кэша.
        live = flash > 0 or tile.key in self._live_keys
        key = (tile, rect.width(), rect.height(), self._tile_state(index), dark, accent, dpr)
        cached = None if live else self._pixmaps.get(key)
        if cached is not None:
            return cached
        pixmap = QPixmap(int(rect.width() * dpr), int(rect.height() * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_tile(painter, index, QRect(0, 0, rect.width(), rect.height()), tile, tokens, dark)
        painter.end()
        self.rendered_tiles += 1
        if not live:
            self._pixmaps[key] = pixmap
        return pixmap

    def _paint_header(self, painter: QPainter, rect: QRect, tile: HostsTile, tokens) -> None:
        # Как StrongBodyLabel: 14 px, полужирный; счётчик — приглушённый.
        title_font = self._header_font
        painter.setFont(title_font)
        painter.setPen(to_qcolor(tokens.fg))
        title_area = QRect(rect.left(), rect.top(), rect.width(), self.GROUP_HEIGHT)
        area = title_area.adjusted(2, 0, 0, -8)
        align = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        painter.drawText(area, align, tile.title)
        if tile.counter:
            offset = QFontMetrics(title_font).horizontalAdvance(tile.title) + 10
            painter.setFont(self._caption_font)
            painter.setPen(to_qcolor(tokens.fg_faint))
            painter.drawText(area.adjusted(offset, 0, 0, -1), align, tile.counter)
        if tile.legend:
            legend_area = QRect(rect.left() + 2, title_area.bottom() - 4, rect.width() - 2, self.LEGEND_HEIGHT)
            self._paint_legend(painter, legend_area, tile.legend, tokens)

    def _paint_legend(self, painter: QPainter, area: QRect, legend: tuple[HostsChoice, ...], tokens) -> None:
        """Под заголовком группы: «иконка название» всех провайдеров, в одну строку."""
        metrics = QFontMetrics(self._caption_font)
        icon = self._LEGEND_ICON
        painter.setFont(self._caption_font)
        painter.setPen(to_qcolor(tokens.fg_muted))
        left = area.left()
        for choice in legend:
            width = icon + 5 + metrics.horizontalAdvance(choice.label)
            if left + width > area.right():
                break
            pixmap = get_cached_qta_pixmap(choice.icon_name, color=choice.color, size=icon)
            painter.drawPixmap(left, area.top() + (area.height() - icon) // 2, pixmap)
            text_rect = QRect(left + icon + 5, area.top(), width - icon - 5 + 2, area.height())
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), choice.label)
            left += width + 16

    def _card_colors(self, index: int, tile: HostsTile, dark: bool) -> tuple[QColor, QColor, QColor | None]:
        """Фон и рамка — как у CardWidget; включённая подкрашена акцентом."""
        hovered = index == self._hover or (index == self._cursor and self.hasFocus())
        # Нажатие по иконке профиля не «продавливает» всю карточку.
        pressed = index == self._pressed and self._pressed_choice < 0
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
        angle, glow_alpha, glow_color = self._icon_motion(tile, self._now())
        if glow_alpha > 0 or angle:
            # Переключили: иконка сервиса качается и светится.
            center = QPointF(rect.left() + pad + self._ICON / 2, rect.top() + pad + self._ICON / 2)
            if glow_alpha > 0:
                _change, progress = self._change_progress(tile.key)
                self._paint_glow(painter, center, 14.0 + 10.0 * progress, glow_color, glow_alpha)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.translate(center)
            painter.rotate(angle)
            painter.drawPixmap(QPointF(-self._ICON / 2, -self._ICON / 2), icon)
            painter.restore()
        else:
            painter.drawPixmap(rect.left() + pad, rect.top() + pad, icon)

        text_left = rect.left() + pad + self._ICON + 10
        right_edge = rect.right() - pad
        if tile.has_switch:
            right_edge = self._switch_rect(rect).left() - 10
        elif tile.has_choices:
            # Справа от названия — выбранный профиль или «записываю…».
            state = self._pending_text() if tile.pending else tile.state_text
            if state:
                metrics = QFontMetrics(self._caption_font)
                state_width = min(metrics.horizontalAdvance(state) + 2, (rect.width() - 2 * pad) // 2)
                state_rect = QRect(right_edge - state_width, rect.top() + pad - 2, state_width, 22)
                painter.setFont(self._caption_font)
                if tile.pending or tile.is_on:
                    painter.setPen(QColor(themeColor()))
                else:
                    painter.setPen(to_qcolor(tokens.fg_muted))
                painter.drawText(
                    state_rect,
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    metrics.elidedText(state, Qt.TextElideMode.ElideRight, state_width),
                )
                right_edge = state_rect.left() - 10

        # Название: как BodyLabel, 14 px; лишнее — многоточием (полное — в подсказке).
        title_font = self._title_font_on if tile.is_on else self._title_font
        painter.setFont(title_font)
        painter.setPen(to_qcolor(tokens.fg))
        title_rect = QRect(text_left, rect.top() + pad - 2, max(0, right_edge - text_left), 22)
        painter.drawText(
            title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(title_font).elidedText(tile.title, Qt.TextElideMode.ElideRight, title_rect.width()),
        )

        if tile.has_switch:
            self._paint_switch(painter, self._switch_rect(rect), tile, dark)

        # Вторая строка: иконки профилей или пояснение (как CaptionLabel, 12 px).
        if tile.has_choices:
            self._paint_choices(painter, index, rect, tile, dark)
        else:
            caption_rect = QRect(text_left, rect.top() + pad + 22, rect.right() - pad - text_left, 20)
            if tile.pending:
                self._paint_caption(painter, caption_rect, self._pending_text(), tokens, accent=True)
            elif tile.note:
                self._paint_caption(painter, caption_rect, tile.note, tokens)
        painter.restore()

    @staticmethod
    def _pending_text() -> str:
        return "записываю…"

    def _paint_caption(self, painter: QPainter, area: QRect, text: str, tokens, *, accent: bool = False) -> None:
        if area.width() <= 8:
            return
        painter.setFont(self._caption_font)
        painter.setPen(QColor(themeColor()) if accent else to_qcolor(tokens.fg_muted))
        painter.drawText(
            area,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(self._caption_font).elidedText(text, Qt.TextElideMode.ElideRight, area.width()),
        )

    def _paint_choices(self, painter: QPainter, index: int, rect: QRect, tile: HostsTile, dark: bool) -> None:
        """Ряд иконок профилей. Интерфейс безрамочный: выбранная выделена только
        мягкой заливкой цвета провайдера и значком в полном цвете, без обводки."""
        hover_slot = self._hover_choice if index == self._hover else -1
        pressed_slot = self._pressed_choice if index == self._pressed else -1
        muted = QColor(255, 255, 255, 110) if dark else QColor(0, 0, 0, 95)
        change, progress = self._change_progress(tile.key)
        spinning = tile.pending and are_live_animations_enabled()
        for slot, choice in enumerate(tile.choices[: self._visible_slots(rect)]):
            if not choice.available:
                continue
            area = QRectF(self._choice_rect(rect, slot))
            selected = choice.profile_id == tile.selected
            color = QColor(choice.color)
            painter.setPen(Qt.PenStyle.NoPen)
            # Кручение и свечение: выбранная только что (pick), выключенная
            # (drop — назад и серым) и выбранная, пока идёт запись.
            angle, glow_radius, glow_alpha, glow_color = 0.0, 0.0, 0, color
            if change is not None and change.profile_id == choice.profile_id and progress < 1.0:
                eased = 1.0 - (1.0 - progress) ** 3
                angle = 360.0 * eased * (1 if change.kind == "pick" else -1)
                glow_radius = 12.0 + 12.0 * eased
                glow_alpha = int(170 * (1.0 - progress))
                if change.kind == "drop":
                    glow_color = QColor(160, 160, 160)
            elif spinning and selected:
                now = self._now()
                angle = (now * 420.0) % 360.0
                glow_radius = 17.0
                glow_alpha = int(70 + 50 * math.sin(now * 7.0))
            if glow_alpha > 0:
                self._paint_glow(painter, area.center(), glow_radius, glow_color, glow_alpha)
                painter.setPen(Qt.PenStyle.NoPen)
            if selected:
                back = QColor(color)
                back.setAlpha(86 if dark else 60)
            elif slot == pressed_slot:
                back = QColor(255, 255, 255, 10) if dark else QColor(0, 0, 0, 14)
            elif slot == hover_slot:
                back = QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 10)
            else:
                back = QColor(255, 255, 255, 12) if dark else QColor(0, 0, 0, 6)
            painter.setBrush(back)
            painter.drawEllipse(area)
            highlighted = selected or slot == hover_slot or angle != 0.0
            icon_color = choice.color if highlighted else muted.name(QColor.NameFormat.HexArgb)
            size = self._CHOICE_ICON
            pixmap = get_cached_qta_pixmap(choice.icon_name, color=icon_color, size=size)
            if angle:
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.translate(area.center())
                painter.rotate(angle)
                painter.drawPixmap(QPointF(-size / 2, -size / 2), pixmap)
                painter.restore()
            else:
                offset = (self._CHOICE - size) // 2
                painter.drawPixmap(int(area.left()) + offset, int(area.top()) + offset, pixmap)

    def _icon_motion(self, tile: HostsTile, now: float) -> tuple[float, int, QColor]:
        """Покачивание и свечение иконки сервиса при смене: (угол, прозрачность, цвет).

        Затухающие колебания влево-вправо; включили — свечение цвета сервиса,
        выключили — серое. Без идущей смены — покой.
        """
        change = self._changes.get(tile.key)
        if change is None:
            return 0.0, 0, QColor()
        progress = min(1.0, max(0.0, (now - change.started) / self.CHANGE_SECONDS))
        if progress >= 1.0:
            return 0.0, 0, QColor()
        angle = 16.0 * math.sin(progress * 4.0 * math.pi) * (1.0 - progress)
        turned_on = change.kind == "pick" or (change.kind == "switch" and tile.is_on)
        color = QColor(tile.icon_color or themeColor().name()) if turned_on else QColor(160, 160, 160)
        return angle, int(170 * (1.0 - progress)), color

    @staticmethod
    def _paint_glow(painter: QPainter, center: QPointF, radius: float, color: QColor, alpha: int) -> None:
        """Мягкое круговое свечение: к краю цвет плавно уходит в прозрачность."""
        gradient = QRadialGradient(center, radius)
        inner = QColor(color)
        inner.setAlpha(max(0, min(255, alpha)))
        outer = QColor(color)
        outer.setAlpha(0)
        gradient.setColorAt(0.0, inner)
        gradient.setColorAt(1.0, outer)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(center, radius, radius)

    def _paint_switch(self, painter: QPainter, area: QRect, tile: HostsTile, dark: bool) -> None:
        """Тумблер в цветах переключателя qfluentwidgets; при смене кружок едет и светится."""
        box = QRectF(area).adjusted(1, 1, -1, -1)
        radius = box.height() / 2
        change, progress = self._change_progress(tile.key)
        if change is not None and change.kind == "switch" and progress < 1.0:
            self._paint_glow(painter, box.center(), box.width() / 2 + 10 * progress + 4, QColor(themeColor()), int(150 * (1.0 - progress)))
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
        if change is not None and change.kind == "switch" and progress < 1.0:
            eased = 1.0 - (1.0 - progress) ** 3
            start_x = box.left() + 4 if tile.is_on else box.right() - 4 - 12
            knob_x = start_x + (knob_x - start_x) * eased
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob)
        painter.drawEllipse(QRectF(knob_x, box.center().y() - 6, 12, 12))

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

    # ── действия ─────────────────────────────────────────────

    def _choose(self, index: int, slot: int) -> None:
        """Иконка профиля: выбрать; уже выбранная — выключить."""
        tile = self._tiles[index]
        choice = tile.choices[slot]
        if not choice.available:
            return
        value = None if choice.profile_id == tile.selected else choice.profile_id
        self.profile_chosen.emit(tile.key, value)

    def _cycle(self, index: int) -> None:
        """С клавиатуры: следующий профиль по кругу, после последнего — выкл."""
        tile = self._tiles[index]
        available = [choice.profile_id for choice in tile.choices if choice.available]
        if not available:
            return
        if tile.selected in available:
            position = available.index(tile.selected) + 1
            value = available[position] if position < len(available) else None
        else:
            value = available[0]
        self.profile_chosen.emit(tile.key, value)

    def _activate(self, index: int) -> None:
        """Enter/Пробел: тумблер переключается, DNS-профиль — следующий."""
        if not self._is_clickable(index):
            return
        tile = self._tiles[index]
        if tile.has_choices:
            self._cycle(index)
        elif tile.has_switch:
            self.activated.emit(tile.key)

    # ── мышь и клавиатура ────────────────────────────────────

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            index = self.index_at(point)
            if self._is_clickable(index):
                self._pressed = index
                self._pressed_choice = self._choice_at(index, point)
                self.update(self._rects[index])
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pressed, self._pressed = self._pressed, -1
            pressed_choice, self._pressed_choice = self._pressed_choice, -1
            if 0 <= pressed < len(self._rects):
                self.update(self._rects[pressed])
            point = event.position().toPoint()
            index = self.index_at(point)
            if index == pressed and self._is_clickable(index):
                # Щелчок мышью не прокручивает страницу к плитке.
                self._set_cursor(index, ensure_visible=False)
                tile = self._tiles[index]
                if tile.has_choices:
                    slot = self._choice_at(index, point)
                    if slot >= 0 and slot == pressed_choice:
                        self._choose(index, slot)
                elif tile.has_switch:
                    self.activated.emit(tile.key)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        point = event.position().toPoint()
        index = self.index_at(point)
        hover = index if self._is_clickable(index) else -1
        slot = self._choice_at(hover, point) if hover >= 0 else -1
        self._set_hover(hover, slot)
        tile = self._tiles[index] if index >= 0 else None
        clickable = tile is not None and hover >= 0 and (slot >= 0 or tile.has_switch)
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)
        if tile is None:
            self.setToolTip("")
        elif slot >= 0:
            choice = tile.choices[slot]
            if choice.profile_id == tile.selected:
                self.setToolTip(f"{choice.label} — выбран, щёлкните, чтобы выключить")
            else:
                self.setToolTip(choice.label)
        else:
            self.setToolTip(f"{tile.title}\n{tile.note}" if tile.note else tile.title)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._set_hover(-1, -1)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        if not self._is_clickable(self._cursor):
            clickable = self._clickable()
            # К плитке прокручиваем только при переходе с клавиатуры (Tab),
            # а не когда фокус пришёл от щелчка мышью.
            by_keyboard = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
            self._set_cursor(clickable[0] if clickable else -1, ensure_visible=by_keyboard)
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
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9 and self._is_clickable(self._cursor):
            tile = self._tiles[self._cursor]
            if tile.has_choices:
                number = key - Qt.Key.Key_0
                if number == 0:
                    if tile.selected is not None:
                        self.profile_chosen.emit(tile.key, None)
                elif number <= len(tile.choices) and tile.choices[number - 1].available:
                    self._choose(self._cursor, number - 1)
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

    def _set_hover(self, index: int, slot: int = -1) -> None:
        if index == self._hover and slot == self._hover_choice:
            return
        old, self._hover, self._hover_choice = self._hover, index, slot
        for value in {old, index}:
            if 0 <= value < len(self._rects):
                self.update(self._rects[value])

    def _set_cursor(self, index: int, *, ensure_visible: bool = True) -> None:
        if index == self._cursor:
            return
        old, self._cursor = self._cursor, index
        for value in (old, index):
            if 0 <= value < len(self._rects):
                self.update(self._rects[value])
        if 0 <= index < len(self._tiles):
            tile = self._tiles[index]
            set_control_accessibility(self, name="Сервисы hosts", description=tile.accessible_text or tile.title)
            if ensure_visible:
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


__all__ = ["HostsChoice", "HostsTile", "HostsTilesGrid", "split_service_title"]


def split_service_title(name: str) -> tuple[str, str]:
    """«YouTube (иногда может не работать…)» → («YouTube», «иногда может не работать…»)."""
    text = str(name or "").strip()
    if text.endswith(")") and " (" in text:
        head, _, tail = text.partition(" (")
        if head.strip():
            return head.strip(), tail[:-1].strip()
    return text, ""
