from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QEvent, QModelIndex, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics, QHelpEvent, QMouseEvent, QPainter, QPen, QTransform
from PyQt6.QtWidgets import QApplication, QListView, QStyledItemDelegate, QStyle, QStyleOptionViewItem

from ui.theme import get_theme_tokens
from ui.widgets.fluent_item_tooltip import FluentItemToolTipController
from ui.widgets.folder_header import FOLDER_HEADER_HEIGHT, is_folder_toggle_click, paint_folder_header_row
from ui.widgets.active_row_motion import active_row_motion
from ui.widgets.hover_row import paint_profile_hover_row, profile_hover_row_rect
from ui.widgets.row_hover_motion import attach_row_hover_motion, paint_icon_motion, row_hover_motion

from .common import (
    PRESET_DROP_MARKER_PROPERTY,
    cached_icon,
    normalize_preset_icon_color,
    pick_contrast_color,
    preset_columns_for_width,
    preset_full_row_width,
    set_current_index_if_changed,
    to_qcolor,
    tr_text,
)
from .model import PresetListModel


class PresetListDelegate(QStyledItemDelegate):
    action_triggered = pyqtSignal(str, str)

    # Строка в одну линию: дата и кнопки показываются только под мышью.
    _ROW_HEIGHT = 36
    _SECTION_HEIGHT = 24
    _EMPTY_HEIGHT = 64
    _ACTION_SIZE = 28
    _ACTION_SPACING = 6
    _BADGE_HEIGHT = 18
    _BADGE_H_PADDING = 8
    _BADGE_GAP = 8
    _PIN_SIZE = 14
    _PIN_COLUMN_WIDTH = 18
    _PIN_TO_ICON_SPACING = 8
    _RATING_STAR_SIZE = 11
    _RATING_STAR_GAP = 4
    _RATING_STAR_COLOR = "#d9a441"

    _ACTION_ICONS = {
        "folder": "fa5s.folder-open",
        "rating": "fa5s.star-half-alt",
        "edit": "fa5s.ellipsis-v",
    }

    _PENDING_SHAKE_ROTATIONS = (0, -8, 8, -6, 6, -4, 4, -2, 0)
    _PENDING_SHAKE_INTERVAL_MS = 50
    # Сколько щелчок ждёт второго, прежде чем включить пресет.
    _DOUBLE_CLICK_WAIT_MS = 300
    _DATE_GAP = 12
    _REMOTE_ICON_SPACE = 20

    def __init__(self, view: QListView, *, language_scope: str = "winws2", help_name_role: str = "name"):
        super().__init__(view)
        self._view = view
        self._language_scope = str(language_scope or "winws2")
        self._help_name_role = str(help_name_role or "name")
        self._ui_language = "ru"
        self._action_tooltips: dict[str, str] = {}
        self._hover_row = -1
        self._pressed_row = -1
        self._selected_rows: set[int] = set()
        self._pending_destructive: Optional[tuple[str, str]] = None
        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.timeout.connect(self._clear_pending_destructive)
        self._pending_shake_step = 0
        self._pending_shake_rotation = 0
        self._pending_shake_timer = QTimer(self)
        self._pending_shake_timer.timeout.connect(self._advance_pending_shake)
        self._pending_activation = ""
        self._activation_timer = QTimer(self)
        self._activation_timer.setSingleShot(True)
        self._activation_timer.timeout.connect(self._emit_pending_activation)
        self._tooltip = FluentItemToolTipController(view.viewport())
        attach_row_hover_motion(view, row_filter=_is_preset_row)
        self.set_ui_language("ru")

    def _tr(self, key: str, default: str, **kwargs) -> str:
        return tr_text(key, self._ui_language, default, **kwargs)

    def set_ui_language(self, language: str) -> None:
        self._ui_language = language
        prefix = f"page.{self._language_scope}_user_presets.delegate.tooltip"
        self._action_tooltips = {
            "rating": self._tr(f"{prefix}.rating", "Поставить рейтинг"),
            "edit": self._tr(f"{prefix}.edit", "Меню пресета"),
            "pin": self._tr(f"{prefix}.pin", "Закрепить сверху"),
        }

    def reset_interaction_state(self):
        self._clear_pending_destructive(update=False)
        self._cancel_pending_activation()
        self.setHoverRow(-1)
        self.setPressedRow(-1)
        self.setSelectedRows([])

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

    def _icon_rect_for_row(self, row_rect: QRect, depth: int) -> QRect:
        pin_rect = self._pin_rect(row_rect, "preset", depth)
        if pin_rect is not None:
            icon_left = pin_rect.left() + self._PIN_COLUMN_WIDTH + self._PIN_TO_ICON_SPACING
        else:
            icon_left = row_rect.left() + 12 + depth * 18
        return QRect(icon_left, row_rect.center().y() - 10, 20, 20)

    def _view_width(self) -> int:
        try:
            return int(self._view.viewport().width())
        except Exception:
            return 0

    def _side_by_side(self) -> bool:
        return preset_columns_for_width(self._view_width())[0] > 1

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        # Ширина строки и есть раскладка: список переносит строки слева
        # направо, поэтому пресеты шириной в столбец встают в ряд, а заголовок
        # папки шириной во весь список всегда занимает отдельную линию.
        view_width = self._view_width()
        full_width = preset_full_row_width(view_width)
        kind = index.data(PresetListModel.KindRole)
        if kind == "folder":
            return QSize(full_width, FOLDER_HEADER_HEIGHT)
        if kind == "section":
            return QSize(full_width, self._SECTION_HEIGHT)
        if kind == "empty":
            return QSize(full_width, self._EMPTY_HEIGHT)
        return QSize(preset_columns_for_width(view_width)[1], self._ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        kind = index.data(PresetListModel.KindRole)

        if kind == "folder":
            self._paint_folder_row(painter, option, index)
            self._paint_drop_marker(painter, option, index)
            return

        if kind == "section":
            self._paint_section_row(painter, option, str(index.data(PresetListModel.TextRole) or ""))
            return

        if kind == "empty":
            self._paint_empty_row(painter, option, str(index.data(PresetListModel.TextRole) or ""))
            return

        self._paint_preset_row(painter, option, index)
        self._paint_drop_marker(painter, option, index)

    def editorEvent(self, event, model, option: QStyleOptionViewItem, index: QModelIndex):
        _ = model
        kind = str(index.data(PresetListModel.KindRole) or "")
        if kind == "folder":
            if not is_folder_toggle_click(event):
                return False
            folder_key = str(index.data(PresetListModel.FolderKeyRole) or "")
            if folder_key:
                set_current_index_if_changed(self._view, index)
                self.action_triggered.emit("toggle_folder", folder_key)
                return True
            return False

        if kind != "preset":
            return False
        if event.type() != QEvent.Type.MouseButtonRelease:
            return False
        if not isinstance(event, QMouseEvent):
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False

        item_id = str(index.data(PresetListModel.FileNameRole) or "")
        if not item_id:
            return False

        set_current_index_if_changed(self._view, index)
        is_active = bool(index.data(PresetListModel.ActiveRole))
        is_builtin = bool(index.data(PresetListModel.BuiltinRole))
        depth = int(index.data(PresetListModel.DepthRole) or 0)
        action = self._action_at(option.rect, kind, is_active, is_builtin, depth, event.position().toPoint())

        if action:
            self._cancel_pending_activation()
            self._handle_action_click(item_id, action, event, index)
            return True

        self._clear_pending_destructive(update=False)
        second_click = self._activation_timer.isActive() and self._pending_activation == item_id
        if second_click or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Двойной щелчок и Shift+щелчок открывают страницу пресета и не
            # включают его: посмотреть текст — не то же самое, что запустить.
            self._cancel_pending_activation()
            self.action_triggered.emit("open", item_id)
            return True

        # Обычный щелчок включает пресет не сразу: сначала ждём, не придёт ли
        # второй, иначе двойной щелчок заодно перезапускал бы обход.
        self._pending_activation = item_id
        self._activation_timer.start(min(self._DOUBLE_CLICK_WAIT_MS, QApplication.doubleClickInterval()))
        return True

    def _cancel_pending_activation(self) -> None:
        self._activation_timer.stop()
        self._pending_activation = ""

    def _emit_pending_activation(self) -> None:
        item_id = self._pending_activation
        self._pending_activation = ""
        if item_id:
            self.action_triggered.emit("activate", item_id)

    def helpEvent(self, event: QHelpEvent, view, option: QStyleOptionViewItem, index: QModelIndex) -> bool:
        kind = str(index.data(PresetListModel.KindRole) or "")
        if kind != "preset":
            return super().helpEvent(event, view, option, index)

        if self._help_name_role == "file_name":
            name = str(index.data(PresetListModel.FileNameRole) or "")
        else:
            name = str(index.data(PresetListModel.NameRole) or "")
        is_active = bool(index.data(PresetListModel.ActiveRole))
        is_builtin = bool(index.data(PresetListModel.BuiltinRole))
        depth = int(index.data(PresetListModel.DepthRole) or 0)
        action = self._action_at(option.rect, kind, is_active, is_builtin, depth, event.pos())
        if not action:
            return super().helpEvent(event, view, option, index)

        tooltip = self._action_tooltips.get(action, "")
        if tooltip:
            self._tooltip.show_text(tooltip, event.globalPos())
            return True
        self._tooltip.hide()
        return super().helpEvent(event, view, option, index)

    def _handle_action_click(self, name: str, action: str, _event: QMouseEvent, index: QModelIndex):
        self._clear_pending_destructive(update=False)
        self.action_triggered.emit(action, name)
        self._update_preset_row_view(index)

    def _clear_pending_destructive(self, update: bool = True):
        self._pending_timer.stop()
        self._pending_shake_timer.stop()
        self._pending_shake_step = 0
        self._pending_shake_rotation = 0
        pending = self._pending_destructive
        if pending is None:
            return
        self._pending_destructive = None
        if update:
            self._update_pending_destructive_row(pending)

    def _advance_pending_shake(self):
        self._pending_shake_step += 1
        if self._pending_shake_step >= len(self._PENDING_SHAKE_ROTATIONS):
            self._pending_shake_timer.stop()
            self._pending_shake_step = 0
            self._pending_shake_rotation = 0
            self._update_pending_destructive_row()
            return

        self._pending_shake_rotation = int(self._PENDING_SHAKE_ROTATIONS[self._pending_shake_step])
        self._update_pending_destructive_row()

    def _update_pending_destructive_row(self, pending: Optional[tuple[str, str]] = None) -> None:
        target = pending if pending is not None else self._pending_destructive
        if target is None:
            return
        file_name = str(target[0] or "").strip()
        if not file_name:
            return
        model = self._view.model()
        finder = getattr(model, "find_preset_row", None)
        if not callable(finder):
            return
        try:
            row = int(finder(file_name))
        except Exception:
            return
        if row < 0:
            return
        self._update_preset_row_view(model.index(row, 0))

    def _update_preset_row_view(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        rect = self._view.visualRect(index)
        if rect.isValid():
            self._view.viewport().update(rect)

    def _visible_actions(self, kind: str, is_active: bool, is_builtin: bool) -> list[str]:
        _ = (kind, is_active, is_builtin)
        return ["rating", "edit"]

    def _action_rects(self, row_rect: QRect, kind: str, is_active: bool, is_builtin: bool) -> list[tuple[str, QRect]]:
        actions = self._visible_actions(kind, is_active, is_builtin)
        if not actions:
            return []

        total_width = len(actions) * self._ACTION_SIZE + (len(actions) - 1) * self._ACTION_SPACING
        x = row_rect.right() - 12 - total_width + 1
        y = row_rect.center().y() - (self._ACTION_SIZE // 2)

        rects: list[tuple[str, QRect]] = []
        for action in actions:
            rects.append((action, QRect(x, y, self._ACTION_SIZE, self._ACTION_SIZE)))
            x += self._ACTION_SIZE + self._ACTION_SPACING
        return rects

    def _action_at(self, option_rect: QRect, kind: str, is_active: bool, is_builtin: bool, depth: int, pos) -> Optional[str]:
        pin_rect = self._pin_hit_rect(option_rect, kind, depth)
        if pin_rect is not None and pin_rect.contains(pos):
            return "pin"

        for action, rect in self._action_rects(option_rect, kind, is_active, is_builtin):
            if rect.contains(pos):
                return action
        return None

    def _paint_action_icon(self, painter: QPainter, icon_name: str, icon_color: str, icon_rect: QRect, rotation: int = 0):
        icon = cached_icon(icon_name, icon_color)
        if not rotation:
            icon.paint(painter, icon_rect)
            return

        painter.save()
        center = icon_rect.center()
        transform = QTransform()
        transform.translate(center.x(), center.y())
        transform.rotate(rotation)
        transform.translate(-center.x(), -center.y())
        painter.setTransform(transform, combine=True)
        icon.paint(painter, icon_rect)
        painter.restore()

    def _pin_rect(self, row_rect: QRect, kind: str, depth: int) -> QRect | None:
        if kind != "preset":
            return None
        x = row_rect.left() + 12 + depth * 18
        return QRect(x, row_rect.center().y() - (self._PIN_SIZE // 2), self._PIN_SIZE, self._PIN_SIZE)

    def _pin_hit_rect(self, row_rect: QRect, kind: str, depth: int) -> QRect | None:
        visual_rect = self._pin_rect(row_rect, kind, depth)
        if visual_rect is None:
            return None
        size = max(self._ACTION_SIZE, self._PIN_SIZE)
        return QRect(
            visual_rect.center().x() - (size // 2),
            visual_rect.center().y() - (size // 2),
            size,
            size,
        )

    def _paint_section_row(self, painter: QPainter, option: QStyleOptionViewItem, text: str) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect.adjusted(12, 0, -12, 0)
        tokens = get_theme_tokens()

        painter.setPen(to_qcolor(tokens.fg_muted, "#9aa2af"))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)

        line_y = rect.center().y()
        text_width = painter.fontMetrics().horizontalAdvance(text)
        left_end = rect.left() + text_width + 12
        if left_end < rect.right():
            painter.setPen(to_qcolor(tokens.divider, "#5f6368"))
            painter.drawLine(left_end, line_y, rect.right(), line_y)

        painter.restore()

    def _paint_folder_row(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        collapsed = bool(index.data(PresetListModel.CollapsedRole))
        text = str(index.data(PresetListModel.NameRole) or index.data(PresetListModel.TextRole) or "")
        count = int(index.data(PresetListModel.CountRole) or 0)
        paint_folder_header_row(
            painter,
            option,
            title=text,
            expanded=not collapsed,
            count=count,
        )

    def _paint_drop_marker(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        marker = self._view.property(PRESET_DROP_MARKER_PROPERTY)
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
        elif marker.get("mode") in ("before", "after"):
            before = marker.get("mode") == "before"
            row_rect = profile_hover_row_rect(option.rect)
            pen = QPen(accent, 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            if self._side_by_side():
                # В столбцах пресеты стоят рядом, место вставки — сбоку от строки.
                line_rect = row_rect.adjusted(0, 4, 0, -4)
                x = line_rect.left() + 2 if before else line_rect.right() - 2
                painter.drawLine(x, line_rect.top(), x, line_rect.bottom())
            else:
                line_rect = row_rect.adjusted(12, 0, -12, 0)
                y = line_rect.top() + 2 if before else line_rect.bottom() - 2
                painter.drawLine(line_rect.left(), y, line_rect.right(), y)

        painter.restore()

    def _paint_empty_row(self, painter: QPainter, option: QStyleOptionViewItem, text: str) -> None:
        painter.save()
        tokens = get_theme_tokens()
        painter.setPen(to_qcolor(tokens.fg_muted, "#9aa2af"))
        painter.drawText(option.rect, int(Qt.AlignmentFlag.AlignCenter), text)
        painter.restore()

    def _paint_preset_row(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        tokens = get_theme_tokens()
        rect = profile_hover_row_rect(option.rect)

        name = str(index.data(PresetListModel.NameRole) or "")
        date_text = str(index.data(PresetListModel.DateRole) or "")
        is_active = bool(index.data(PresetListModel.ActiveRole))
        is_builtin = bool(index.data(PresetListModel.BuiltinRole))
        depth = int(index.data(PresetListModel.DepthRole) or 0)
        is_pinned = bool(index.data(PresetListModel.PinnedRole))
        rating = int(index.data(PresetListModel.RatingRole) or 0)
        marker = self._view.property(PRESET_DROP_MARKER_PROPERTY)
        try:
            drag_marker_row = int(marker.get("row", -1)) if isinstance(marker, dict) else -1
        except Exception:
            drag_marker_row = -1
        drag_marker_visible = drag_marker_row >= 0

        hovered = option.state & QStyle.StateFlag.State_MouseOver
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        pressed = self._pressed_row == index.row()
        file_name = str(index.data(PresetListModel.FileNameRole) or "")
        # Дата, булавка и кнопки строки не шумят на всём списке: они видны
        # только на строке под мышью или с клавиатурным фокусом.
        revealed = (
            bool(hovered)
            or focused
            or pressed
            or (self._pending_destructive is not None and self._pending_destructive[0] == file_name)
        )

        motion = active_row_motion(self._view)
        hover_motion = row_hover_motion(self._view)
        live_hover = hover_motion is not None and not focused
        row_paint = paint_profile_hover_row(
            painter,
            rect,
            active=is_active and not drag_marker_visible,
            hovered=bool(hovered) or focused,
            pressed=pressed,
            show_active_marker=False,
            active_reveal=motion.row_reveal(index) if motion is not None else None,
            residual_active=motion.row_residual(index) if motion is not None else 0.0,
            hover_level=hover_motion.hover_level(index) if live_hover else None,
            sheen=hover_motion.sheen_progress(index) if live_hover else None,
        )
        bg = row_paint.background

        icon_rect = self._icon_rect_for_row(rect, depth)
        if motion is not None:
            # После переезда бегунка значок нового активного пресета подпрыгивает.
            icon_rect = icon_rect.translated(0, round(motion.icon_offset(index)))
        icon_color = pick_contrast_color(
            normalize_preset_icon_color(str(index.data(PresetListModel.IconColorRole) or "")),
            bg,
            [tokens.accent_hex, tokens.fg],
            minimum_ratio=2.6,
        )
        paint_icon_motion(
            painter,
            icon_rect,
            hover_motion,
            index,
            lambda: cached_icon("fa5s.file-alt", icon_color).paint(painter, icon_rect),
        )

        text_left = icon_rect.right() + 10
        actions = self._action_rects(rect, "preset", is_active, is_builtin)
        # Место под кнопки занято всегда, даже пока они скрыты: имя не должно
        # перестраиваться, когда на строку наводят мышь.
        right_bound = rect.right() - 12
        if actions:
            right_bound = actions[0][1].left() - 10

        pin_rect = self._pin_rect(rect, "preset", depth)

        meta_font = painter.font()
        meta_font.setBold(False)
        painter.setFont(meta_font)
        meta_metrics = QFontMetrics(meta_font)

        badge_text = self._tr("page.user_presets.delegate.active_badge", "Активный") if is_active else ""
        badge_rect = QRect()
        right_cursor = right_bound
        if badge_text:
            badge_text_width = meta_metrics.horizontalAdvance(badge_text)
            badge_width = badge_text_width + self._BADGE_H_PADDING * 2
            badge_width = min(max(badge_width, 68), max(0, right_bound - text_left))
            if badge_width > 0:
                badge_rect = QRect(
                    max(text_left, right_cursor - badge_width),
                    rect.center().y() - (self._BADGE_HEIGHT // 2),
                    badge_width,
                    self._BADGE_HEIGHT,
                )
                right_cursor = max(text_left, badge_rect.left() - self._BADGE_GAP)

        # Оценка пользователя — единственная пометка, которая видна всегда.
        rating_text = str(rating) if rating else ""
        rating_rect = QRect()
        if rating_text:
            rating_width = (
                self._RATING_STAR_SIZE + self._RATING_STAR_GAP + meta_metrics.horizontalAdvance(rating_text)
            )
            if right_cursor - rating_width - text_left > 48:
                rating_rect = QRect(right_cursor - rating_width, rect.center().y() - 9, rating_width, 18)
                right_cursor = rating_rect.left() - self._BADGE_GAP

        name_rect = QRect(text_left, rect.center().y() - 10, max(0, right_cursor - text_left), 20)

        name_font = painter.font()
        name_font.setBold(is_active)
        painter.setFont(name_font)
        name_metrics = QFontMetrics(name_font)

        # Имя важнее даты: в узком столбце дата появляется под мышью, только
        # если помещается рядом с полным именем и ничего у него не отнимает.
        date_rect = QRect()
        if date_text:
            date_width = meta_metrics.horizontalAdvance(date_text)
            name_end = text_left + name_metrics.horizontalAdvance(name)
            if bool(index.data(PresetListModel.RemoteRole)):
                name_end += self._REMOTE_ICON_SPACE
            if right_cursor - date_width - self._DATE_GAP >= name_end:
                date_rect = QRect(right_cursor - date_width, rect.center().y() - 9, date_width, 18)
        elided_name = name_metrics.elidedText(name, Qt.TextElideMode.ElideRight, name_rect.width())
        name_flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        repeated_prefix = name[: int(index.data(PresetListModel.RepeatedPrefixLengthRole) or 0)]
        if repeated_prefix and len(elided_name) > len(repeated_prefix) and elided_name.startswith(repeated_prefix):
            # Начало имени повторяет название папки над строкой — приглушаем
            # его, чтобы взгляд сразу попадал на отличие.
            painter.setPen(to_qcolor(tokens.fg_faint, "#aeb5c1"))
            painter.drawText(name_rect, name_flags, repeated_prefix)
            painter.setPen(to_qcolor(tokens.fg, "#f5f5f5"))
            painter.drawText(
                name_rect.adjusted(name_metrics.horizontalAdvance(repeated_prefix), 0, 0, 0),
                name_flags,
                elided_name[len(repeated_prefix):],
            )
        else:
            painter.setPen(to_qcolor(tokens.fg, "#f5f5f5"))
            painter.drawText(name_rect, name_flags, elided_name)

        if bool(index.data(PresetListModel.RemoteRole)):
            remote_state = str(index.data(PresetListModel.RemoteStateRole) or "")
            cloud_left = name_rect.left() + name_metrics.horizontalAdvance(elided_name) + 6
            if cloud_left + 14 <= name_rect.right():
                cloud_color = tokens.fg_faint
                if remote_state in ("detached", "error"):
                    try:
                        from ui.theme_semantic import get_semantic_palette

                        cloud_color = get_semantic_palette(tokens.theme_name).warning_soft
                    except Exception:
                        cloud_color = "#ff9800"
                cloud_rect = QRect(cloud_left, name_rect.center().y() - 6, 13, 13)
                cached_icon("fa5s.cloud", cloud_color).paint(painter, cloud_rect)

        painter.setFont(meta_font)
        if revealed and date_rect.width() > 0:
            painter.setPen(to_qcolor(tokens.fg_faint, "#aeb5c1"))
            elided_date = meta_metrics.elidedText(date_text, Qt.TextElideMode.ElideLeft, date_rect.width())
            painter.drawText(date_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), elided_date)
        if rating_rect.isValid():
            star_rect = QRect(
                rating_rect.left(),
                rating_rect.center().y() - self._RATING_STAR_SIZE // 2,
                self._RATING_STAR_SIZE,
                self._RATING_STAR_SIZE,
            )
            cached_icon("fa5s.star", self._RATING_STAR_COLOR).paint(painter, star_rect)
            painter.setPen(to_qcolor(tokens.fg_muted, "#b7bec8"))
            painter.drawText(
                rating_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                rating_text,
            )
        if badge_rect.width() > 0 and badge_text:
            badge_bg = to_qcolor(tokens.accent_soft_bg_hover, tokens.accent_hex)
            badge_text_color = to_qcolor(tokens.accent_hex, "#5caee8")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(badge_bg)
            painter.drawRoundedRect(badge_rect, 9, 9)
            painter.setPen(badge_text_color)
            painter.drawText(
                badge_rect,
                int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter),
                meta_metrics.elidedText(badge_text, Qt.TextElideMode.ElideRight, max(0, badge_rect.width() - self._BADGE_H_PADDING * 2)),
            )

        if pin_rect is not None and (is_pinned or revealed):
            pin_color = tokens.accent_hex if is_pinned else tokens.fg_faint
            self._paint_action_icon(
                painter,
                "fa5s.thumbtack",
                pin_color,
                pin_rect.adjusted(2, 2, -2, -2),
            )

        for action, action_rect in actions if revealed else ():
            btn_bg = to_qcolor(tokens.surface_bg_hover, tokens.surface_bg)
            icon_color = pick_contrast_color(
                str(tokens.fg_muted),
                btn_bg,
                [tokens.fg],
                minimum_ratio=2.6,
            )

            painter.setBrush(btn_bg)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(action_rect, 6, 6)

            icon_name = self._ACTION_ICONS.get(action, "fa5s.circle")
            rotation = self._pending_shake_rotation if self._pending_destructive == (file_name, action) else 0
            self._paint_action_icon(
                painter,
                icon_name,
                icon_color,
                action_rect.adjusted(7, 7, -7, -7),
                rotation=rotation,
            )

        painter.restore()


def _is_preset_row(index) -> bool:
    return str(index.data(PresetListModel.KindRole) or "") == "preset"


__all__ = ["PresetListDelegate"]
