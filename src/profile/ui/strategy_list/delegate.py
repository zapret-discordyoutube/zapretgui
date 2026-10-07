"""Отрисовка строк списка стратегий.

Получает готовую строку (``VisibleRow``) и только рисует её: заголовок группы,
подзаголовок или стратегию.

Список перерисовывается целиком при каждом шаге прокрутки, поэтому на одну
плитку должно уходить как можно меньше обращений к Qt. Всё, что одинаково у
всех строк кадра — цвета темы, шрифты, их мерки, — собирается один раз на кадр
(``_Style``), а значок стратегии выводится готовой картинкой.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QModelIndex, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from profile.strategy_list import BADGE_PERSONAL, BADGE_RECOMMENDED, BADGE_WARNING, ROW_GROUP, ROW_SECTION, VisibleRow
from profile.ui.strategy_list.icons import strategy_icon
from profile.ui.strategy_list.model import ROW_ROLE
from profile.ui.widgets.payload_badge import PAYLOAD_BADGE_HEIGHT, paint_payload_badge, payload_badge_width
from ui.theme import get_cached_qta_pixmap, get_theme_tokens, to_qcolor
from ui.widgets.active_row_motion import active_row_motion
from ui.widgets.fluent_item_tooltip import FluentItemToolTipController
from ui.widgets.folder_header import folder_header_font, folder_header_icon_color, folder_header_icon_name
from ui.widgets.hover_row import paint_profile_hover_row
from ui.widgets.row_hover_motion import paint_icon_motion, row_hover_motion

SELECTED_TEXT = "Выбрана"
MENU_HINT = "Правая кнопка мыши: отметить, работает ли стратегия, или добавить её в избранное."

_ICON_SIZE = 14
# Значок стратегии: крупный на плитке в две строки, мельче в строке в один столбец.
_TILE_ICON_SIZE = 28
_ROW_ICON_SIZE = 20
_STAR_SIZE = 11
_BADGE_HEIGHT = 18
# Сколько места обязано остаться названию: иначе метки справа не рисуются.
_MIN_TEXT_WIDTH = 150
_FAVORITE_ICON = ("fa5s.star", "#d9a441")
_BADGE_COLORS = {BADGE_RECOMMENDED: "#e5b454", BADGE_WARNING: "#e0795a", BADGE_PERSONAL: "#5fbf7a"}


@dataclass(frozen=True)
class _Style:
    """Всё, что одинаково у строк одного кадра: цвета темы, шрифты, мерки."""

    tokens: object
    fg: QColor
    fg_muted: QColor
    fg_faint: QColor
    accent: QColor
    accent_soft: QColor
    # Цвет плитки под значком: им обведена отметка оценки на значке.
    backdrop: str
    font: QFont
    small: QFont
    metrics: QFontMetrics
    small_metrics: QFontMetrics
    selected_width: int


def _build_style(base_font: QFont) -> _Style:
    tokens = get_theme_tokens()
    font = QFont(base_font)
    font.setBold(False)
    small = _smaller(font)
    metrics = QFontMetrics(font)
    fg = to_qcolor(tokens.fg, "#f5f5f5")
    return _Style(
        tokens=tokens,
        fg=fg,
        fg_muted=to_qcolor(tokens.fg_muted, "#b7bec8"),
        fg_faint=to_qcolor(tokens.fg_faint, "#aeb5c1"),
        accent=to_qcolor(tokens.accent_hex, "#5caee8"),
        accent_soft=to_qcolor(tokens.accent_soft_bg_hover, tokens.accent_hex),
        backdrop="#2d2d2d" if fg.lightness() > 128 else "#f4f4f4",
        font=font,
        small=small,
        metrics=metrics,
        small_metrics=QFontMetrics(small),
        selected_width=metrics.horizontalAdvance(SELECTED_TEXT) + 18,
    )


def _smaller(font: QFont) -> QFont:
    small = QFont(font)
    if small.pointSizeF() > 0:
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1.0))
    elif small.pixelSize() > 0:
        small.setPixelSize(max(9, small.pixelSize() - 1))
    return small


def twin_chip_text(row: VisibleRow) -> str:
    """Подпись кнопки вариантов у стратегии с одноимёнными соседями."""
    if row.twin_count < 2:
        return ""
    return "свернуть" if row.twin_open else f"ещё {row.twin_count - 1}"


def strategy_tooltip(row: VisibleRow) -> str:
    item = row.item
    if item is None:
        return ""
    marks = []
    if item.is_current:
        marks.append("Эта стратегия выбрана для профиля.")
    if item.rating == "work":
        marks.append("Зелёная галочка на значке: вы отметили, что эта стратегия работает.")
    elif item.rating == "notwork":
        marks.append("Красный крестик на значке: вы отметили, что эта стратегия не работает.")
    if item.favorite:
        marks.append("Звезда: стратегия у вас в избранном.")
    if row.twin_count > 1:
        marks.append(f"У стратегии {row.twin_count} варианта с этим названием — они отличаются источником.")
    if item.badge_text:
        marks.append(f"Метка «{item.badge_text}» — кнопка: открывает подробности о стратегии.")
    return "\n\n".join(part for part in ("\n".join(marks), item.tooltip, MENU_HINT) if part)


class StrategyListDelegate(QStyledItemDelegate):
    def __init__(self, view) -> None:
        super().__init__(view)
        self._view = view
        self._tooltip = FluentItemToolTipController(view)
        self._pass_style: _Style | None = None

    def begin_pass(self) -> None:
        """Список начинает кадр: общее для всех строк собирается один раз."""
        self._pass_style = _build_style(self._view.font())

    def end_pass(self) -> None:
        self._pass_style = None

    def _style(self, painter: QPainter) -> _Style:
        return self._pass_style or _build_style(painter.font())

    # ------------------------------------------------------------------
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        _ = option
        row = index.data(ROW_ROLE)
        return self._view.row_size(row.kind if row is not None else "")

    def helpEvent(self, event, view, option, index: QModelIndex) -> bool:  # noqa: N802
        _ = (view, option)
        row = index.data(ROW_ROLE)
        text = ""
        if row is not None and row.item is not None:
            text = strategy_tooltip(row)
        elif row is not None and row.kind == ROW_GROUP and row.group is not None:
            text = row.group.description[:1].upper() + row.group.description[1:] if row.group.description else ""
        if not text:
            self._tooltip.hide()
            return True
        self._tooltip.show_text(text, event.globalPos())
        return True

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        row = index.data(ROW_ROLE)
        if row is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if row.kind == ROW_GROUP:
            self._paint_group(painter, option, index, row)
        elif row.kind == ROW_SECTION:
            self._paint_section(painter, option, row)
        else:
            self._paint_strategy(painter, option, index, row)
        painter.restore()

    # ------------------------------------------------------------------
    def _side_rects(self, rect: QRect, row: VisibleRow, style: _Style, left: int) -> tuple[QRect, QRect, QRect, int]:
        """Где на плитке стоят кнопка вариантов, плашка «Выбрана» и метка.

        Одна раскладка для отрисовки и для щелчка мыши: кнопка нажимается
        ровно там, где нарисована. Возвращает ещё правую границу текста.
        """
        item = row.item
        center_y = rect.center().y()
        right = rect.right() - 10
        chip_rect = QRect()
        chip_text = twin_chip_text(row)
        if chip_text:
            width = style.small_metrics.horizontalAdvance(chip_text) + 26
            chip_rect = QRect(right - width, center_y - _BADGE_HEIGHT // 2, width, _BADGE_HEIGHT)
            right = chip_rect.left() - 8
        selected_rect = QRect()
        if item.is_current and right - style.selected_width - left >= _MIN_TEXT_WIDTH:
            selected_rect = QRect(right - style.selected_width, center_y - 10, style.selected_width, 20)
            right = selected_rect.left() - 8
        badge_rect = QRect()
        if item.badge_text:
            width = style.small_metrics.horizontalAdvance(item.badge_text) + 14
            if right - width - left >= _MIN_TEXT_WIDTH:
                badge_rect = QRect(right - width, center_y - _BADGE_HEIGHT // 2, width, _BADGE_HEIGHT)
                right = badge_rect.left() - 8
        return chip_rect, selected_rect, badge_rect, right

    def _text_left(self, rect: QRect) -> int:
        icon_size = _TILE_ICON_SIZE if rect.height() >= 36 else _ROW_ICON_SIZE
        return rect.left() + 14 + icon_size + 10

    def hit_rects(self, option_rect: QRect, row: VisibleRow) -> tuple[QRect, QRect]:
        """Кнопки плитки для щелчка мыши: (варианты, метка-подробности)."""
        if row.item is None:
            return QRect(), QRect()
        style = self._pass_style or _build_style(self._view.font())
        rect = self._view.row_paint_rect(option_rect)
        chip_rect, _selected, badge_rect, _right = self._side_rects(rect, row, style, self._text_left(rect))
        return chip_rect, badge_rect

    def _paint_pill(self, painter: QPainter, rect: QRect, text: str, color: QColor, font: QFont, *, strong: bool) -> None:
        fill = QColor(color)
        fill.setAlpha(34 if strong else 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, rect.height() // 2, rect.height() // 2)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)

    def _paint_strategy(self, painter: QPainter, option, index, row: VisibleRow) -> None:
        item = row.item
        style = self._style(painter)
        rect = self._view.row_paint_rect(option.rect)
        two_lines = rect.height() >= 36
        dimmed = item.rating == "notwork" and not item.is_current
        state = option.state
        hovered = bool(state & QStyle.StateFlag.State_MouseOver)
        focused = bool(state & QStyle.StateFlag.State_HasFocus) or bool(state & QStyle.StateFlag.State_Selected)

        motion = active_row_motion(self._view)
        hover = row_hover_motion(self._view)
        live_hover = hover is not None and not focused
        paint_profile_hover_row(
            painter,
            rect,
            active=item.is_current,
            hovered=hovered,
            selected=focused,
            show_active_marker=not (motion is not None and motion.hides_static_marker(index)),
            active_reveal=motion.row_reveal(index) if motion is not None else None,
            residual_active=motion.row_residual(index) if motion is not None else 0.0,
            hover_level=hover.hover_level(index) if live_hover else None,
            sheen=hover.sheen_progress(index) if live_hover else None,
        )

        metrics = style.metrics
        center_y = rect.center().y()
        # Текст не сдвигается, когда стратегию выбирают: место под полоску
        # акцента оставлено у всех строк.
        left = rect.left() + 14
        right = rect.right() - 10

        icon_size = _TILE_ICON_SIZE if two_lines else _ROW_ICON_SIZE
        icon_dy = round(motion.icon_offset(index)) if motion is not None else 0
        icon_rect = QRect(left, center_y - icon_size // 2 + icon_dy, icon_size, icon_size)
        pixmap = strategy_icon(
            item.family_key,
            item.family_color,
            item.rating,
            icon_size,
            painter.device().devicePixelRatioF(),
            style.backdrop,
        )
        if dimmed:
            painter.setOpacity(0.6)
        paint_icon_motion(painter, icon_rect, hover, index, lambda: painter.drawPixmap(icon_rect, pixmap))
        if dimmed:
            painter.setOpacity(1.0)
        left = self._text_left(rect)

        # Справа по порядку от края: кнопка вариантов, плашка «Выбрана», метка
        # про готовые пресеты, звезда избранного, типы пакетов. Что не
        # помещается рядом с названием — не рисуется.
        min_text = _MIN_TEXT_WIDTH
        chip_rect, selected_rect, badge_rect, right = self._side_rects(rect, row, style, left)

        star_rect = QRect()
        if item.favorite and right - _STAR_SIZE - left >= min_text:
            star_rect = QRect(right - _STAR_SIZE, center_y - _STAR_SIZE // 2, _STAR_SIZE, _STAR_SIZE)
            right = star_rect.left() - 8

        payload_rect = QRect()
        if item.payload_badge:
            payload_width = payload_badge_width(metrics, item.payload_badge)
            if payload_width and right - payload_width - left >= min_text:
                payload_rect = QRect(
                    right - payload_width, center_y - PAYLOAD_BADGE_HEIGHT // 2, payload_width, PAYLOAD_BADGE_HEIGHT
                )
                right = payload_rect.left() - 8

        text_width = max(0, right - left)
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # Способ стоит после уточнения: первым взгляд читает, откуда стратегия.
        if item.detail and item.plain_label:
            second = f"{item.detail} · {item.plain_label}"
        else:
            second = item.detail or item.plain_label
        painter.setFont(style.font)
        painter.setPen(style.fg_muted if dimmed else style.fg)
        if two_lines and second:
            painter.drawText(
                QRect(left, rect.top() + 4, text_width, 18),
                flags,
                metrics.elidedText(item.title, Qt.TextElideMode.ElideRight, text_width),
            )
            painter.setFont(style.small)
            painter.setPen(style.fg_faint if dimmed else style.fg_muted)
            painter.drawText(
                QRect(left, rect.top() + 21, text_width, 15),
                flags,
                style.small_metrics.elidedText(second, Qt.TextElideMode.ElideRight, text_width),
            )
        else:
            # В один столбец строка низкая: название и способ стоят в одну линию.
            title = item.name if not two_lines else item.title
            title_width = min(metrics.horizontalAdvance(title), text_width)
            painter.drawText(
                QRect(left, rect.top(), text_width, rect.height()),
                flags,
                metrics.elidedText(title, Qt.TextElideMode.ElideRight, text_width),
            )
            rest = text_width - title_width - 14
            if not two_lines and item.plain_label and rest >= 80:
                painter.setFont(style.small)
                painter.setPen(style.fg_faint if dimmed else style.fg_muted)
                painter.drawText(
                    QRect(left + title_width + 14, rect.top(), rest, rect.height()),
                    flags,
                    style.small_metrics.elidedText(item.plain_label, Qt.TextElideMode.ElideRight, rest),
                )

        if payload_rect.width() > 0:
            painter.setFont(style.font)
            paint_payload_badge(painter, payload_rect, item.payload_badge, metrics, style.tokens)
        if star_rect.width() > 0:
            star = get_cached_qta_pixmap(_FAVORITE_ICON[0], color=_FAVORITE_ICON[1], size=_STAR_SIZE)
            if not star.isNull():
                painter.drawPixmap(star_rect, star)
        if badge_rect.width() > 0:
            if dimmed:
                painter.setOpacity(0.6)
            tone_color = _BADGE_COLORS.get(item.badge_tone)
            self._paint_pill(
                painter,
                badge_rect,
                item.badge_text,
                QColor(tone_color) if tone_color else style.fg_muted,
                style.small,
                strong=tone_color is not None,
            )
            if dimmed:
                painter.setOpacity(1.0)
        if selected_rect.width() > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(style.accent_soft)
            painter.drawRoundedRect(selected_rect, 9, 9)
            painter.setFont(style.font)
            painter.setPen(style.accent)
            painter.drawText(selected_rect, int(Qt.AlignmentFlag.AlignCenter), SELECTED_TEXT)
        if chip_rect.width() > 0:
            self._paint_twin_chip(painter, chip_rect, row, style)

    def _paint_twin_chip(self, painter: QPainter, rect: QRect, row: VisibleRow, style: _Style) -> None:
        """Кнопка «ещё 4»: раскрывает стратегии с тем же названием."""
        tokens = style.tokens
        font = style.small
        fill = QColor(style.fg_muted)
        fill.setAlpha(30)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, rect.height() // 2, rect.height() // 2)
        arrow = get_cached_qta_pixmap(
            "fa5s.chevron-up" if row.twin_open else "fa5s.chevron-down", color=tokens.fg_muted, size=8
        )
        if not arrow.isNull():
            painter.drawPixmap(QRect(rect.right() - 15, rect.center().y() - 4, 8, 8), arrow)
        painter.setFont(font)
        painter.setPen(style.fg_muted)
        painter.drawText(
            rect.adjusted(8, 0, -18, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            twin_chip_text(row),
        )

    def _paint_section(self, painter: QPainter, option, row: VisibleRow) -> None:
        """Подзаголовок внутри группы: подпись с числом стратегий и тонкой линией."""
        tokens = get_theme_tokens()
        rect = option.rect
        font = _smaller(painter.font())
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        left = rect.left() + 26
        title = row.section.title
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        text_rect = QRect(left, rect.top() + 4, max(0, rect.right() - left - 16), rect.height() - 4)
        painter.setPen(to_qcolor(tokens.fg_muted, "#b7bec8"))
        painter.drawText(text_rect, flags, title)
        left += metrics.horizontalAdvance(title) + 8
        font.setBold(False)
        painter.setFont(font)
        count = str(len(row.section.items))
        painter.setPen(to_qcolor(tokens.fg_faint, "#aeb5c1"))
        painter.drawText(QRect(left, text_rect.top(), 60, text_rect.height()), flags, count)
        left += QFontMetrics(font).horizontalAdvance(count) + 10
        line = to_qcolor(tokens.fg_faint, "#aeb5c1")
        line.setAlpha(50)
        painter.setPen(line)
        y = text_rect.center().y()
        painter.drawLine(left, y, rect.right() - 24, y)

    def _paint_group(self, painter: QPainter, option, index, row: VisibleRow) -> None:
        group = row.group
        tokens = get_theme_tokens()
        rect = self._view.row_paint_rect(option.rect)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        hover = row_hover_motion(self._view)
        paint_profile_hover_row(
            painter,
            rect,
            hovered=hovered,
            selected=focused,
            fill_idle=False,
            hover_level=hover.hover_level(index) if hover is not None and not focused else None,
        )
        center_y = rect.center().y()
        left = rect.left() + 12
        right = rect.right() - 12

        chevron = get_cached_qta_pixmap(folder_header_icon_name(row.expanded), color=folder_header_icon_color(), size=10)
        if not chevron.isNull():
            painter.drawPixmap(QRect(left, center_y - 5, 10, 10), chevron)
        left += 20
        icon = get_cached_qta_pixmap(group.icon_name, color=group.color or tokens.fg_muted, size=_ICON_SIZE)
        if not icon.isNull():
            icon_rect = QRect(left, center_y - _ICON_SIZE // 2, _ICON_SIZE, _ICON_SIZE)
            paint_icon_motion(painter, icon_rect, hover, index, lambda: painter.drawPixmap(icon_rect, icon))
        left += _ICON_SIZE + 10

        body_font = QFont(painter.font())
        body_font.setBold(False)
        metrics = QFontMetrics(body_font)
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # В свёрнутой группе справа видно, что выбранная стратегия лежит в ней.
        current_name = "" if row.expanded else group.current_name
        if current_name:
            width = metrics.horizontalAdvance(SELECTED_TEXT) + 18
            badge = QRect(right - width, center_y - 10, width, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(to_qcolor(tokens.accent_soft_bg_hover, tokens.accent_hex))
            painter.drawRoundedRect(badge, 9, 9)
            painter.setFont(body_font)
            painter.setPen(to_qcolor(tokens.accent_hex, "#5caee8"))
            painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), SELECTED_TEXT)
            right = badge.left() - 10
            name_width = min(metrics.horizontalAdvance(current_name), int(rect.width() * 0.4))
            if name_width >= 60:
                painter.setPen(to_qcolor(tokens.fg, "#f5f5f5"))
                painter.drawText(
                    QRect(right - name_width, rect.top(), name_width, rect.height()),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    metrics.elidedText(current_name, Qt.TextElideMode.ElideRight, name_width),
                )
                right -= name_width + 16

        title_font = folder_header_font(painter.font())
        title_metrics = QFontMetrics(title_font)
        title = title_metrics.elidedText(group.title, Qt.TextElideMode.ElideRight, max(0, right - left))
        painter.setFont(title_font)
        painter.setPen(to_qcolor(tokens.fg, "#f5f5f5"))
        painter.drawText(QRect(left, rect.top(), max(0, right - left), rect.height()), flags, title)
        left += title_metrics.horizontalAdvance(title) + 8

        painter.setFont(body_font)
        count = str(group.count)
        if right - left > metrics.horizontalAdvance(count):
            painter.setPen(to_qcolor(tokens.fg_muted, "#b7bec8"))
            painter.drawText(QRect(left, rect.top(), right - left, rect.height()), flags, count)
            left += metrics.horizontalAdvance(count) + 12
        if group.description and right - left > 80:
            painter.setPen(to_qcolor(tokens.fg_faint, "#aeb5c1"))
            painter.drawText(
                QRect(left, rect.top(), right - left, rect.height()),
                flags,
                metrics.elidedText(group.description, Qt.TextElideMode.ElideRight, right - left),
            )
