"""Отрисовка строк списка стратегий.

Получает готовую строку (``VisibleRow``) и только рисует её: заголовок группы,
подзаголовок или стратегию. Ничего не считает и не хранит.
"""

from __future__ import annotations

from PyQt6.QtCore import QModelIndex, QRect, QSize, Qt
from PyQt6.QtGui import QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from profile.strategy_list import BADGE_RECOMMENDED, BADGE_WARNING, ROW_GROUP, ROW_SECTION, VisibleRow
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
_STAR_SIZE = 11
_BADGE_HEIGHT = 18
_FAVORITE_ICON = ("fa5s.star", "#d9a441")
# Значок слева говорит, что человек знает о стратегии: ещё не пробовал,
# работает или не работает. Способ обхода написан словами под названием.
_STATUS_UNTRIED = "fa5.circle"
_STATUS_WORKS = ("fa5s.check-circle", "#49a35f")
_STATUS_NOT_WORKS = ("fa5s.times-circle", "#d85c5c")
_BADGE_COLORS = {BADGE_RECOMMENDED: "#e5b454", BADGE_WARNING: "#e0795a"}


def status_icon(rating: str, tokens) -> tuple[str, str]:
    if rating == "work":
        return _STATUS_WORKS
    if rating == "notwork":
        return _STATUS_NOT_WORKS
    return _STATUS_UNTRIED, tokens.fg_faint


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
        marks.append("Зелёная галочка: вы отметили, что эта стратегия работает.")
    elif item.rating == "notwork":
        marks.append("Красный крестик: вы отметили, что эта стратегия не работает.")
    else:
        marks.append("Пустой кружок: вы ещё не отмечали, работает ли эта стратегия.")
    if item.favorite:
        marks.append("Звезда: стратегия у вас в избранном.")
    if row.twin_count > 1:
        marks.append(f"У стратегии {row.twin_count} варианта с этим названием — они отличаются источником.")
    return "\n\n".join(part for part in ("\n".join(marks), item.tooltip, MENU_HINT) if part)


class StrategyListDelegate(QStyledItemDelegate):
    def __init__(self, view) -> None:
        super().__init__(view)
        self._view = view
        self._tooltip = FluentItemToolTipController(view)

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
    def twin_chip_rect(self, option_rect: QRect, row: VisibleRow, font: QFont) -> QRect:
        """Где на плитке стоит кнопка вариантов; пустой прямоугольник — её нет."""
        text = twin_chip_text(row)
        if not text:
            return QRect()
        rect = self._view.row_paint_rect(option_rect)
        width = QFontMetrics(_smaller(font)).horizontalAdvance(text) + 26
        return QRect(rect.right() - 10 - width, rect.center().y() - _BADGE_HEIGHT // 2, width, _BADGE_HEIGHT)

    def _paint_pill(self, painter: QPainter, rect: QRect, text: str, color: str, font: QFont, *, strong: bool) -> None:
        if rect.width() <= 0 or not text:
            return
        fill = to_qcolor(color, "#aeb5c1")
        fill.setAlpha(34 if strong else 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, rect.height() // 2, rect.height() // 2)
        painter.setFont(font)
        painter.setPen(to_qcolor(color, "#aeb5c1"))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)

    def _paint_strategy(self, painter: QPainter, option, index, row: VisibleRow) -> None:
        item = row.item
        tokens = get_theme_tokens()
        rect = self._view.row_paint_rect(option.rect)
        two_lines = rect.height() >= 36
        dimmed = item.rating == "notwork" and not item.is_current
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus) or bool(
            option.state & QStyle.StateFlag.State_Selected
        )

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

        font = QFont(painter.font())
        font.setBold(False)
        small = _smaller(font)
        metrics = QFontMetrics(font)
        center_y = rect.center().y()
        # Текст не сдвигается, когда стратегию выбирают: место под полоску
        # акцента оставлено у всех строк.
        left = rect.left() + 18
        right = rect.right() - 10

        icon_name, icon_color = status_icon(item.rating, tokens)
        icon_dy = round(motion.icon_offset(index)) if motion is not None else 0
        icon_rect = QRect(left, center_y - _ICON_SIZE // 2 + icon_dy, _ICON_SIZE, _ICON_SIZE)
        moving = hover is not None and hover.icon_moving(index)
        pixmap = get_cached_qta_pixmap(icon_name, color=icon_color, size=_ICON_SIZE * (2 if moving else 1))
        if not pixmap.isNull():
            painter.setOpacity(0.55 if dimmed else 1.0)
            paint_icon_motion(painter, icon_rect, hover, index, lambda: painter.drawPixmap(icon_rect, pixmap))
            painter.setOpacity(1.0)
        left = icon_rect.right() + 10

        # Справа по порядку от края: кнопка вариантов, плашка «Выбрана», метка
        # про готовые пресеты, звезда избранного, типы пакетов. Что не
        # помещается рядом с названием — не рисуется.
        min_text = 150
        chip_rect = self.twin_chip_rect(option.rect, row, font)
        if chip_rect.width() > 0:
            right = chip_rect.left() - 8

        selected_rect = QRect()
        if item.is_current:
            width = metrics.horizontalAdvance(SELECTED_TEXT) + 18
            if right - width - left >= min_text:
                selected_rect = QRect(right - width, center_y - 10, width, 20)
                right = selected_rect.left() - 8

        badge_rect = QRect()
        if item.badge_text:
            width = QFontMetrics(small).horizontalAdvance(item.badge_text) + 14
            if right - width - left >= min_text:
                badge_rect = QRect(right - width, center_y - _BADGE_HEIGHT // 2, width, _BADGE_HEIGHT)
                right = badge_rect.left() - 8

        star_rect = QRect()
        if item.favorite and right - _STAR_SIZE - left >= min_text:
            star_rect = QRect(right - _STAR_SIZE, center_y - _STAR_SIZE // 2, _STAR_SIZE, _STAR_SIZE)
            right = star_rect.left() - 8

        payload_rect = QRect()
        payload_width = payload_badge_width(metrics, item.payload_badge)
        if payload_width and right - payload_width - left >= min_text:
            payload_rect = QRect(
                right - payload_width, center_y - PAYLOAD_BADGE_HEIGHT // 2, payload_width, PAYLOAD_BADGE_HEIGHT
            )
            right = payload_rect.left() - 8

        text_width = max(0, right - left)
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # Способ стоит после уточнения: первым взгляд читает, откуда стратегия.
        second = " · ".join(part for part in (item.detail, item.plain_label) if part)
        painter.setFont(font)
        painter.setPen(to_qcolor(tokens.fg_muted if dimmed else tokens.fg, "#f5f5f5"))
        if two_lines and second:
            painter.drawText(
                QRect(left, rect.top() + 4, text_width, 18),
                flags,
                metrics.elidedText(item.title, Qt.TextElideMode.ElideRight, text_width),
            )
            painter.setFont(small)
            painter.setPen(to_qcolor(tokens.fg_faint if dimmed else tokens.fg_muted, "#aeb5c1"))
            painter.drawText(
                QRect(left, rect.top() + 21, text_width, 15),
                flags,
                QFontMetrics(small).elidedText(second, Qt.TextElideMode.ElideRight, text_width),
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
                painter.setFont(small)
                painter.setPen(to_qcolor(tokens.fg_faint if dimmed else tokens.fg_muted, "#aeb5c1"))
                painter.drawText(
                    QRect(left + title_width + 14, rect.top(), rest, rect.height()),
                    flags,
                    QFontMetrics(small).elidedText(item.plain_label, Qt.TextElideMode.ElideRight, rest),
                )
        painter.setFont(font)

        paint_payload_badge(painter, payload_rect, item.payload_badge, metrics, tokens)
        if star_rect.width() > 0:
            star = get_cached_qta_pixmap(_FAVORITE_ICON[0], color=_FAVORITE_ICON[1], size=_STAR_SIZE)
            if not star.isNull():
                painter.drawPixmap(star_rect, star)
        painter.setOpacity(0.6 if dimmed else 1.0)
        self._paint_pill(
            painter,
            badge_rect,
            item.badge_text,
            _BADGE_COLORS.get(item.badge_tone, tokens.fg_muted),
            small,
            strong=item.badge_tone in _BADGE_COLORS,
        )
        painter.setOpacity(1.0)
        if selected_rect.width() > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(to_qcolor(tokens.accent_soft_bg_hover, tokens.accent_hex))
            painter.drawRoundedRect(selected_rect, 9, 9)
            painter.setFont(font)
            painter.setPen(to_qcolor(tokens.accent_hex, "#5caee8"))
            painter.drawText(selected_rect, int(Qt.AlignmentFlag.AlignCenter), SELECTED_TEXT)
        if chip_rect.width() > 0:
            self._paint_twin_chip(painter, chip_rect, row, small, tokens)

    def _paint_twin_chip(self, painter: QPainter, rect: QRect, row: VisibleRow, font: QFont, tokens) -> None:
        """Кнопка «ещё 4»: раскрывает стратегии с тем же названием."""
        fill = to_qcolor(tokens.fg_muted, "#aeb5c1")
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
        painter.setPen(to_qcolor(tokens.fg_muted, "#aeb5c1"))
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
