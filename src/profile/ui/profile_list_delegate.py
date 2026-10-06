from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QModelIndex, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import QListView, QStyledItemDelegate, QStyle, QStyleOptionViewItem

from profile.list_view_state import (
    PROFILE_METER_MAX_SEGMENTS,
    profile_group_chevron_tooltip,
    profile_group_counter_tooltip,
    profile_group_tooltip,
    profile_state_tooltip,
    profile_strategy_tooltip,
)
from profile.ui.profile_icon import profile_icon_pixmap
from profile.ui.widgets.payload_badge import PAYLOAD_BADGE_HEIGHT, paint_payload_badge, payload_badge_width
from ui.presets_menu.common import cached_icon
from ui.theme import get_theme_tokens, to_qcolor
from ui.widgets.fluent_item_tooltip import FluentItemToolTipController
from ui.widgets.folder_header import (
    FOLDER_HEADER_HEIGHT,
    folder_header_font,
    folder_header_icon_color,
    folder_header_icon_name,
    is_folder_toggle_click,
    paint_folder_header_row,
)
from ui.widgets.hover_row import paint_profile_hover_row, profile_hover_row_rect
from ui.widgets.row_hover_motion import attach_row_hover_motion, paint_icon_motion, row_hover_motion
from ui.widgets.profile_row_style import (
    PROFILE_BADGE_HOSTLIST_BG,
    PROFILE_BADGE_HOSTLIST_FG,
    PROFILE_BADGE_IPSET_BG,
    PROFILE_BADGE_IPSET_FG,
)

from .profile_list_model import ProfileListModel
from .profile_list_view import PROFILE_DROP_MARKER_PROPERTY


class ProfileListDelegate(QStyledItemDelegate):
    action_triggered = pyqtSignal(str, str)

    _ROW_HEIGHT = 44
    _EMPTY_HEIGHT = 64
    _ICON_SIZE = 18
    _BADGE_HEIGHT = 18
    _BADGE_H_PADDING = 8
    _MIN_NAME_WIDTH = 64
    _MIN_STRATEGY_WIDTH = 72
    # Режим плиток: строки ниже и короче, заголовок папки — шапка плитки.
    _TILE_ROW_HEIGHT = 32
    _TILE_HEADER_HEIGHT = 36

    def __init__(self, view: QListView):
        super().__init__(view)
        self._view = view
        self._hover_row = -1
        self._pressed_row = -1
        self._selected_rows: set[int] = set()
        self._tooltip = FluentItemToolTipController(view.viewport())
        attach_row_hover_motion(view, row_filter=_is_profile_row)

    def setHoverRow(self, row: int) -> None:
        self._hover_row = int(row)

    def setPressedRow(self, row: int) -> None:
        self._pressed_row = int(row)

    def setSelectedRows(self, indexes) -> None:
        rows: set[int] = set()
        try:
            for index in indexes or []:
                row = getattr(index, "row", None)
                row_value = row() if callable(row) else row
                if row_value is None:
                    continue
                rows.add(int(row_value))
        except Exception:
            rows = set()

        self._selected_rows = rows
        if self._pressed_row in self._selected_rows:
            self._pressed_row = -1

    def _tile_mode(self) -> bool:
        """Вид раскладывает строки плитками (страница профилей пресета)."""
        enabled = getattr(self._view, "tile_layout_enabled", None)
        return bool(enabled()) if callable(enabled) else False

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        kind = str(index.data(ProfileListModel.KindRole) or "")
        tile_mode = self._tile_mode()
        if kind == "folder":
            return QSize(0, self._TILE_HEADER_HEIGHT if tile_mode else FOLDER_HEADER_HEIGHT)
        if kind == "empty":
            return QSize(0, self._EMPTY_HEIGHT)
        return QSize(0, self._TILE_ROW_HEIGHT if tile_mode else self._ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        kind = str(index.data(ProfileListModel.KindRole) or "")
        tile_mode = self._tile_mode()
        if kind == "folder":
            if tile_mode:
                self._paint_tile_header(painter, option, index)
            else:
                self._paint_folder_row(painter, option, index)
            self._paint_drop_marker(painter, option, index)
            return
        if kind == "empty":
            self._paint_empty_row(painter, option, str(index.data(ProfileListModel.DisplayNameRole) or ""))
            return
        if tile_mode:
            self._paint_tile_profile_row(painter, option, index)
        else:
            self._paint_profile_row(painter, option, index)
        self._paint_drop_marker(painter, option, index)

    def editorEvent(self, event, model, option: QStyleOptionViewItem, index: QModelIndex):
        _ = model
        _ = option
        kind = str(index.data(ProfileListModel.KindRole) or "")
        if kind != "folder":
            return False
        if not is_folder_toggle_click(event):
            return False
        group_key = str(index.data(ProfileListModel.GroupRole) or "")
        if not group_key:
            return False
        self.action_triggered.emit("toggle_folder", group_key)
        return True

    def helpEvent(self, event, view, option, index):  # noqa: N802
        if not index.isValid() or not isinstance(event, QEvent):
            return super().helpEvent(event, view, option, index)
        text = ""
        if self._tile_mode() and hasattr(event, "pos"):
            # В плитке у каждого элемента своя подсказка: шапка, счётчик,
            # точка состояния, способ обхода.
            text = self._tile_element_tooltip(event.pos(), option, index)
        if not text:
            text = str(index.data(ProfileListModel.TooltipRole) or "").strip()
        if not text:
            self._tooltip.hide()
            return False
        pos = event.globalPos() if hasattr(event, "globalPos") else None
        if pos is None and isinstance(event, QMouseEvent):
            pos = event.globalPosition().toPoint()
        if pos is not None:
            self._tooltip.show_text(text.replace("\n", "<br>"), pos)
            return True
        return super().helpEvent(event, view, option, index)

    def _tile_element_tooltip(self, pos, option: QStyleOptionViewItem, index: QModelIndex) -> str:
        """Текст подсказки для того элемента плитки, на который наведена мышь."""
        kind = str(index.data(ProfileListModel.KindRole) or "")
        rect = profile_hover_row_rect(option.rect)
        metrics = QFontMetrics(option.font)
        if kind == "folder":
            count = int(index.data(ProfileListModel.CountRole) or 0)
            active_count = int(index.data(ProfileListModel.ActiveCountRole) or 0)
            layout = self._tile_header_layout_for(rect, index, metrics)
            if layout.chevron_rect.adjusted(-6, -8, 10, 8).contains(pos):
                return profile_group_chevron_tooltip(not bool(index.data(ProfileListModel.CollapsedRole)))
            counter_left = min(
                (part.left() for part in (layout.counter_rect, layout.meter_rect) if part.isValid()),
                default=None,
            )
            if counter_left is not None and pos.x() >= counter_left - 6:
                return profile_group_counter_tooltip(active_count, count)
            return profile_group_tooltip(str(index.data(ProfileListModel.GroupNameRole) or ""), active_count, count)
        if kind != "profile":
            return ""
        in_preset = bool(index.data(ProfileListModel.InPresetRole))
        enabled = bool(index.data(ProfileListModel.EnabledRole))
        row_layout = self._tile_row_parts(rect, index, metrics).layout
        if pos.x() <= row_layout.icon_rect.right() + 5:
            return profile_state_tooltip(
                in_preset=in_preset,
                enabled=enabled,
                icon_in_header=bool(index.data(ProfileListModel.IconInHeaderRole)),
            )
        if row_layout.strategy_rect.isValid() and pos.x() >= row_layout.strategy_rect.left() - 6:
            return profile_strategy_tooltip(
                in_preset=in_preset,
                enabled=enabled,
                strategy_name=str(index.data(ProfileListModel.StrategyNameRole) or ""),
                rating=str(index.data(ProfileListModel.RatingRole) or "").strip().lower(),
                favorite=bool(index.data(ProfileListModel.FavoriteRole)),
            )
        # Над именем — прежняя подробная подсказка: порты, список, стратегия.
        return ""

    def _tile_header_layout_for(self, rect: QRect, index: QModelIndex, metrics: QFontMetrics) -> "TileHeaderLayout":
        count = int(index.data(ProfileListModel.CountRole) or 0)
        active_count = int(index.data(ProfileListModel.ActiveCountRole) or 0)
        return _tile_header_layout(
            rect,
            has_icon=bool(str(index.data(ProfileListModel.IconNameRole) or "")),
            counter_width=metrics.horizontalAdvance(_tile_counter_text(active_count, count)) + 2,
            meter_width=_tile_meter_width(count),
        )

    def _tile_row_parts(self, rect: QRect, index: QModelIndex, metrics: QFontMetrics) -> "TileRowParts":
        """Раскладка строки плитки: одна и та же для отрисовки и для подсказок."""
        strategy_name = str(index.data(ProfileListModel.StrategyNameRole) or "")
        payload_badge = str(index.data(ProfileListModel.StrategyPayloadBadgeRole) or "")
        marks = _tile_feedback_marks(
            str(index.data(ProfileListModel.RatingRole) or "").strip().lower(),
            bool(index.data(ProfileListModel.FavoriteRole)),
        )
        badge_width = payload_badge_width(metrics, payload_badge) if strategy_name else 0
        marks_width = len(marks) * (_TILE_MARK_SIZE + _TILE_MARK_GAP) if strategy_name else 0
        strategy_width = 0
        if strategy_name:
            strategy_width = metrics.horizontalAdvance(strategy_name) + 8 + marks_width
            if badge_width:
                strategy_width += badge_width + _PAYLOAD_BADGE_GAP
        return TileRowParts(
            layout=_tile_row_layout(rect, strategy_width=strategy_width),
            strategy_name=strategy_name,
            payload_badge=payload_badge,
            badge_width=badge_width,
            marks=marks,
            marks_width=marks_width,
        )

    def _paint_folder_row(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        paint_folder_header_row(
            painter,
            option,
            title=str(index.data(ProfileListModel.GroupNameRole) or ""),
            expanded=not bool(index.data(ProfileListModel.CollapsedRole)),
            count=int(index.data(ProfileListModel.CountRole) or 0),
        )

    def _paint_tile_header(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Шапка плитки: значок группы, её имя, «3 из 5», полоска и стрелка сворачивания."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        tokens = get_theme_tokens()
        rect = profile_hover_row_rect(option.rect)
        if bool(option.state & QStyle.StateFlag.State_MouseOver) or bool(
            option.state & QStyle.StateFlag.State_HasFocus
        ):
            paint_profile_hover_row(painter, rect, hovered=True, fill_idle=False, show_active_marker=False)

        expanded = not bool(index.data(ProfileListModel.CollapsedRole))
        count = int(index.data(ProfileListModel.CountRole) or 0)
        active_count = int(index.data(ProfileListModel.ActiveCountRole) or 0)
        icon_name = str(index.data(ProfileListModel.IconNameRole) or "")
        counter_text = _tile_counter_text(active_count, count)
        layout = self._tile_header_layout_for(rect, index, QFontMetrics(painter.font()))

        cached_icon(folder_header_icon_name(expanded), folder_header_icon_color()).paint(painter, layout.chevron_rect)

        if icon_name:
            # Значок общий для всей группы; серый, пока в ней ничего не включено.
            pixmap = profile_icon_pixmap(
                icon_name,
                color=str(index.data(ProfileListModel.IconColorRole) or "#888888") if active_count > 0 else "#888888",
                size=self._ICON_SIZE,
                theme_name=tokens.theme_name,
            )
            if not pixmap.isNull():
                painter.drawPixmap(layout.icon_rect, pixmap)

        body_font = painter.font()
        title_font = _tile_title_font(body_font)
        painter.setFont(title_font)
        painter.setPen(to_qcolor(tokens.fg, "#f5f5f5"))
        title = QFontMetrics(title_font).elidedText(
            str(index.data(ProfileListModel.GroupNameRole) or ""),
            Qt.TextElideMode.ElideRight,
            layout.title_rect.width(),
        )
        painter.drawText(layout.title_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), title)

        if layout.counter_rect.isValid():
            painter.setFont(body_font)
            painter.setPen(to_qcolor(tokens.fg_muted, "#b7bec8"))
            painter.drawText(
                layout.counter_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                counter_text,
            )
        if layout.meter_rect.isValid():
            _paint_tile_meter(
                painter,
                layout.meter_rect,
                tuple(index.data(ProfileListModel.ActiveFlagsRole) or ()),
                active_count,
                count,
                tokens,
            )
        painter.restore()

    def _paint_tile_profile_row(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Строка профиля внутри плитки: значок, имя и справа способ обхода.

        Порты и источник адресов здесь не рисуются — они остаются в подсказке
        строки. Подложку даёт плитка, поэтому строка подсвечивается только
        под мышью.
        """
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        tokens = get_theme_tokens()
        rect = profile_hover_row_rect(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected) or bool(
            option.state & QStyle.StateFlag.State_HasFocus
        )
        hovered = _profile_row_is_interactive(
            index.row(),
            hovered=bool(option.state & QStyle.StateFlag.State_MouseOver),
            selected=selected,
            hover_row=self._hover_row,
            pressed_row=self._pressed_row,
            selected_rows=self._selected_rows,
        )
        hover_motion = row_hover_motion(self._view)
        live_hover = hover_motion is not None and not (
            selected or self._pressed_row == index.row() or index.row() in self._selected_rows
        )
        paint_profile_hover_row(
            painter,
            rect,
            active=False,
            hovered=hovered,
            fill_idle=False,
            show_active_marker=False,
            hover_level=hover_motion.hover_level(index) if live_hover else None,
            sheen=hover_motion.sheen_progress(index) if live_hover else None,
        )

        in_preset = bool(index.data(ProfileListModel.InPresetRole))
        working = in_preset and bool(index.data(ProfileListModel.EnabledRole))

        text_font = painter.font()
        text_font.setBold(False)
        metrics = QFontMetrics(text_font)
        parts = self._tile_row_parts(rect, index, metrics)
        row_layout = parts.layout
        strategy_name = parts.strategy_name
        payload_badge = parts.payload_badge
        badge_width = parts.badge_width
        marks = parts.marks
        marks_width = parts.marks_width

        if bool(index.data(ProfileListModel.IconInHeaderRole)):
            # Значок группы уже стоит в шапке плитки: здесь только состояние.
            _paint_tile_status_dot(painter, row_layout.icon_rect, working, tokens)
        else:
            icon_color = str(index.data(ProfileListModel.IconColorRole) or "#888888")
            if not in_preset:
                icon_color = "#888888"
            moving = hover_motion is not None and hover_motion.icon_moving(index)
            pixmap = profile_icon_pixmap(
                str(index.data(ProfileListModel.IconNameRole) or ""),
                color=icon_color,
                size=self._ICON_SIZE * (2 if moving else 1),
                theme_name=tokens.theme_name,
            )
            if not pixmap.isNull():
                paint_icon_motion(
                    painter,
                    row_layout.icon_rect,
                    hover_motion,
                    index,
                    lambda: painter.drawPixmap(row_layout.icon_rect, pixmap),
                )

        painter.setFont(text_font)
        painter.setPen(to_qcolor(tokens.fg if working else tokens.fg_muted, "#f5f5f5"))
        painter.drawText(
            row_layout.name_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrics.elidedText(
                # Под шапкой группы имя пишется без повтора её названия.
                str(index.data(ProfileListModel.TileNameRole) or ""),
                Qt.TextElideMode.ElideRight,
                row_layout.name_rect.width(),
            ),
        )

        if row_layout.strategy_rect.isValid():
            payload_badge_rect, text_rect = _strategy_payload_badge_rects(row_layout.strategy_rect, badge_width)
            paint_payload_badge(painter, payload_badge_rect, payload_badge, metrics, tokens)
            painter.setFont(text_font)
            elided = metrics.elidedText(
                strategy_name,
                Qt.TextElideMode.ElideRight,
                max(0, text_rect.width() - marks_width),
            )
            painter.setPen(to_qcolor(tokens.fg_muted if working else tokens.fg_faint, "#b7bec8"))
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), elided)
            # Отметки «работает / не работает / в избранном» стоят вплотную
            # перед именем стратегии, чтобы правый край строк оставался ровным.
            mark_right = text_rect.right() - metrics.horizontalAdvance(elided) - _TILE_MARK_GAP
            for icon_name, color in reversed(marks):
                mark_rect = QRect(
                    mark_right - _TILE_MARK_SIZE + 1,
                    text_rect.center().y() - _TILE_MARK_SIZE // 2,
                    _TILE_MARK_SIZE,
                    _TILE_MARK_SIZE,
                )
                if mark_rect.left() < text_rect.left():
                    break
                cached_icon(icon_name, color).paint(painter, mark_rect)
                mark_right = mark_rect.left() - _TILE_MARK_GAP

        painter.restore()

    def _paint_drop_marker(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        marker = self._view.property(PROFILE_DROP_MARKER_PROPERTY)
        if not isinstance(marker, dict):
            return
        try:
            marker_row = int(marker.get("row", -1))
        except Exception:
            marker_row = -1
        if marker_row != index.row():
            return

        tokens = get_theme_tokens()
        accent = to_qcolor(tokens.accent_hex, "#5caee8")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if marker.get("mode") == "folder":
            fill = to_qcolor(tokens.accent_soft_bg_hover, tokens.accent_hex)
            fill.setAlpha(70)
            rect = option.rect.adjusted(4, 2, -8, -2)
            painter.setBrush(fill)
            painter.setPen(QPen(accent, 2))
            painter.drawRoundedRect(rect, 6, 6)
        elif marker.get("mode") == "before":
            line_rect = profile_hover_row_rect(option.rect).adjusted(12, 0, -12, 0)
            pen = QPen(accent, 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            y = line_rect.top() + 2
            painter.drawLine(line_rect.left(), y, line_rect.right(), y)
        elif marker.get("mode") == "after":
            line_rect = profile_hover_row_rect(option.rect).adjusted(12, 0, -12, 0)
            pen = QPen(accent, 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            y = line_rect.bottom() - 2
            painter.drawLine(line_rect.left(), y, line_rect.right(), y)

        painter.restore()

    def _paint_empty_row(self, painter: QPainter, option: QStyleOptionViewItem, text: str) -> None:
        painter.save()
        tokens = get_theme_tokens()
        painter.setPen(to_qcolor(tokens.fg_muted, "#9aa2af"))
        painter.drawText(option.rect, int(Qt.AlignmentFlag.AlignCenter), text)
        painter.restore()

    def _paint_profile_row(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        tokens = get_theme_tokens()
        rect = profile_hover_row_rect(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected) or bool(
            option.state & QStyle.StateFlag.State_HasFocus
        )
        hovered = _profile_row_is_interactive(
            index.row(),
            hovered=bool(option.state & QStyle.StateFlag.State_MouseOver),
            selected=selected,
            hover_row=self._hover_row,
            pressed_row=self._pressed_row,
            selected_rows=self._selected_rows,
        )
        active = str(index.data(ProfileListModel.StrategyIdRole) or "") not in {"", "none"}
        hover_motion = row_hover_motion(self._view)
        # Выделенная или нажатая строка подсвечена всегда, плавно — только наведение мышью.
        live_hover = hover_motion is not None and not (
            selected or self._pressed_row == index.row() or index.row() in self._selected_rows
        )
        paint_profile_hover_row(
            painter,
            rect,
            active=False,
            hovered=hovered,
            show_active_marker=False,
            hover_level=hover_motion.hover_level(index) if live_hover else None,
            sheen=hover_motion.sheen_progress(index) if live_hover else None,
        )

        strategy_name = str(index.data(ProfileListModel.StrategyNameRole) or "")
        payload_badge = str(index.data(ProfileListModel.StrategyPayloadBadgeRole) or "")
        rating = str(index.data(ProfileListModel.RatingRole) or "").strip().lower()
        favorite = bool(index.data(ProfileListModel.FavoriteRole))
        feedback_text = _feedback_text(rating, favorite)
        list_type = str(index.data(ProfileListModel.ListTypeRole) or "")
        badge_text = "Hostlist" if list_type == "hostlist" else ("IPset" if list_type == "ipset" else "")

        meta_font = painter.font()
        meta_font.setBold(False)
        meta_metrics = QFontMetrics(meta_font)
        strategy_text_width = meta_metrics.horizontalAdvance(strategy_name) + 8 if strategy_name else 0
        payload_badge_full_width = payload_badge_width(meta_metrics, payload_badge) if strategy_name else 0
        if payload_badge_full_width:
            strategy_text_width += payload_badge_full_width + _PAYLOAD_BADGE_GAP
        feedback_text_width = meta_metrics.horizontalAdvance(feedback_text) + 8 if feedback_text else 0
        badge_width = meta_metrics.horizontalAdvance(badge_text) + self._BADGE_H_PADDING * 2 if badge_text else 0
        name = str(index.data(ProfileListModel.DisplayNameRole) or "")
        description = str(index.data(ProfileListModel.DescriptionRole) or "")
        name_font = painter.font()
        name_font.setBold(False)
        name_metrics = QFontMetrics(name_font)
        left_text_width = name_metrics.horizontalAdvance(name)
        if description:
            left_text_width += 8 + meta_metrics.horizontalAdvance(description)
        row_layout = _profile_row_layout(
            rect,
            strategy_text_width=strategy_text_width,
            feedback_text_width=feedback_text_width,
            badge_width=badge_width,
            left_text_width=left_text_width,
        )

        icon_color = str(index.data(ProfileListModel.IconColorRole) or "#888888")
        if not bool(index.data(ProfileListModel.InPresetRole)):
            icon_color = "#888888"
        # Пока значок наклоняется, он рисуется из картинки двойного размера:
        # так при повороте и увеличении края остаются чёткими.
        moving = hover_motion is not None and hover_motion.icon_moving(index)
        pixmap = profile_icon_pixmap(
            str(index.data(ProfileListModel.IconNameRole) or ""),
            color=icon_color,
            size=self._ICON_SIZE * (2 if moving else 1),
            theme_name=tokens.theme_name,
        )
        if not pixmap.isNull():
            paint_icon_motion(
                painter,
                row_layout.icon_rect,
                hover_motion,
                index,
                lambda: painter.drawPixmap(row_layout.icon_rect, pixmap),
            )

        painter.setFont(name_font)
        painter.setPen(to_qcolor(tokens.fg, "#f5f5f5"))
        elided_name = name_metrics.elidedText(name, Qt.TextElideMode.ElideRight, row_layout.name_rect.width())
        painter.drawText(row_layout.name_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), elided_name)

        if description and name_metrics.horizontalAdvance(elided_name) + 10 < row_layout.name_rect.width():
            desc_left = min(row_layout.name_rect.right(), row_layout.name_rect.left() + name_metrics.horizontalAdvance(elided_name) + 8)
            desc_rect = QRect(desc_left, rect.center().y() - 8, max(0, row_layout.name_rect.right() - desc_left), 16)
            painter.setFont(meta_font)
            painter.setPen(to_qcolor(tokens.fg_muted, "#b7bec8"))
            painter.drawText(
                desc_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                meta_metrics.elidedText(description, Qt.TextElideMode.ElideRight, desc_rect.width()),
            )

        if row_layout.badge_rect.isValid() and badge_text:
            badge_bg, badge_fg = _badge_palette(list_type)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(to_qcolor(badge_bg, badge_bg))
            painter.drawRoundedRect(row_layout.badge_rect, 9, 9)
            painter.setPen(to_qcolor(badge_fg, "#111111"))
            painter.drawText(row_layout.badge_rect, int(Qt.AlignmentFlag.AlignCenter), badge_text)

        dot_color = _status_dot_color(
            active,
            active_color=tokens.accent_hex,
            fallback=str(tokens.fg_faint),
        )
        painter.setFont(meta_font)
        painter.setPen(to_qcolor(dot_color, "#888888"))
        painter.drawText(row_layout.dot_rect, int(Qt.AlignmentFlag.AlignCenter), "●")

        strategy_color = tokens.fg if active else tokens.fg_muted
        if row_layout.strategy_rect.isValid():
            payload_badge_rect, strategy_text_rect = _strategy_payload_badge_rects(
                row_layout.strategy_rect,
                payload_badge_full_width,
            )
            paint_payload_badge(painter, payload_badge_rect, payload_badge, meta_metrics, tokens)
            painter.setFont(meta_font)
            painter.setPen(to_qcolor(strategy_color, "#b7bec8"))
            painter.drawText(
                strategy_text_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                meta_metrics.elidedText(strategy_name, Qt.TextElideMode.ElideRight, strategy_text_rect.width()),
            )

        if feedback_text and row_layout.feedback_rect.isValid():
            painter.setPen(to_qcolor(_feedback_color(tokens, rating, favorite), "#b7bec8"))
            painter.drawText(
                row_layout.feedback_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                meta_metrics.elidedText(feedback_text, Qt.TextElideMode.ElideRight, row_layout.feedback_rect.width()),
            )

        painter.restore()


_PAYLOAD_BADGE_GAP = 6
# Сколько места оставить имени стратегии рядом со значком (дальше — многоточие).
_PAYLOAD_BADGE_MIN_NAME_WIDTH = 24


def _strategy_payload_badge_rects(strategy_rect: QRect, badge_width: int) -> tuple[QRect, QRect]:
    """(значок типов пакетов, имя стратегии) внутри области стратегии.

    Значок составной стратегии стоит слева и виден всегда; имя стратегии
    сокращается многоточием в оставшемся месте. Если места совсем мало,
    сокращается и сам значок.
    """
    text_rect = QRect(strategy_rect)
    if badge_width <= 0 or not strategy_rect.isValid():
        return QRect(), text_rect
    width = min(int(badge_width), strategy_rect.width() - _PAYLOAD_BADGE_GAP - _PAYLOAD_BADGE_MIN_NAME_WIDTH)
    if width <= 0:
        return QRect(), text_rect
    badge_rect = QRect(
        strategy_rect.left(),
        strategy_rect.center().y() - PAYLOAD_BADGE_HEIGHT // 2,
        width,
        PAYLOAD_BADGE_HEIGHT,
    )
    text_rect.setLeft(badge_rect.right() + 1 + _PAYLOAD_BADGE_GAP)
    return badge_rect, text_rect


@dataclass(frozen=True)
class ProfileRowLayout:
    icon_rect: QRect
    name_rect: QRect
    badge_rect: QRect
    dot_rect: QRect
    strategy_rect: QRect
    feedback_rect: QRect


def _profile_row_layout(
    rect: QRect,
    *,
    strategy_text_width: int,
    feedback_text_width: int,
    badge_width: int,
    left_text_width: int = 0,
) -> ProfileRowLayout:
    icon_size = ProfileListDelegate._ICON_SIZE
    min_name_width = ProfileListDelegate._MIN_NAME_WIDTH
    min_strategy_width = ProfileListDelegate._MIN_STRATEGY_WIDTH
    right_padding = 12
    gap = 4
    dot_width = 10
    row_center_y = rect.center().y()

    icon_rect = QRect(rect.left() + 12, row_center_y - 9, icon_size, icon_size)
    text_left = icon_rect.right() + 10
    right_edge = rect.right() - right_padding
    available_after_name = max(0, right_edge - (text_left + min_name_width))

    requested_strategy_width = max(0, int(strategy_text_width or 0))
    max_strategy_width = max(min_strategy_width, rect.width() // 3)
    strategy_width = min(requested_strategy_width, max_strategy_width)
    required_strategy_width = min(strategy_width, min_strategy_width)
    if strategy_width <= 0 or available_after_name < dot_width + gap + required_strategy_width:
        strategy_width = 0

    feedback_width = max(0, int(feedback_text_width or 0))
    if feedback_width:
        right_block_with_feedback = dot_width + gap + strategy_width + gap + feedback_width
        if strategy_width <= 0 or right_block_with_feedback > available_after_name:
            feedback_width = 0

    right_block_width = dot_width
    if strategy_width:
        right_block_width += gap + strategy_width
    if feedback_width:
        right_block_width += gap + feedback_width

    right_block_left = max(text_left, right_edge - right_block_width)
    dot_rect = QRect(right_block_left, row_center_y - 8, dot_width, 16)
    strategy_rect = QRect()
    feedback_rect = QRect()
    cursor_left = dot_rect.right() + gap
    if strategy_width:
        strategy_rect = QRect(cursor_left, row_center_y - 9, strategy_width, 18)
        cursor_left = strategy_rect.right() + gap
    if feedback_width:
        feedback_rect = QRect(cursor_left, row_center_y - 9, feedback_width, 18)

    left_right = max(text_left, right_block_left - 10)
    left_area_width = max(0, left_right - text_left)
    badge_rect = QRect()
    normalized_badge_width = max(0, int(badge_width or 0))
    badge_gap = 8
    can_show_badge = normalized_badge_width > 0 and left_area_width >= (min_name_width + badge_gap + normalized_badge_width)
    name_width = left_area_width
    if can_show_badge:
        requested_text_width = int(left_text_width or (left_area_width - badge_gap - normalized_badge_width))
        requested_text_width = max(min_name_width, requested_text_width)
        name_width = min(requested_text_width, left_area_width - badge_gap - normalized_badge_width)
        badge_rect = QRect(
            text_left + name_width + badge_gap,
            row_center_y - (ProfileListDelegate._BADGE_HEIGHT // 2),
            normalized_badge_width,
            ProfileListDelegate._BADGE_HEIGHT,
        )

    name_rect = QRect(text_left, row_center_y - 10, max(0, name_width), 20)
    return ProfileRowLayout(
        icon_rect=icon_rect,
        name_rect=name_rect,
        badge_rect=badge_rect,
        dot_rect=dot_rect,
        strategy_rect=strategy_rect,
        feedback_rect=feedback_rect,
    )


_TILE_MARK_SIZE = 11
_TILE_MARK_GAP = 5
_TILE_METER_SEGMENT_WIDTH = 10
_TILE_METER_SEGMENT_GAP = 2
_TILE_METER_HEIGHT = 4
# Больше стольких профилей — вместо отдельных делений одна сплошная полоска.
_TILE_METER_MAX_SEGMENTS = PROFILE_METER_MAX_SEGMENTS
_TILE_METER_BAR_WIDTH = 64


@dataclass(frozen=True)
class TileRowLayout:
    icon_rect: QRect
    name_rect: QRect
    strategy_rect: QRect


def _tile_row_layout(rect: QRect, *, strategy_width: int) -> TileRowLayout:
    icon_size = ProfileListDelegate._ICON_SIZE
    min_name_width = ProfileListDelegate._MIN_NAME_WIDTH
    gap = 12
    row_center_y = rect.center().y()

    icon_rect = QRect(rect.left() + 10, row_center_y - icon_size // 2, icon_size, icon_size)
    text_left = icon_rect.right() + 10
    right_edge = rect.right() - 10
    available = max(0, right_edge - text_left + 1)

    # Способ обхода занимает не больше половины строки: имя сайта главнее.
    requested = max(0, int(strategy_width or 0))
    width = min(requested, available // 2)
    if width and available - width - gap < min_name_width:
        width = max(0, available - gap - min_name_width)
    # Прячем способ, только когда от него остался бы обрывок. Короткое
    # название («pass») показывается целиком.
    if width < min(requested, 40):
        width = 0

    strategy_rect = QRect()
    name_width = available
    if width:
        strategy_rect = QRect(right_edge - width + 1, row_center_y - 9, width, 18)
        name_width = max(0, strategy_rect.left() - gap - text_left)
    return TileRowLayout(
        icon_rect=icon_rect,
        name_rect=QRect(text_left, row_center_y - 10, name_width, 20),
        strategy_rect=strategy_rect,
    )


@dataclass(frozen=True)
class TileRowParts:
    layout: TileRowLayout
    strategy_name: str
    payload_badge: str
    badge_width: int
    marks: tuple[tuple[str, str], ...]
    marks_width: int


@dataclass(frozen=True)
class TileHeaderLayout:
    icon_rect: QRect
    title_rect: QRect
    counter_rect: QRect
    meter_rect: QRect
    chevron_rect: QRect


def _tile_header_layout(rect: QRect, *, has_icon: bool, counter_width: int, meter_width: int) -> TileHeaderLayout:
    icon_size = ProfileListDelegate._ICON_SIZE
    row_center_y = rect.center().y()
    # Значок шапки стоит в том же месте, что значок или точка у строк, поэтому
    # имя группы и имена профилей начинаются с одной вертикали.
    icon_rect = QRect(rect.left() + 10, row_center_y - icon_size // 2, icon_size, icon_size)
    text_left = icon_rect.right() + 10 if has_icon else icon_rect.left()
    chevron_rect = QRect(rect.right() - 10 - 11, row_center_y - 6, 12, 12)
    cursor = chevron_rect.left() - 10

    meter_rect = QRect()
    counter_rect = QRect()
    # Счётчик и полоска показываются, только если имени группы остаётся место.
    if meter_width > 0 and cursor - meter_width - text_left >= 160:
        meter_rect = QRect(
            cursor - meter_width + 1,
            row_center_y - _TILE_METER_HEIGHT // 2,
            meter_width,
            _TILE_METER_HEIGHT,
        )
        cursor = meter_rect.left() - 8
    if counter_width > 0 and cursor - counter_width - text_left >= 96:
        counter_rect = QRect(cursor - counter_width + 1, row_center_y - 9, counter_width, 18)
        cursor = counter_rect.left() - 10
    return TileHeaderLayout(
        icon_rect=icon_rect if has_icon else QRect(),
        title_rect=QRect(text_left, rect.top(), max(0, cursor - text_left), rect.height()),
        counter_rect=counter_rect,
        meter_rect=meter_rect,
        chevron_rect=chevron_rect,
    )


def _tile_title_font(base_font):
    """Имя группы чуть крупнее строк: шапка плитки читается первой."""
    font = folder_header_font(base_font)
    if font.pointSizeF() > 0:
        font.setPointSizeF(font.pointSizeF() + 1.0)
    elif font.pixelSize() > 0:
        font.setPixelSize(font.pixelSize() + 1)
    return font


def _paint_tile_status_dot(painter: QPainter, slot: QRect, working: bool, tokens) -> None:
    """Точка на месте значка: закрашена — профиль работает, кольцо — нет."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    center = slot.center()
    if working:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(to_qcolor(tokens.accent_hex, "#5caee8"))
        painter.drawEllipse(center, 4, 4)
    else:
        painter.setPen(QPen(to_qcolor(tokens.fg_faint, "#8f9aa6"), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, 3, 3)
    painter.restore()


def _tile_counter_text(active_count: int, count: int) -> str:
    return f"{max(0, int(active_count))} из {max(0, int(count))}"


def _tile_meter_width(count: int) -> int:
    count = max(0, int(count))
    if count <= 0:
        return 0
    if count > _TILE_METER_MAX_SEGMENTS:
        return _TILE_METER_BAR_WIDTH
    return count * _TILE_METER_SEGMENT_WIDTH + (count - 1) * _TILE_METER_SEGMENT_GAP


def _paint_tile_meter(
    painter: QPainter,
    rect: QRect,
    active_flags: tuple[bool, ...],
    active_count: int,
    count: int,
    tokens,
) -> None:
    """Полоска «сколько профилей группы включено».

    Пока профилей немного, у каждого своё деление в том же порядке, что и
    строки плитки: видно не только сколько включено, но и какие именно.
    """
    count = max(0, int(count))
    active_count = max(0, min(count, int(active_count)))
    if count <= 0 or not rect.isValid():
        return
    radius = _TILE_METER_HEIGHT / 2
    filled = to_qcolor(tokens.accent_hex, "#5caee8")
    empty = to_qcolor(tokens.divider_strong, "#5f6368")
    painter.setPen(Qt.PenStyle.NoPen)
    if count > _TILE_METER_MAX_SEGMENTS:
        painter.setBrush(empty)
        painter.drawRoundedRect(rect, radius, radius)
        filled_width = round(rect.width() * active_count / count)
        if filled_width > 0:
            painter.setBrush(filled)
            painter.drawRoundedRect(
                QRect(rect.left(), rect.top(), max(_TILE_METER_HEIGHT, filled_width), rect.height()),
                radius,
                radius,
            )
        return
    flags = tuple(bool(flag) for flag in active_flags)
    if len(flags) != count:
        flags = tuple(position < active_count for position in range(count))
    left = rect.left()
    for flag in flags:
        painter.setBrush(filled if flag else empty)
        painter.drawRoundedRect(QRect(left, rect.top(), _TILE_METER_SEGMENT_WIDTH, rect.height()), radius, radius)
        left += _TILE_METER_SEGMENT_WIDTH + _TILE_METER_SEGMENT_GAP


def _tile_feedback_marks(rating: str, favorite: bool) -> tuple[tuple[str, str], ...]:
    """Те же отметки, что текст _feedback_text в списке, но значками: (значок, цвет)."""
    marks: list[tuple[str, str]] = []
    if rating == "work":
        marks.append(("fa5s.check", "#49a35f"))
    elif rating == "notwork":
        marks.append(("fa5s.times", "#d85c5c"))
    if favorite:
        marks.append(("fa5s.star", "#d9a441"))
    return tuple(marks)


def _feedback_text(rating: str, favorite: bool) -> str:
    parts: list[str] = []
    if rating == "work":
        parts.append("стратегия работает")
    elif rating == "notwork":
        parts.append("стратегия не работает")
    if favorite:
        parts.append("стратегия в избранном")
    return " • ".join(parts)


def _badge_palette(list_type: str) -> tuple[str, str]:
    if list_type == "hostlist":
        return PROFILE_BADGE_HOSTLIST_BG, PROFILE_BADGE_HOSTLIST_FG
    if list_type == "ipset":
        return PROFILE_BADGE_IPSET_BG, PROFILE_BADGE_IPSET_FG
    return "#7d8792", "#111114"


def _tinted_background_enabled() -> bool:
    try:
        from settings.appearance import peek_warmed_tinted_settings

        plan = peek_warmed_tinted_settings()
        return bool(getattr(plan, "tinted_background", False))
    except Exception:
        return False


def _profile_row_uses_accent(active: bool, *, tinted_background: bool | None = None) -> bool:
    if not bool(active):
        return False
    if tinted_background is None:
        tinted_background = _tinted_background_enabled()
    return bool(tinted_background)


def _status_dot_color(
    active: bool,
    *,
    active_color: str = "#5caee8",
    fallback: str = "#8f9aa6",
    tinted_background: bool | None = None,
) -> str:
    _ = tinted_background
    if bool(active):
        return str(active_color or "#5caee8")
    return str(fallback or "#8f9aa6")


def _is_profile_row(index) -> bool:
    return str(index.data(ProfileListModel.KindRole) or "") not in {"folder", "empty"}


def _profile_row_is_interactive(
    row: int,
    *,
    hovered: bool,
    selected: bool,
    hover_row: int,
    pressed_row: int,
    selected_rows: set[int],
) -> bool:
    row = int(row)
    return (
        bool(hovered)
        or bool(selected)
        or int(hover_row) == row
        or int(pressed_row) == row
        or row in set(selected_rows or set())
    )


def _feedback_color(tokens, rating: str, favorite: bool) -> str:
    if rating == "work":
        return "#49a35f"
    if rating == "notwork":
        return "#d85c5c"
    if favorite:
        return "#d9a441"
    return str(tokens.fg_muted)


__all__ = ["ProfileListDelegate"]
