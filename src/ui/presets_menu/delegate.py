from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QElapsedTimer, QEvent, QModelIndex, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFontMetrics, QHelpEvent, QLinearGradient, QMouseEvent, QPainter, QPen, QTransform
from PyQt6.QtWidgets import QApplication, QListView, QStyledItemDelegate, QStyle, QStyleOptionViewItem

from ui.theme import get_theme_tokens
from ui.widgets.fluent_item_tooltip import FluentItemToolTipController
from ui.widgets.folder_header import FOLDER_HEADER_HEIGHT, is_folder_toggle_click, paint_folder_header_row
from ui.widgets.active_row_motion import active_row_motion
from ui.widgets.hover_row import paint_profile_hover_row
from ui.widgets.row_hover_motion import attach_row_hover_motion, row_hover_motion

from .common import (
    PRESET_DROP_MARKER_PROPERTY,
    PRESET_TILE_MAX_WIDTH,
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

    # Пресет — плитка в одну линию: значок, имя и пометки справа. Кнопки
    # появляются только под мышью, дата и полное имя — в подсказке.
    _ROW_HEIGHT = 32
    _SECTION_HEIGHT = 24
    _EMPTY_HEIGHT = 64
    _TILE_GAP = 3
    _TILE_PADDING = 8
    _ICON_SIZE = 18
    _ICON_GAP = 8
    _ACTION_SIZE = 24
    _ACTION_SPACING = 4
    _MARK_SIZE = 11
    _MARK_GAP = 6
    # Место справа от имени под пометки (оценка, булавка, облако).
    _MARKS_SPACE = 40
    _NAME_FADE_WIDTH = 18
    _RATING_STAR_GAP = 4
    _RATING_STAR_COLOR = "#d9a441"

    _ACTION_ICONS = {
        "pin": "fa5s.thumbtack",
        "edit": "fa5s.ellipsis-v",
    }

    _PENDING_SHAKE_ROTATIONS = (0, -8, 8, -6, 6, -4, 4, -2, 0)
    _PENDING_SHAKE_INTERVAL_MS = 50
    # Запас к системному времени двойного щелчка: одиночный щелчок включает
    # пресет только когда второй уже точно не придёт.
    _DOUBLE_CLICK_MARGIN_MS = 30

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
        self._press_timer = QElapsedTimer()
        self._skip_release = False
        self._activation_timer = QTimer(self)
        self._activation_timer.setSingleShot(True)
        self._activation_timer.timeout.connect(self._emit_pending_activation)
        self._bound_model = None
        self._wanted_tile_width: int | None = None
        self._tooltip = FluentItemToolTipController(view.viewport())
        attach_row_hover_motion(view, row_filter=_is_preset_row)
        self.set_ui_language("ru")

    def _tr(self, key: str, default: str, **kwargs) -> str:
        return tr_text(key, self._ui_language, default, **kwargs)

    def set_ui_language(self, language: str) -> None:
        self._ui_language = language
        prefix = f"page.{self._language_scope}_user_presets.delegate.tooltip"
        self._action_tooltips = {
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

    def _tile_rect(self, row_rect: QRect) -> QRect:
        return row_rect.adjusted(self._TILE_GAP, 2, -self._TILE_GAP, -2)

    def _view_width(self) -> int:
        try:
            return int(self._view.viewport().width())
        except Exception:
            return 0

    def _bind_model(self, model) -> None:
        if model is self._bound_model:
            return
        self._bound_model = model
        self._wanted_tile_width = None
        if model is None:
            return
        for signal in (model.modelReset, model.rowsInserted, model.rowsRemoved, model.rowsMoved, model.layoutChanged):
            signal.connect(self._forget_tile_width)
        model.dataChanged.connect(self._on_model_data_changed)

    def _forget_tile_width(self, *args) -> None:
        self._wanted_tile_width = None

    def _on_model_data_changed(self, top_left, bottom_right, roles=()) -> None:
        if not roles or PresetListModel.NameRole in roles:
            self._wanted_tile_width = None

    def _shown_name(self, index: QModelIndex) -> str:
        """Имя без начала, которое повторяет заголовок папки над плиткой."""
        name = str(index.data(PresetListModel.NameRole) or "")
        return name[int(index.data(PresetListModel.RepeatedPrefixLengthRole) or 0):]

    def _tile_width_for_longest_name(self) -> int:
        """Ширина плитки, в которую целиком входят почти все имена списка."""
        model = self._view.model()
        self._bind_model(model)
        if self._wanted_tile_width is None:
            font = self._view.font()
            # Активный пресет написан жирным — меряем с запасом на него.
            font.setBold(True)
            metrics = QFontMetrics(font)
            widths: list[int] = []
            for row in range(model.rowCount() if model is not None else 0):
                index = model.index(row, 0)
                if index.data(PresetListModel.KindRole) == "preset":
                    widths.append(metrics.horizontalAdvance(self._shown_name(index)))
            widths.sort()
            # Одно-два очень длинных имени не должны раздувать все столбцы:
            # ширину задают девять имён из десяти, остальные сокращаются
            # (полное имя есть в подсказке).
            longest = widths[max(0, -(-len(widths) * 9 // 10) - 1)] if widths else 0
            chrome = (
                2 * self._TILE_GAP
                + 2 * self._TILE_PADDING
                + self._ICON_SIZE
                + self._ICON_GAP
                + self._MARKS_SPACE
            )
            self._wanted_tile_width = min(PRESET_TILE_MAX_WIDTH, longest + chrome)
        return self._wanted_tile_width

    def column_layout(self) -> tuple[int, int]:
        """Число столбцов пресетов и ширина одного столбца."""
        return preset_columns_for_width(self._view_width(), self._tile_width_for_longest_name())

    def _side_by_side(self) -> bool:
        return self.column_layout()[0] > 1

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        # Ширина строки и есть раскладка: список переносит строки слева
        # направо, поэтому плитки шириной в столбец встают в ряд, а заголовок
        # папки шириной во весь список всегда занимает отдельную линию.
        full_width = preset_full_row_width(self._view_width())
        kind = index.data(PresetListModel.KindRole)
        if kind == "folder":
            return QSize(full_width, FOLDER_HEADER_HEIGHT)
        if kind == "section":
            return QSize(full_width, self._SECTION_HEIGHT)
        if kind == "empty":
            return QSize(full_width, self._EMPTY_HEIGHT)
        return QSize(self.column_layout()[1], self._ROW_HEIGHT)

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
        if not isinstance(event, QMouseEvent) or event.button() != Qt.MouseButton.LeftButton:
            return False
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress:
            self._skip_release = False
            self._press_timer.start()
            return False
        if event_type not in (QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick):
            return False

        item_id = str(index.data(PresetListModel.FileNameRole) or "")
        if not item_id:
            return False

        action = self._action_at(option.rect, event.position().toPoint())
        if event_type == QEvent.Type.MouseButtonDblClick:
            # Второе нажатие двойного щелчка система присылает отдельным
            # событием. По нему и открываем страницу пресета: посмотреть
            # текст — не то же самое, что запустить.
            self._skip_release = False
            if action:
                return False
            self._skip_release = True
            self._cancel_pending_activation()
            self._clear_pending_destructive(update=False)
            set_current_index_if_changed(self._view, index)
            self.action_triggered.emit("open", item_id)
            return True

        if self._skip_release:
            # Отпускание кнопки после двойного щелчка — уже не новый щелчок.
            self._skip_release = False
            return True

        set_current_index_if_changed(self._view, index)

        if action:
            self._cancel_pending_activation()
            self._handle_action_click(item_id, action, event, index)
            return True

        self._clear_pending_destructive(update=False)
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Shift+щелчок открывает страницу пресета и не включает его.
            self._cancel_pending_activation()
            self.action_triggered.emit("open", item_id)
            return True

        # Обычный щелчок включает пресет не сразу: сначала ждём, не придёт ли
        # второй, иначе двойной щелчок заодно перезапускал бы обход. Система
        # отсчитывает двойной щелчок от первого нажатия — ждём столько же.
        self._pending_activation = item_id
        since_press = self._press_timer.elapsed() if self._press_timer.isValid() else 0
        wait = QApplication.doubleClickInterval() - since_press + self._DOUBLE_CLICK_MARGIN_MS
        self._activation_timer.start(max(self._DOUBLE_CLICK_MARGIN_MS, wait))
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

        action = self._action_at(option.rect, event.pos())
        if action:
            tooltip = self._action_tooltips.get(action, "")
        else:
            # Дата и полное имя на плитку не помещаются — они в подсказке.
            name_role = PresetListModel.FileNameRole if self._help_name_role == "file_name" else PresetListModel.NameRole
            lines = [str(index.data(name_role) or "")]
            date_text = str(index.data(PresetListModel.DateRole) or "")
            if date_text:
                lines.append(self._tr("page.user_presets.delegate.tooltip.changed", "Изменён: {date}", date=date_text))
            lines.append(
                self._tr(
                    "page.user_presets.delegate.tooltip.open_hint",
                    "Щелчок — включить, двойной щелчок — открыть текст",
                )
            )
            tooltip = "\n".join(line for line in lines if line)
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

    def _action_rects(self, row_rect: QRect) -> list[tuple[str, QRect]]:
        """Кнопки плитки: «закрепить» и «меню». Видны под мышью, щелчок ловят всегда."""
        tile = self._tile_rect(row_rect)
        actions = list(self._ACTION_ICONS)
        total_width = len(actions) * self._ACTION_SIZE + (len(actions) - 1) * self._ACTION_SPACING
        x = tile.right() - 4 - total_width + 1
        y = tile.center().y() - (self._ACTION_SIZE // 2)

        rects: list[tuple[str, QRect]] = []
        for action in actions:
            rects.append((action, QRect(x, y, self._ACTION_SIZE, self._ACTION_SIZE)))
            x += self._ACTION_SIZE + self._ACTION_SPACING
        return rects

    def _action_at(self, row_rect: QRect, pos) -> Optional[str]:
        for action, rect in self._action_rects(row_rect):
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
            rect = option.rect.adjusted(self._TILE_GAP, 2, -self._TILE_GAP, -2)
            painter.setBrush(fill)
            painter.setPen(QPen(accent, 2))
            painter.drawRoundedRect(rect, 6, 6)
        elif marker.get("mode") in ("before", "after"):
            before = marker.get("mode") == "before"
            row_rect = self._tile_rect(option.rect)
            pen = QPen(accent, 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            if self._side_by_side():
                # В столбцах пресеты стоят рядом, место вставки — сбоку от строки.
                line_rect = row_rect.adjusted(0, 4, 0, -4)
                x = line_rect.left() - 1 if before else line_rect.right() + 2
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
        rect = self._tile_rect(option.rect)

        name = self._shown_name(index)
        is_active = bool(index.data(PresetListModel.ActiveRole))
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
        motion = active_row_motion(self._view)
        hover_motion = row_hover_motion(self._view)
        live_hover = hover_motion is not None and not focused
        hover_level = hover_motion.hover_level(index) if live_hover else None
        # Кнопки не шумят на всём списке: они проявляются только на плитке
        # под мышью или с клавиатурным фокусом — вместе с подсветкой плитки.
        if focused or pressed or (self._pending_destructive is not None and self._pending_destructive[0] == file_name):
            reveal = 1.0
        elif hover_level is not None:
            reveal = hover_level
        else:
            reveal = 1.0 if hovered else 0.0
        row_paint = paint_profile_hover_row(
            painter,
            rect,
            active=is_active and not drag_marker_visible,
            hovered=bool(hovered) or focused,
            pressed=pressed,
            show_active_marker=False,
            active_reveal=motion.row_reveal(index) if motion is not None else None,
            residual_active=motion.row_residual(index) if motion is not None else 0.0,
            hover_level=hover_level,
        )
        bg = row_paint.background

        icon_rect = QRect(
            rect.left() + self._TILE_PADDING,
            rect.center().y() - self._ICON_SIZE // 2 + 1,
            self._ICON_SIZE,
            self._ICON_SIZE,
        )
        if motion is not None:
            # После переезда бегунка значок нового активного пресета подпрыгивает.
            icon_rect = icon_rect.translated(0, round(motion.icon_offset(index)))
        if is_active:
            # Включённый пресет виден сразу: галочка вместо значка файла.
            icon_name, wanted_color = "fa5s.check-circle", str(tokens.accent_hex)
        else:
            icon_name = "fa5s.file-alt"
            wanted_color = normalize_preset_icon_color(str(index.data(PresetListModel.IconColorRole) or ""))
        icon_color = pick_contrast_color(wanted_color, bg, [tokens.accent_hex, tokens.fg], minimum_ratio=2.6)
        cached_icon(icon_name, icon_color).paint(painter, icon_rect)

        text_left = icon_rect.right() + self._ICON_GAP
        center_y = rect.center().y()
        actions = self._action_rects(option.rect)
        meta_font = painter.font()
        meta_font.setBold(False)
        meta_metrics = QFontMetrics(meta_font)

        # Справа от имени: под мышью — кнопки, иначе — постоянные пометки.
        # Имя обрезается один раз, по месту в покое, и при наведении не
        # «прыгает»: кнопки проявляются поверх, а хвост имени под ними тает.
        right_cursor = rect.right() - self._TILE_PADDING
        rating_rect = QRect()
        pin_mark_rect = QRect()
        if is_pinned:
            pin_mark_rect = QRect(
                right_cursor - self._MARK_SIZE + 1,
                center_y - self._MARK_SIZE // 2,
                self._MARK_SIZE,
                self._MARK_SIZE,
            )
            right_cursor = pin_mark_rect.left() - self._MARK_GAP
        if rating:
            rating_width = self._MARK_SIZE + self._RATING_STAR_GAP + meta_metrics.horizontalAdvance(str(rating))
            rating_rect = QRect(right_cursor - rating_width + 1, center_y - 9, rating_width, 18)
            right_cursor = rating_rect.left() - self._MARK_GAP
        actions_left = actions[0][1].left() - self._MARK_GAP

        name_rect = QRect(text_left, center_y - 10, max(0, right_cursor - text_left), 20)
        name_font = painter.font()
        name_font.setBold(is_active)
        painter.setFont(name_font)
        name_metrics = QFontMetrics(name_font)
        elided_name = name_metrics.elidedText(name, Qt.TextElideMode.ElideRight, name_rect.width())
        name_end = name_rect.left() + name_metrics.horizontalAdvance(elided_name)
        name_color = to_qcolor(tokens.fg, "#f5f5f5")
        # Край, за которым имя уже не видно: в покое — конец строки, при
        # наведении он плавно подъезжает к кнопкам.
        name_limit = name_rect.right() + (actions_left - name_rect.right()) * reveal
        if reveal > 0.0 and name_end > name_limit:
            clear = QColor(name_color)
            clear.setAlpha(0)
            fade = QLinearGradient(name_limit - self._NAME_FADE_WIDTH, 0.0, name_limit, 0.0)
            fade.setColorAt(0.0, name_color)
            fade.setColorAt(1.0, clear)
            painter.setPen(QPen(QBrush(fade), 1))
        else:
            painter.setPen(name_color)
        painter.drawText(name_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), elided_name)

        if bool(index.data(PresetListModel.RemoteRole)):
            remote_state = str(index.data(PresetListModel.RemoteStateRole) or "")
            cloud_left = name_rect.left() + name_metrics.horizontalAdvance(elided_name) + 6
            if cloud_left + 14 <= min(name_rect.right(), name_limit):
                cloud_color = tokens.fg_faint
                if remote_state in ("detached", "error"):
                    try:
                        from ui.theme_semantic import get_semantic_palette

                        cloud_color = get_semantic_palette(tokens.theme_name).warning_soft
                    except Exception:
                        cloud_color = "#ff9800"
                cloud_rect = QRect(cloud_left, name_rect.center().y() - 6, 13, 13)
                cached_icon("fa5s.cloud", cloud_color).paint(painter, cloud_rect)

        meta_font.setBold(False)
        painter.setFont(meta_font)
        # Пометки уступают место кнопкам и гаснут вдвое быстрее, чем те
        # проявляются: друг сквозь друга они не просвечивают.
        marks_opacity = max(0.0, 1.0 - reveal * 2.0)
        painter.setOpacity(marks_opacity)
        if rating_rect.isValid() and marks_opacity > 0.0:
            star_rect = QRect(
                rating_rect.left(),
                rating_rect.center().y() - self._MARK_SIZE // 2,
                self._MARK_SIZE,
                self._MARK_SIZE,
            )
            cached_icon("fa5s.star", self._RATING_STAR_COLOR).paint(painter, star_rect)
            painter.setPen(to_qcolor(tokens.fg_muted, "#b7bec8"))
            painter.drawText(
                rating_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                str(rating),
            )
        if pin_mark_rect.isValid() and marks_opacity > 0.0:
            self._paint_action_icon(painter, "fa5s.thumbtack", str(tokens.accent_hex), pin_mark_rect)

        painter.setOpacity(reveal)
        for action, action_rect in actions if reveal > 0.0 else ():
            btn_bg = to_qcolor(tokens.surface_bg_hover, tokens.surface_bg)
            if action == "pin" and is_pinned:
                icon_color = str(tokens.accent_hex)
            else:
                icon_color = pick_contrast_color(str(tokens.fg_muted), btn_bg, [tokens.fg], minimum_ratio=2.6)

            painter.setBrush(btn_bg)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(action_rect, 6, 6)

            rotation = self._pending_shake_rotation if self._pending_destructive == (file_name, action) else 0
            self._paint_action_icon(
                painter,
                self._ACTION_ICONS[action],
                icon_color,
                action_rect.adjusted(6, 6, -6, -6),
                rotation=rotation,
            )

        painter.restore()


def _is_preset_row(index) -> bool:
    return str(index.data(PresetListModel.KindRole) or "") == "preset"


__all__ = ["PresetListDelegate"]
