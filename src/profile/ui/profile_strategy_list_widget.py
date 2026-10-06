"""Виджеты списка готовых стратегий profile и их free-функции.

Вынесено из profile_setup_page.py (этап 4, фаза B, чанк M1) без изменения
поведения: классы реэкспортируются из profile.ui.profile_setup_page.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QEvent, QModelIndex, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QFont, QFontMetrics, QKeySequence, QPainter, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QHBoxLayout,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from log.log import log
from profile.strategy_families import strategy_count_text
from profile.strategy_grouping import (
    GROUPING_METHOD,
    STRATEGY_GROUPINGS,
    StrategyGroupingLayout,
    normalize_strategy_grouping,
    strategy_grouping_layout,
)
from profile.strategy_list_filter import (
    STRATEGY_SELECTED_TEXT,
    ProfileStrategyListFilterWorker,
    ProfileStrategyListGroup,
    ProfileStrategyListPlan,
    build_profile_strategy_list_plan,
    group_description_text,
)
from profile.strategy_shape import payload_badge_accessible_text
from profile.ui.widgets.payload_badge import (
    PAYLOAD_BADGE_HEIGHT,
    paint_payload_badge,
    payload_badge_width,
)
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    MenuAnimationType,
    SearchLineEdit,
    TransparentToolButton,
)
from ui.widgets.active_row_motion import active_row_motion, attach_active_row_motion
from ui.accessibility import (
    remove_line_edit_buttons_from_tab_order,
    set_control_accessibility,
    set_state_text,
)
from ui.fluent_widgets import set_tooltip
from ui.latest_value_worker_state import LatestValueWorkerState
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.theme import get_cached_qta_pixmap, get_theme_tokens, to_qcolor
from ui.widgets.fluent_item_tooltip import FluentItemToolTipController
from ui.widgets.fluent_scrollbar import install_fluent_scrollbars
from ui.widgets.folder_header import folder_header_font, folder_header_icon_color, folder_header_icon_name
from ui.widgets.hover_row import paint_profile_hover_row, profile_hover_row_rect
from ui.widgets.row_hover_motion import attach_row_hover_motion, paint_icon_motion, row_hover_motion


_ROW_KIND_GROUP = "group"
_ROW_KIND_SUBGROUP = "subgroup"
_STRATEGY_ROW_HEIGHT = 31
# На широком списке стратегии стоят плитками в несколько столбцов: название и
# под ним уточнение. В узком окне остаётся один столбец обычных строк.
_TILE_HEIGHT = 46
_TILE_MIN_WIDTH = 300
_MAX_TILE_COLUMNS = 4
_SUBGROUP_ROW_HEIGHT = 26
# В коротком списке сворачивать нечего: все группы раскрыты сразу. В длинном
# раскрыта одна группа: открыл другую — прежняя закрылась сама.
_AUTO_COLLAPSE_MIN_ROWS = 30
_KEYBOARD_PAGE_ROWS = 10
_NAVIGATION_KEYS = (
    Qt.Key.Key_Down,
    Qt.Key.Key_Up,
    Qt.Key.Key_Home,
    Qt.Key.Key_End,
    Qt.Key.Key_PageDown,
    Qt.Key.Key_PageUp,
)
_MARK_SIZE = 11
_MARK_GAP = 5
_MARK_FAVORITE = ("fa5s.star", "#d9a441")
_MARK_WORKS = ("fa5s.check", "#49a35f")
_MARK_NOT_WORKS = ("fa5s.times", "#d85c5c")


def _strategy_marks_tooltip(*, is_active: bool, favorite: bool, rating: str) -> str:
    lines = []
    if is_active:
        lines.append("Эта стратегия выбрана для профиля.")
    if favorite:
        lines.append("Звезда: стратегия у вас в избранном.")
    if rating == "work":
        lines.append("Галочка: вы отметили, что эта стратегия работает.")
    elif rating == "notwork":
        lines.append("Крестик: вы отметили, что эта стратегия не работает.")
    return "\n".join(lines)


def _is_group_item(item) -> bool:
    """Строка списка — заголовок группы стратегий, а не сама стратегия."""
    if item is None:
        return False
    try:
        return str(item.data(ProfileStrategyListWidget._ROLE_ROW_KIND) or "") == _ROW_KIND_GROUP
    except Exception:
        return False


def _is_subgroup_item(item) -> bool:
    """Строка списка — подзаголовок внутри группы: подпись, не стратегия."""
    if item is None:
        return False
    try:
        return str(item.data(ProfileStrategyListWidget._ROLE_ROW_KIND) or "") == _ROW_KIND_SUBGROUP
    except Exception:
        return False


def _row_skipped(list_widget, row: int) -> bool:
    """Клавиатура не останавливается на строке: она скрыта или это подзаголовок."""
    if _row_hidden(list_widget, row):
        return True
    getter = getattr(list_widget, "item", None)
    if not callable(getter):
        return False
    try:
        return _is_subgroup_item(getter(row))
    except Exception:
        return False


def _column_count(list_widget) -> int:
    counter = getattr(list_widget, "column_count", None)
    if not callable(counter):
        return 1
    try:
        return max(1, int(counter()))
    except Exception:
        return 1


def _row_hidden(list_widget, row: int) -> bool:
    checker = getattr(list_widget, "isRowHidden", None)
    if not callable(checker):
        return False
    try:
        return bool(checker(row))
    except Exception:
        return False


def _visible_row_from(list_widget, row: int, step: int, count: int) -> int:
    """Ближайшая строка для клавиатуры начиная с row в сторону step; -1, если таких нет."""
    while 0 <= row < count:
        if not _row_skipped(list_widget, row):
            return row
        row += step
    return -1


def _keyboard_target_row(list_widget, key: int, *, count: int, current_row: int) -> int:
    """Куда клавиша навигации переводит текущую строку.

    Строки свёрнутых групп скрыты, поэтому счёт идёт только по видимым. Когда
    стратегии стоят плитками в несколько столбцов, стрелки вверх и вниз
    переходят на соседний ряд, а не на соседнюю плитку.
    """
    first = _visible_row_from(list_widget, 0, 1, count)
    last = _visible_row_from(list_widget, count - 1, -1, count)
    if first < 0:
        return -1
    key = int(key)
    if key == int(Qt.Key.Key_Home) or current_row < 0:
        return first
    if key == int(Qt.Key.Key_End):
        return last
    step = 1 if key in (int(Qt.Key.Key_Down), int(Qt.Key.Key_PageDown)) else -1
    moves = _KEYBOARD_PAGE_ROWS if key in (int(Qt.Key.Key_PageDown), int(Qt.Key.Key_PageUp)) else 1
    by_lines = _column_count(list_widget) > 1
    row = current_row
    for _ in range(moves):
        if by_lines:
            next_row = int(list_widget.row_in_next_line(row, step))
        else:
            next_row = _visible_row_from(list_widget, row + step, step, count)
        if next_row < 0:
            break
        row = next_row
    if _row_skipped(list_widget, row):
        return first
    return row


def _set_widget_text_if_changed(widget, text: str) -> bool:
    """Ленивый мост к profile.ui.profile_setup_page.set_widget_text_if_changed.

    Widget-state сеттеры остаются в модуле страницы (патч-цель тестов по пути
    profile.ui.profile_setup_page.*); ленивый импорт разрывает циклический
    импорт страницы и этого модуля и сохраняет действие патчей."""
    from profile.ui import profile_setup_page

    return profile_setup_page.set_widget_text_if_changed(widget, text)


class ProfileStrategyListDelegate(QStyledItemDelegate):
    """Рисует готовые стратегии как единый текстовый список."""

    def __init__(self, view: QListWidget):
        super().__init__(view)
        self._tooltip = FluentItemToolTipController(view.viewport())
        attach_row_hover_motion(view)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        # Под приклеенным заголовком группы строки не рисуются: фон списка
        # полупрозрачный, закрасить их сверху нечем.
        clip_top = int(getattr(self.parent(), "rows_clip_top", 0) or 0)
        if clip_top > option.rect.top():
            painter.save()
            painter.setClipRect(
                QRect(option.rect.left(), clip_top, option.rect.width(), max(0, option.rect.bottom() + 1 - clip_top)),
                Qt.ClipOperation.IntersectClip,
            )
            self._paint_row(painter, option, index)
            painter.restore()
            return
        self._paint_row(painter, option, index)

    def _paint_row(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        kind = str(index.data(ProfileStrategyListWidget._ROLE_ROW_KIND) or "")
        if kind == _ROW_KIND_GROUP:
            self._paint_group_header(painter, option, index)
            return
        if kind == _ROW_KIND_SUBGROUP:
            self._paint_subgroup_header(painter, option, index)
            return
        if _column_count(self.parent()) > 1:
            self._paint_tile(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        tokens = get_theme_tokens()
        rect = profile_hover_row_rect(option.rect)
        is_active = bool(index.data(ProfileStrategyListWidget._ROLE_IS_ACTIVE))
        rating = str(index.data(ProfileStrategyListWidget._ROLE_RATING) or "")
        favorite = bool(index.data(ProfileStrategyListWidget._ROLE_FAVORITE))
        # Стратегия с отметкой «не работает» отступает на второй план.
        dimmed = rating == "notwork" and not is_active
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected) or bool(
            option.state & QStyle.StateFlag.State_HasFocus
        )

        motion = active_row_motion(self.parent())
        hover_motion = row_hover_motion(self.parent())
        # Выделенная строка подсвечена сразу, плавно проявляется только наведение мышью.
        live_hover = hover_motion is not None and not selected
        paint_profile_hover_row(
            painter,
            rect,
            active=is_active,
            hovered=hovered,
            selected=selected,
            show_active_marker=not (motion is not None and motion.hides_static_marker(index)),
            active_reveal=motion.row_reveal(index) if motion is not None else None,
            residual_active=motion.row_residual(index) if motion is not None else 0.0,
            hover_level=hover_motion.hover_level(index) if live_hover else None,
            sheen=hover_motion.sheen_progress(index) if live_hover else None,
        )
        icon_dy = round(motion.icon_offset(index)) if motion is not None else 0

        left = rect.left() + (24 if is_active else 18)
        right = rect.right() - 16
        # Справа словами пишется только «Выбрана»; оценка и избранное — значками.
        status = STRATEGY_SELECTED_TEXT if is_active else ""
        icon_name = str(index.data(ProfileStrategyListWidget._ROLE_VISUAL_ICON_NAME) or "")
        visual_color = str(index.data(ProfileStrategyListWidget._ROLE_VISUAL_COLOR) or "")
        visual_label = str(index.data(ProfileStrategyListWidget._ROLE_VISUAL_LABEL_TEXT) or "")
        status_rect = QRect()

        font = painter.font()
        font.setBold(False)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        if status:
            status_width = min(metrics.horizontalAdvance(status) + 18, max(0, rect.width() // 2))
            status_rect = QRect(right - status_width, rect.center().y() - 10, status_width, 20)
            right = status_rect.left() - 12

        icon_size = 14
        if icon_name:
            icon_rect = QRect(left, rect.center().y() - icon_size // 2 + icon_dy, icon_size, icon_size)
            # Пока значок наклоняется, он рисуется из картинки двойного размера — края чёткие.
            moving = hover_motion is not None and hover_motion.icon_moving(index)
            pixmap = get_cached_qta_pixmap(
                icon_name,
                color=visual_color or tokens.fg_faint,
                size=icon_size * (2 if moving else 1),
            )
            if not pixmap.isNull():
                if dimmed:
                    painter.setOpacity(0.45)
                paint_icon_motion(painter, icon_rect, hover_motion, index, lambda: painter.drawPixmap(icon_rect, pixmap))
                painter.setOpacity(1.0)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(to_qcolor(visual_color or tokens.fg_faint, "#aeb5c1"))
                painter.drawEllipse(icon_rect)
            left = icon_rect.right() + 10

        visual_rect = QRect()
        if visual_label:
            visual_width = min(metrics.horizontalAdvance(visual_label) + 4, max(0, rect.width() // 3))
            visual_rect = QRect(max(left, right - visual_width), rect.top(), visual_width, rect.height())
            right = visual_rect.left() - 12

        # Отметки пользователя стоят перед названием способа: звезда избранного,
        # затем галочка «работает» или крестик «не работает».
        marks = []
        if favorite:
            marks.append(_MARK_FAVORITE)
        if rating == "work":
            marks.append(_MARK_WORKS)
        elif rating == "notwork":
            marks.append(_MARK_NOT_WORKS)
        mark_rects = []
        if marks:
            mark_right = right + (12 - _MARK_GAP if visual_label else 0)
            for mark in reversed(marks):
                mark_rect = QRect(mark_right - _MARK_SIZE, rect.center().y() - _MARK_SIZE // 2, _MARK_SIZE, _MARK_SIZE)
                if mark_rect.left() - left < 120:
                    break
                mark_rects.append((mark, mark_rect))
                mark_right = mark_rect.left() - _MARK_GAP
            if mark_rects:
                right = mark_right - 6

        payload_badge = str(index.data(ProfileStrategyListWidget._ROLE_PAYLOAD_BADGE_TEXT) or "")
        payload_badge_rect = QRect()
        badge_width = payload_badge_width(metrics, payload_badge)
        if badge_width and right - left >= badge_width + 10 + 120:
            payload_badge_rect = QRect(
                right - badge_width,
                rect.center().y() - PAYLOAD_BADGE_HEIGHT // 2,
                badge_width,
                PAYLOAD_BADGE_HEIGHT,
            )
            right = payload_badge_rect.left() - 10

        name = str(index.data(ProfileStrategyListWidget._ROLE_NAME_TEXT) or "")
        name_rect = QRect(left, rect.top(), max(0, right - left), rect.height())
        painter.setPen(to_qcolor(tokens.fg_muted if dimmed else tokens.fg, "#f5f5f5"))
        painter.drawText(
            name_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrics.elidedText(name, Qt.TextElideMode.ElideRight, name_rect.width()),
        )

        paint_payload_badge(painter, payload_badge_rect, payload_badge, metrics, tokens)

        for (mark_icon, mark_color), mark_rect in mark_rects:
            mark_pixmap = get_cached_qta_pixmap(mark_icon, color=mark_color, size=_MARK_SIZE)
            if not mark_pixmap.isNull():
                painter.drawPixmap(mark_rect, mark_pixmap)

        if visual_rect.width() > 0:
            # Цвет способа остаётся только у значка слева: название справа
            # спокойное, чтобы взгляд читал имена стратегий.
            painter.setPen(to_qcolor(tokens.fg_faint if dimmed else tokens.fg_muted, "#aeb5c1"))
            painter.drawText(
                visual_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                metrics.elidedText(visual_label, Qt.TextElideMode.ElideRight, visual_rect.width()),
            )

        if status_rect.width() > 0:
            if is_active:
                badge_bg = to_qcolor(tokens.accent_soft_bg_hover, tokens.accent_hex)
                badge_fg = to_qcolor(tokens.accent_hex, "#5caee8")
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(badge_bg)
                painter.drawRoundedRect(status_rect, 9, 9)
                painter.setPen(badge_fg)
            else:
                painter.setPen(to_qcolor(tokens.fg_faint, "#aeb5c1"))
            painter.drawText(
                status_rect,
                int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter),
                metrics.elidedText(status, Qt.TextElideMode.ElideRight, max(0, status_rect.width() - 10)),
            )

        painter.restore()

    def _paint_tile(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Плитка стратегии: название и под ним уточнение со способом обхода."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        tokens = get_theme_tokens()
        rect = self.parent().item_paint_rect(option.rect)
        is_active = bool(index.data(ProfileStrategyListWidget._ROLE_IS_ACTIVE))
        rating = str(index.data(ProfileStrategyListWidget._ROLE_RATING) or "")
        favorite = bool(index.data(ProfileStrategyListWidget._ROLE_FAVORITE))
        dimmed = rating == "notwork" and not is_active
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected) or bool(
            option.state & QStyle.StateFlag.State_HasFocus
        )

        motion = active_row_motion(self.parent())
        hover_motion = row_hover_motion(self.parent())
        live_hover = hover_motion is not None and not selected
        paint_profile_hover_row(
            painter,
            rect,
            active=is_active,
            hovered=hovered,
            selected=selected,
            show_active_marker=not (motion is not None and motion.hides_static_marker(index)),
            active_reveal=motion.row_reveal(index) if motion is not None else None,
            residual_active=motion.row_residual(index) if motion is not None else 0.0,
            hover_level=hover_motion.hover_level(index) if live_hover else None,
            sheen=hover_motion.sheen_progress(index) if live_hover else None,
        )
        icon_dy = round(motion.icon_offset(index)) if motion is not None else 0

        # Текст не сдвигается, когда плитку выбирают: место под акцентную
        # полоску оставлено у всех плиток.
        left = rect.left() + 18
        right = rect.right() - 12
        center_y = rect.center().y()

        font = painter.font()
        font.setBold(False)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        icon_name = str(index.data(ProfileStrategyListWidget._ROLE_VISUAL_ICON_NAME) or "")
        visual_color = str(index.data(ProfileStrategyListWidget._ROLE_VISUAL_COLOR) or "")
        icon_size = 14
        if icon_name:
            icon_rect = QRect(left, center_y - icon_size // 2 + icon_dy, icon_size, icon_size)
            moving = hover_motion is not None and hover_motion.icon_moving(index)
            pixmap = get_cached_qta_pixmap(
                icon_name,
                color=visual_color or tokens.fg_faint,
                size=icon_size * (2 if moving else 1),
            )
            if not pixmap.isNull():
                if dimmed:
                    painter.setOpacity(0.45)
                paint_icon_motion(painter, icon_rect, hover_motion, index, lambda: painter.drawPixmap(icon_rect, pixmap))
                painter.setOpacity(1.0)
            left = icon_rect.right() + 10

        # Справа по порядку от края: плашка «Выбрана», отметки пользователя,
        # типы пакетов составной стратегии. Что не помещается — не рисуется.
        status_rect = QRect()
        if is_active:
            status_width = metrics.horizontalAdvance(STRATEGY_SELECTED_TEXT) + 18
            if right - status_width - left >= 110:
                status_rect = QRect(right - status_width, center_y - 10, status_width, 20)
                right = status_rect.left() - 8

        marks = []
        if favorite:
            marks.append(_MARK_FAVORITE)
        if rating == "work":
            marks.append(_MARK_WORKS)
        elif rating == "notwork":
            marks.append(_MARK_NOT_WORKS)
        mark_rects = []
        mark_right = right
        for mark in reversed(marks):
            mark_rect = QRect(mark_right - _MARK_SIZE, center_y - _MARK_SIZE // 2, _MARK_SIZE, _MARK_SIZE)
            if mark_rect.left() - left < 110:
                break
            mark_rects.append((mark, mark_rect))
            mark_right = mark_rect.left() - _MARK_GAP
        if mark_rects:
            right = mark_right - 3

        payload_badge = str(index.data(ProfileStrategyListWidget._ROLE_PAYLOAD_BADGE_TEXT) or "")
        payload_badge_rect = QRect()
        badge_width = payload_badge_width(metrics, payload_badge)
        if badge_width and right - left >= badge_width + 8 + 110:
            payload_badge_rect = QRect(
                right - badge_width,
                center_y - PAYLOAD_BADGE_HEIGHT // 2,
                badge_width,
                PAYLOAD_BADGE_HEIGHT,
            )
            right = payload_badge_rect.left() - 8

        title = str(
            index.data(ProfileStrategyListWidget._ROLE_TITLE_TEXT)
            or index.data(ProfileStrategyListWidget._ROLE_NAME_TEXT)
            or ""
        )
        visual_label = str(index.data(ProfileStrategyListWidget._ROLE_VISUAL_LABEL_TEXT) or "")
        if title.lower().startswith(visual_label.lower()):
            # «MultiSplit seqovl 226» уже называет свой способ обхода.
            visual_label = ""
        # Способ обхода стоит после уточнения: первым взгляд читает, откуда стратегия.
        detail = " · ".join(
            part
            for part in (str(index.data(ProfileStrategyListWidget._ROLE_DETAIL_TEXT) or ""), visual_label)
            if part
        )
        text_width = max(0, right - left)
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title_rect = QRect(left, rect.top() + 4, text_width, 18) if detail else QRect(left, rect.top(), text_width, rect.height())
        painter.setPen(to_qcolor(tokens.fg_muted if dimmed else tokens.fg, "#f5f5f5"))
        painter.drawText(title_rect, flags, metrics.elidedText(title, Qt.TextElideMode.ElideRight, text_width))
        if detail:
            detail_font = _smaller_font(font)
            painter.setFont(detail_font)
            painter.setPen(to_qcolor(tokens.fg_faint if dimmed else tokens.fg_muted, "#aeb5c1"))
            painter.drawText(
                QRect(left, rect.top() + 21, text_width, 15),
                flags,
                QFontMetrics(detail_font).elidedText(detail, Qt.TextElideMode.ElideRight, text_width),
            )
            painter.setFont(font)

        paint_payload_badge(painter, payload_badge_rect, payload_badge, metrics, tokens)

        for (mark_icon, mark_color), mark_rect in mark_rects:
            mark_pixmap = get_cached_qta_pixmap(mark_icon, color=mark_color, size=_MARK_SIZE)
            if not mark_pixmap.isNull():
                painter.drawPixmap(mark_rect, mark_pixmap)

        if status_rect.width() > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(to_qcolor(tokens.accent_soft_bg_hover, tokens.accent_hex))
            painter.drawRoundedRect(status_rect, 9, 9)
            painter.setPen(to_qcolor(tokens.accent_hex, "#5caee8"))
            painter.drawText(
                status_rect,
                int(Qt.AlignmentFlag.AlignCenter),
                metrics.elidedText(STRATEGY_SELECTED_TEXT, Qt.TextElideMode.ElideRight, status_rect.width() - 10),
            )

        painter.restore()

    def _paint_subgroup_header(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Подзаголовок внутри группы: название, число стратегий и тонкая черта до края."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        tokens = get_theme_tokens()
        rect = profile_hover_row_rect(option.rect)
        left = rect.left() + 18
        right = rect.right() - 16
        # Подпись прижата к плиткам под ней, а не к строкам сверху.
        text_rect = QRect(left, rect.top() + 3, max(0, right - left), rect.height() - 3)
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        font = _smaller_font(painter.font())
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        title = metrics.elidedText(
            str(index.data(ProfileStrategyListWidget._ROLE_NAME_TEXT) or ""),
            Qt.TextElideMode.ElideRight,
            text_rect.width(),
        )
        painter.setPen(to_qcolor(tokens.fg_muted, "#b7bec8"))
        painter.drawText(text_rect, flags, title)
        left += metrics.horizontalAdvance(title) + 7

        font.setBold(False)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        count_text = str(int(index.data(ProfileStrategyListWidget._ROLE_GROUP_COUNT) or 0))
        if right - left > metrics.horizontalAdvance(count_text):
            painter.setPen(to_qcolor(tokens.fg_faint, "#aeb5c1"))
            painter.drawText(QRect(left, text_rect.top(), right - left, text_rect.height()), flags, count_text)
            left += metrics.horizontalAdvance(count_text) + 10

        if right - left > 24:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(to_qcolor(tokens.divider_strong, "#33ffffff"))
            painter.drawRect(QRect(left, text_rect.center().y(), right - left, 1))
        painter.restore()

    def _paint_group_header(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Заголовок группы: стрелка, значок способа, название, число стратегий и объяснение."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        tokens = get_theme_tokens()
        rect = profile_hover_row_rect(option.rect)
        if bool(option.state & QStyle.StateFlag.State_MouseOver) or bool(
            option.state & QStyle.StateFlag.State_HasFocus
        ):
            paint_profile_hover_row(painter, rect, hovered=True, fill_idle=False, show_active_marker=False)

        center_y = rect.center().y()
        left = rect.left() + 18
        right = rect.right() - 16
        expanded = bool(index.data(ProfileStrategyListWidget._ROLE_GROUP_EXPANDED))
        current_name = str(index.data(ProfileStrategyListWidget._ROLE_GROUP_CURRENT_NAME) or "")
        if current_name:
            # В этой группе стоит выбранная стратегия: та же акцентная полоска,
            # что у выбранной строки, видна и когда группа свёрнута.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(to_qcolor(tokens.accent_hex, "#5caee8"))
            painter.drawRoundedRect(QRect(rect.left() + 6, rect.top() + 6, 4, max(12, rect.height() - 12)), 2, 2)
            if not expanded:
                # Раскрытая группа показывает выбранную строку сама.
                right = self._paint_group_current_strategy(painter, rect, right, current_name, tokens)
        chevron = get_cached_qta_pixmap(
            folder_header_icon_name(expanded),
            color=folder_header_icon_color(),
            size=10,
        )
        if not chevron.isNull():
            painter.drawPixmap(QRect(left, center_y - 5, 10, 10), chevron)
        left += 10 + 8

        icon_name = str(index.data(ProfileStrategyListWidget._ROLE_VISUAL_ICON_NAME) or "")
        if icon_name:
            icon = get_cached_qta_pixmap(
                icon_name,
                color=str(index.data(ProfileStrategyListWidget._ROLE_VISUAL_COLOR) or "") or tokens.fg_faint,
                size=14,
            )
            if not icon.isNull():
                painter.drawPixmap(QRect(left, center_y - 7, 14, 14), icon)
            left += 14 + 8

        body_font = painter.font()
        body_font.setBold(False)
        title_font = folder_header_font(body_font)
        title_metrics = QFontMetrics(title_font)
        title = title_metrics.elidedText(
            str(index.data(ProfileStrategyListWidget._ROLE_NAME_TEXT) or ""),
            Qt.TextElideMode.ElideRight,
            max(0, right - left),
        )
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        painter.setFont(title_font)
        painter.setPen(to_qcolor(tokens.fg, "#f5f5f5"))
        painter.drawText(QRect(left, rect.top(), max(0, right - left), rect.height()), flags, title)
        left += title_metrics.horizontalAdvance(title) + 8

        painter.setFont(body_font)
        metrics = QFontMetrics(body_font)
        count_text = str(int(index.data(ProfileStrategyListWidget._ROLE_GROUP_COUNT) or 0))
        if right - left > metrics.horizontalAdvance(count_text):
            painter.setPen(to_qcolor(tokens.fg_muted, "#b7bec8"))
            painter.drawText(QRect(left, rect.top(), right - left, rect.height()), flags, count_text)
            left += metrics.horizontalAdvance(count_text) + 12

        description = str(index.data(ProfileStrategyListWidget._ROLE_VISUAL_DESCRIPTION) or "")
        if description and right - left > 80:
            painter.setPen(to_qcolor(tokens.fg_faint, "#aeb5c1"))
            painter.drawText(
                QRect(left, rect.top(), right - left, rect.height()),
                flags,
                metrics.elidedText(description, Qt.TextElideMode.ElideRight, right - left),
            )
        painter.restore()

    def _paint_group_current_strategy(self, painter: QPainter, rect: QRect, right: int, name: str, tokens) -> int:
        """Справа в заголовке: название выбранной стратегии и плашка «Выбрана».

        Возвращает новую правую границу для текста заголовка.
        """
        font = painter.font()
        font.setBold(False)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        badge_width = metrics.horizontalAdvance(STRATEGY_SELECTED_TEXT) + 18
        badge_rect = QRect(right - badge_width, rect.center().y() - 10, badge_width, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(to_qcolor(tokens.accent_soft_bg_hover, tokens.accent_hex))
        painter.drawRoundedRect(badge_rect, 9, 9)
        painter.setPen(to_qcolor(tokens.accent_hex, "#5caee8"))
        painter.drawText(badge_rect, int(Qt.AlignmentFlag.AlignCenter), STRATEGY_SELECTED_TEXT)
        right = badge_rect.left() - 10

        # Название занимает не больше 45% строки: слева остаётся место заголовку.
        name_width = min(metrics.horizontalAdvance(name), int(rect.width() * 0.45))
        if name_width >= 60:
            name_rect = QRect(right - name_width, rect.top(), name_width, rect.height())
            painter.setPen(to_qcolor(tokens.fg, "#f5f5f5"))
            painter.drawText(
                name_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                metrics.elidedText(name, Qt.TextElideMode.ElideRight, name_width),
            )
            right = name_rect.left() - 16
        return right

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        _ = option
        sizer = getattr(self.parent(), "item_size", None)
        if callable(sizer):
            return sizer(str(index.data(ProfileStrategyListWidget._ROLE_ROW_KIND) or ""))
        return QSize(0, _STRATEGY_ROW_HEIGHT)

    def helpEvent(self, event, view, option, index: QModelIndex) -> bool:  # noqa: N802
        _ = (view, option)
        text = str(index.data(ProfileStrategyListWidget._ROLE_TOOLTIP_TEXT) or "").strip()
        if str(index.data(ProfileStrategyListWidget._ROLE_ROW_KIND) or "") != _ROW_KIND_GROUP:
            # Значки отметок в строке без слов — подсказка их расшифровывает.
            marks = _strategy_marks_tooltip(
                is_active=bool(index.data(ProfileStrategyListWidget._ROLE_IS_ACTIVE)),
                favorite=bool(index.data(ProfileStrategyListWidget._ROLE_FAVORITE)),
                rating=str(index.data(ProfileStrategyListWidget._ROLE_RATING) or ""),
            )
            text = "\n\n".join(part for part in (marks, text) if part)
        if not text:
            self._tooltip.hide()
            return True
        self._tooltip.show_text(text, event.globalPos())
        return True


def _smaller_font(font: QFont) -> QFont:
    """Шрифт на ступень мельче основного: вторая строка плитки, подзаголовок."""
    smaller = QFont(font)
    if font.pixelSize() > 0:
        smaller.setPixelSize(max(9, font.pixelSize() - 1))
    elif font.pointSizeF() > 0:
        smaller.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
    return smaller


class CompactDisplayComboBox(ComboBox):
    """ComboBox с подробным меню и коротким выбранным значением."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._compact_text_by_data: dict[str, str] = {}

    def addItem(self, text: str, icon=None, userData=None, compactText: str | None = None):  # noqa: N802
        super().addItem(text, icon=icon, userData=userData)
        if compactText is not None:
            self._compact_text_by_data[str(userData)] = str(compactText)
            self._sync_compact_text()

    def setCurrentIndex(self, index: int):  # noqa: N802
        super().setCurrentIndex(index)
        self._sync_compact_text()

    def setItemAccessibleText(self, index: int, text: str) -> None:  # noqa: N802
        if 0 <= int(index) < len(self.items):
            setattr(self.items[int(index)], "accessibleText", str(text or "").strip())

    def _create_accessible_combo_menu(self):
        menu = self._createComboMenu()
        for index, item in enumerate(self.items):
            action = QAction(item.icon, item.text, triggered=lambda _checked=False, row=index: self._onItemClicked(row))
            action.setEnabled(item.isEnabled)
            menu.addAction(action)
            accessible_text = str(getattr(item, "accessibleText", "") or "").strip()
            menu_item = action.property("item")
            if accessible_text and menu_item is not None:
                menu_item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text)
                menu_item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, accessible_text)
        return menu

    def _showComboMenu(self) -> None:
        if not self.items:
            return

        menu = self._create_accessible_combo_menu()
        if menu.view.width() < self.width():
            menu.view.setMinimumWidth(self.width())
            menu.adjustSize()

        menu.setMaxVisibleItems(self.maxVisibleItems())
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.closedSignal.connect(self._onDropMenuClosed)
        self.dropMenu = menu

        if self.currentIndex() >= 0 and self.items:
            menu.setDefaultAction(menu.actions()[self.currentIndex()])

        x = -menu.width() // 2 + menu.layout().contentsMargins().left() + self.width() // 2
        down_pos = self.mapToGlobal(QPoint(x, self.height()))
        down_height = menu.view.heightForAnimation(down_pos, MenuAnimationType.DROP_DOWN)

        up_pos = self.mapToGlobal(QPoint(x, 0))
        up_height = menu.view.heightForAnimation(up_pos, MenuAnimationType.PULL_UP)

        if down_height >= up_height:
            menu.view.adjustSize(down_pos, MenuAnimationType.DROP_DOWN)
            menu.exec(down_pos, aniType=MenuAnimationType.DROP_DOWN)
        else:
            menu.view.adjustSize(up_pos, MenuAnimationType.PULL_UP)
            menu.exec(up_pos, aniType=MenuAnimationType.PULL_UP)

    def _sync_compact_text(self) -> None:
        index = self.currentIndex()
        if index < 0:
            return
        data = str(self.itemData(index))
        compact = self._compact_text_by_data.get(data)
        if compact:
            _set_widget_text_if_changed(self, compact)


class ProfileStrategyListView(QListWidget):
    """Список стратегий, который выбирается клавиатурой так же, как DNS."""

    # Заголовок группы нажали мышью или клавишей: её нужно свернуть или развернуть.
    group_toggle_requested = pyqtSignal(object)

    # Ниже какой высоты делегат рисует строки; не 0 только пока рисуется список
    # с приклеенным заголовком группы.
    rows_clip_top = 0
    _pinned_hovered = False
    _pinned_pressed = False

    # ------------------------------------------------------------------
    # Плитки в несколько столбцов
    # ------------------------------------------------------------------

    def set_vertical_padding(self, padding: int) -> None:
        """Отступ строк от верхнего и нижнего края списка.

        Задаётся полями области строк, а не padding в стиле: тот Qt считает
        рамкой со всех сторон и из-за него недодаёт ширины ряду плиток.
        """
        self.setViewportMargins(0, int(padding), 0, int(padding))

    def _layout_width(self) -> int:
        """Ширина, в которую Qt раскладывает ряд плиток (QListView, перенос строк)."""
        width = min(self.viewport().width(), self.maximumViewportSize().width())
        if self.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
            # Под свою полосу прокрутки Qt оставляет место, даже пока её нет.
            width -= self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        # Плитка, вставшая вплотную к краю, переносится на следующий ряд.
        return max(0, width - 1)

    def column_count(self) -> int:
        """Сколько плиток стратегий помещается в ряд при нынешней ширине списка."""
        return max(1, min(_MAX_TILE_COLUMNS, self._layout_width() // _TILE_MIN_WIDTH))

    def item_size(self, kind: str) -> QSize:
        """Размер строки списка: заголовки идут на всю ширину, плитки делят её."""
        width = self.viewport().width()
        if kind == _ROW_KIND_GROUP:
            return QSize(width, _STRATEGY_ROW_HEIGHT)
        if kind == _ROW_KIND_SUBGROUP:
            return QSize(width, _SUBGROUP_ROW_HEIGHT)
        columns = self.column_count()
        if columns <= 1:
            return QSize(width, _STRATEGY_ROW_HEIGHT)
        return QSize(self._layout_width() // columns, _TILE_HEIGHT)

    def item_paint_rect(self, rect: QRect) -> QRect:
        """Где в ячейке списка рисуется подложка строки или плитки.

        У крайних плиток отступ от края списка такой же, как у заголовков
        групп, а между соседними плитками — вдвое меньше с каждой стороны.
        """
        columns = self.column_count()
        if columns <= 1 or rect.width() <= 0 or rect.width() >= self.viewport().width():
            return profile_hover_row_rect(rect)
        column = round(rect.left() / rect.width())
        return rect.adjusted(8 if column <= 0 else 4, 3, -8 if column >= columns - 1 else -4, -3)

    def row_in_next_line(self, row: int, step: int) -> int:
        """Строка в соседнем ряду, ближайшая по горизонтали; -1 — ряда нет."""
        item = self.item(int(row))
        if item is None:
            return -1
        origin = self.visualItemRect(item)
        count = self.count()
        line_top = None
        best = -1
        best_distance = 0
        candidate = int(row) + step
        while 0 <= candidate < count:
            if not _row_skipped(self, candidate):
                rect = self.visualItemRect(self.item(candidate))
                if rect.top() != origin.top():
                    if line_top is None:
                        line_top = rect.top()
                    elif rect.top() != line_top:
                        break
                    distance = abs(rect.left() - origin.left())
                    if best < 0 or distance < best_distance:
                        best, best_distance = candidate, distance
            candidate += step
        return best

    def _move_along_line_from_keyboard(self, key: int) -> bool:
        """Стрелки влево и вправо переходят по плиткам, когда столбцов несколько."""
        if key not in (Qt.Key.Key_Left, Qt.Key.Key_Right) or self.column_count() <= 1:
            return False
        item = self.currentItem()
        if item is None or _is_group_item(item):
            return False
        step = 1 if key == Qt.Key.Key_Right else -1
        row = _visible_row_from(self, self.row(item) + step, step, self.count())
        if row >= 0:
            self.setCurrentRow(row)
            self.scrollToItem(self.currentItem())
        return True

    # ------------------------------------------------------------------
    # Приклеенный заголовок раскрытой группы
    # ------------------------------------------------------------------

    def group_header_for_row(self, row: int):
        """Заголовок группы, в которой стоит строка; None — список без групп."""
        for candidate in range(int(row), -1, -1):
            item = self.item(candidate)
            if _is_group_item(item):
                return item
        return None

    def pinned_group_header(self):
        """Заголовок раскрытой группы, уехавший за верх списка.

        Пока видны строки группы, её заголовок остаётся у верхнего края:
        свернуть группу можно, не прокручивая список обратно. Возвращает
        (строка заголовка, её место на экране) или None.
        """
        viewport = self.viewport()
        # В первом столбце плитка есть в каждом ряду, в середине ряда её может не быть.
        top_item = self.itemAt(QPoint(2, 0)) or self.itemAt(QPoint(max(0, viewport.width() // 2), 0))
        if top_item is None:
            return None
        top_row = self.row(top_item)
        header = self.group_header_for_row(top_row)
        if header is None or not bool(header.data(ProfileStrategyListWidget._ROLE_GROUP_EXPANDED)):
            return None
        if self.visualItemRect(header).top() >= 0:
            return None
        top = 0
        # Следующий заголовок выталкивает приклеенный вверх.
        for row in range(top_row + 1, self.count()):
            item = self.item(row)
            if not _is_group_item(item):
                continue
            if not _row_hidden(self, row):
                top = min(0, self.visualItemRect(item).top() - _STRATEGY_ROW_HEIGHT)
            break
        return header, QRect(0, top, viewport.width(), _STRATEGY_ROW_HEIGHT)

    def _pinned_header_at(self, point: QPoint):
        pinned = self.pinned_group_header()
        if pinned is not None and pinned[1].contains(point):
            return pinned[0]
        return None

    def _set_pinned_hovered(self, hovered: bool) -> None:
        if bool(hovered) == bool(self._pinned_hovered):
            return
        self._pinned_hovered = bool(hovered)
        self.viewport().update()

    def paintEvent(self, event):  # noqa: N802
        pinned = self.pinned_group_header()
        self.rows_clip_top = pinned[1].bottom() + 1 if pinned is not None else 0
        try:
            super().paintEvent(event)
        finally:
            self.rows_clip_top = 0
        if pinned is None:
            return
        header, rect = pinned
        option = QStyleOptionViewItem()
        self.initViewItemOption(option)
        option.rect = rect
        option.state &= ~(QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_HasFocus)
        if self._pinned_hovered:
            option.state |= QStyle.StateFlag.State_MouseOver
        painter = QPainter(self.viewport())
        try:
            self.itemDelegate().paint(painter, option, self.indexFromItem(header))
        finally:
            painter.end()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        super().scrollContentsBy(dx, dy)
        # Прокрутка сдвигает уже нарисованное, а приклеенный заголовок стоит
        # на месте: список перерисовывается целиком.
        self.viewport().update()

    def viewportEvent(self, event):  # noqa: N802
        if event.type() == QEvent.Type.ToolTip:
            pinned = self.pinned_group_header()
            if pinned is not None and pinned[1].contains(event.pos()):
                option = QStyleOptionViewItem()
                option.rect = pinned[1]
                self.itemDelegate().helpEvent(event, self, option, self.indexFromItem(pinned[0]))
                return True
        elif event.type() == QEvent.Type.Leave:
            self._set_pinned_hovered(False)
        return super().viewportEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        self._set_pinned_hovered(self._pinned_header_at(event.position().toPoint()) is not None)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        # Щелчок по приклеенному заголовку не должен достаться строке под ним.
        if self._pinned_header_at(event.position().toPoint()) is not None:
            self._pinned_pressed = event.button() == Qt.MouseButton.LeftButton
            event.accept()
            return
        self._pinned_pressed = False
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if self._pinned_header_at(event.position().toPoint()) is not None:
            self._pinned_pressed = event.button() == Qt.MouseButton.LeftButton
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _toggle_group_from_keyboard(self, key: int) -> bool:
        """Стрелка влево сворачивает группу текущей строки, вправо — раскрывает.

        Когда стратегии стоят плитками, на плитке эти стрелки переходят к
        соседней, а группу сворачивают только на её заголовке.
        """
        if key not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            return False
        if self._move_along_line_from_keyboard(key):
            return True
        item = self.currentItem()
        if item is None:
            return False
        header = item if _is_group_item(item) else self.group_header_for_row(self.row(item))
        if header is None:
            return False
        expanded = bool(header.data(ProfileStrategyListWidget._ROLE_GROUP_EXPANDED))
        if expanded == (key == Qt.Key.Key_Left):
            self.group_toggle_requested.emit(header)
        return True

    def keyPressEvent(self, event):  # noqa: N802
        if self._toggle_group_from_keyboard(event.key()):
            event.accept()
            return
        if self._move_current_row_from_keyboard(event.key()):
            event.accept()
            return
        navigation_keys = _NAVIGATION_KEYS
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            item = self.currentItem()
            if item is None:
                item = self._first_selectable_item()
                if item is not None:
                    self.setCurrentItem(item)
            if _is_group_item(item):
                self.group_toggle_requested.emit(item)
                event.accept()
                return
            if item is not None:
                self.itemActivated.emit(item)
                event.accept()
                return
        super().keyPressEvent(event)
        if event.key() in navigation_keys:
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._pinned_pressed:
            self._pinned_pressed = False
            header = self._pinned_header_at(event.position().toPoint())
            if header is not None and event.button() == Qt.MouseButton.LeftButton:
                self.group_toggle_requested.emit(header)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        item = self.itemAt(event.position().toPoint())
        if _is_group_item(item):
            self.group_toggle_requested.emit(item)

    def focusInEvent(self, event):  # noqa: N802
        super().focusInEvent(event)
        if self.currentItem() is None:
            item = self._first_selectable_item()
            if item is not None:
                self.setCurrentItem(item)

    def _first_selectable_item(self):
        # Сначала ищем саму стратегию; если все группы свёрнуты — первый заголовок.
        first_visible = None
        for row in range(self.count()):
            item = self.item(row)
            if item is None or _row_hidden(self, row):
                continue
            if item.flags() & Qt.ItemFlag.ItemIsSelectable:
                return item
            if first_visible is None:
                first_visible = item
        return first_visible

    def _move_current_row_from_keyboard(self, key: int) -> bool:
        if key not in _NAVIGATION_KEYS:
            return False
        count = self.count()
        if count <= 0:
            return False

        row = _keyboard_target_row(self, int(key), count=count, current_row=self.currentRow())
        if row < 0:
            return False

        self.setCurrentRow(row)
        item = self.currentItem()
        if item is not None:
            self.scrollToItem(item)
        return True


class ProfileStrategySearchLineEdit(SearchLineEdit):
    """Поиск стратегий, где Enter выбирает текущий результат."""

    activate_current_result = pyqtSignal()
    navigate_results = pyqtSignal(int)
    close_requested = pyqtSignal()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close_requested.emit()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activate_current_result.emit()
            event.accept()
            return
        if event.key() in (
            Qt.Key.Key_Down,
            Qt.Key.Key_Up,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
            Qt.Key.Key_PageDown,
            Qt.Key.Key_PageUp,
        ):
            self.navigate_results.emit(int(event.key()))
            event.accept()
            return
        super().keyPressEvent(event)


class ProfileStrategyListWidget(QWidget):
    """Большой список готовых стратегий для profile."""

    strategy_activated = pyqtSignal(str)
    # Пользователь открыл другую группу длинного списка: (чей список, ключ
    # группы; пустой ключ — всё свёрнуто). Страница сохраняет это в настройки.
    open_group_changed = pyqtSignal(str, str)
    # Пользователь выбрал, по чему группировать список (profile.strategy_grouping).
    grouping_changed = pyqtSignal(str)

    _ROLE_STRATEGY_ID = int(Qt.ItemDataRole.UserRole) + 1
    _ROLE_NAME_TEXT = int(Qt.ItemDataRole.UserRole) + 2
    _ROLE_STATUS_TEXT = int(Qt.ItemDataRole.UserRole) + 3
    _ROLE_IS_ACTIVE = int(Qt.ItemDataRole.UserRole) + 4
    _ROLE_VISUAL_ICON_NAME = int(Qt.ItemDataRole.UserRole) + 5
    _ROLE_VISUAL_COLOR = int(Qt.ItemDataRole.UserRole) + 6
    _ROLE_VISUAL_LABEL_TEXT = int(Qt.ItemDataRole.UserRole) + 7
    _ROLE_VISUAL_DESCRIPTION = int(Qt.ItemDataRole.UserRole) + 8
    _ROLE_TOOLTIP_TEXT = int(Qt.ItemDataRole.UserRole) + 9
    _ROLE_PAYLOAD_BADGE_TEXT = int(Qt.ItemDataRole.UserRole) + 10
    # Группы по способу обхода: заголовок помечен _ROLE_ROW_KIND, а ключ группы
    # есть и у заголовка, и у его стратегий.
    _ROLE_ROW_KIND = int(Qt.ItemDataRole.UserRole) + 11
    _ROLE_GROUP_KEY = int(Qt.ItemDataRole.UserRole) + 12
    _ROLE_GROUP_COUNT = int(Qt.ItemDataRole.UserRole) + 13
    _ROLE_GROUP_EXPANDED = int(Qt.ItemDataRole.UserRole) + 14
    _ROLE_RATING = int(Qt.ItemDataRole.UserRole) + 15
    _ROLE_FAVORITE = int(Qt.ItemDataRole.UserRole) + 16
    # У заголовка группы: название выбранной стратегии, если она в этой группе.
    _ROLE_GROUP_CURRENT_NAME = int(Qt.ItemDataRole.UserRole) + 17
    # Плитка пишет название и уточнение («из Steam») двумя строками.
    _ROLE_TITLE_TEXT = int(Qt.ItemDataRole.UserRole) + 18
    _ROLE_DETAIL_TEXT = int(Qt.ItemDataRole.UserRole) + 19

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_strategy_id = "none"
        self._entries = {}
        self._states = {}
        self._item_by_strategy_id = {}
        self._group_header_items = {}
        self._subgroup_items = []
        self._grouping = GROUPING_METHOD
        # Выбор в переключателе важнее сохранённого значения, которое страница
        # присылает вместе со строками.
        self._grouping_chosen_here = False
        # Что пользователь сам свернул в коротком списке (там группы
        # сворачиваются независимо друг от друга).
        self._group_expanded_choice = {}
        # Длинный список: какая группа открыта у каждого профиля. Ключ — метка
        # списка от страницы (постоянный ключ профиля), значение — ключ группы
        # или "" (всё свёрнуто).
        self._open_group_token = ""
        self._open_group_memory = {}
        self._rows_signature = None
        self._strategy_filter_runtime = OneShotWorkerRuntime()
        self._strategy_filter_state = LatestValueWorkerState(
            self._strategy_filter_runtime,
            empty_value=None,
        )
        self._strategy_filter_timer = QTimer(self)
        self._strategy_filter_timer.setSingleShot(True)
        self._strategy_filter_timer.timeout.connect(self._run_debounced_tree_rebuild)
        self.destroyed.connect(self._cleanup_strategy_filter_worker)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._grouping_row = QWidget(self)
        grouping_layout = QHBoxLayout(self._grouping_row)
        grouping_layout.setContentsMargins(0, 0, 0, 0)
        grouping_layout.setSpacing(10)
        self._grouping_label = BodyLabel("Группировать")
        grouping_layout.addWidget(self._grouping_label)
        self._grouping_combo = ComboBox(self._grouping_row)
        for grouping_key, grouping_title in STRATEGY_GROUPINGS:
            self._grouping_combo.addItem(grouping_title, userData=grouping_key)
        self._grouping_combo.setMinimumWidth(190)
        set_tooltip(
            self._grouping_combo,
            "По чему разложить стратегии: по способу обхода, по серии (первое слово названия) "
            "или по источнику (уточнение «из …» в названии).",
        )
        set_control_accessibility(
            self._grouping_combo,
            name="Группировка готовых стратегий",
            description="Выберите, по чему разложить список: по способу обхода, по серии или по источнику.",
        )
        self._grouping_combo.currentIndexChanged.connect(self._on_grouping_combo_changed)
        grouping_layout.addWidget(self._grouping_combo)
        grouping_layout.addStretch(1)
        # В коротком списке группировать нечего: переключатель появляется
        # вместе с длинным списком (set_rows).
        self._grouping_row.hide()
        layout.addWidget(self._grouping_row)

        top_row = QWidget(self)
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)

        self._search = ProfileStrategySearchLineEdit(self)
        self._search.setPlaceholderText("Поиск по готовым стратегиям")
        set_control_accessibility(self._search, name="Поиск готовых стратегий")
        set_tooltip(
            self._search,
            "Поиск по названию, прежнему названию, параметрам --lua-desync и описанию готовой стратегии. "
            "Ctrl+F — открыть или закрыть поиск, Esc — закрыть.",
        )
        set_control_accessibility(
            self._search,
            name="Поиск готовых стратегий",
            description=(
                "Поиск по названию, прежнему названию, параметрам --lua-desync и описанию готовой стратегии. "
                "После ввода перейдите в список клавишей Tab или нажмите Стрелка вниз, "
                "выберите стратегию стрелками вверх и вниз, "
                "затем нажмите Enter или Пробел. "
                "Esc закрывает поиск и сбрасывает фильтр."
            ),
        )
        remove_line_edit_buttons_from_tab_order(self._search)
        self._search.textChanged.connect(self._apply_filter)
        self._search.close_requested.connect(self.hide_search)
        top_layout.addWidget(self._search, 1)

        self._summary = BodyLabel("")
        set_tooltip(
            self._summary,
            "Сколько готовых стратегий сейчас показано после фильтра поиска.",
        )
        top_layout.addWidget(self._summary)

        self._search_close = TransparentToolButton(FluentIcon.CLOSE, top_row)
        set_tooltip(
            self._search_close,
            "Закрыть поиск и показать все стратегии (Esc).",
        )
        set_control_accessibility(
            self._search_close,
            name="Закрыть поиск стратегий",
            description="Скрывает строку поиска и сбрасывает фильтр списка стратегий.",
        )
        self._search_close.clicked.connect(self.hide_search)
        top_layout.addWidget(self._search_close)

        # Строка поиска скрыта по умолчанию и не занимает место: открывается по Ctrl+F.
        self._search_row = top_row
        self._search_row.hide()
        layout.addWidget(top_row)

        self._search_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self)
        # WindowShortcut: Ctrl+F работает с любым фокусом в окне; защита от
        # срабатывания на других страницах — проверка isVisible() в обработчике.
        self._search_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._search_shortcut.activated.connect(self._on_search_shortcut)
        self._search_shortcut.activatedAmbiguously.connect(self._on_search_shortcut)

        self._list = ProfileStrategyListView(self)
        self._list.setItemDelegate(ProfileStrategyListDelegate(self._list))
        # При выборе другой стратегии полоска акцента переезжает к новой строке.
        attach_active_row_motion(self._list, self._ROLE_IS_ACTIVE, row_rect_fn=self._list.item_paint_rect)
        # Строки идут слева направо с переносом: заголовок занимает ряд
        # целиком, а плиток в ряд встаёт столько, сколько позволяет ширина.
        self._list.setFlow(QListView.Flow.LeftToRight)
        self._list.setWrapping(True)
        self._list.setResizeMode(QListView.ResizeMode.Adjust)
        self._list.setMovement(QListView.Movement.Static)
        self._list.setSpacing(0)
        self._list.setUniformItemSizes(False)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocusProxy(self._list)
        set_control_accessibility(
            self._list,
            name="Список готовых стратегий",
            description="Выберите готовую стратегию стрелками вверх и вниз, затем нажмите Enter или Пробел. Ctrl+F открывает поиск по стратегиям.",
        )
        set_state_text(self._list, "Список готовых стратегий: список пока загружается")
        self._list.setMouseTracking(True)
        self._list.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self._list.setMinimumHeight(520)
        self._list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._list.currentItemChanged.connect(self._update_current_strategy_accessibility)
        self._list.itemActivated.connect(self._on_item_activated)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.group_toggle_requested.connect(self._toggle_group_item)
        self._search.activate_current_result.connect(self._activate_current_search_result)
        self._search.navigate_results.connect(self._navigate_strategy_results_from_search)
        self._list.installEventFilter(self)
        self._list.setStyleSheet(
            "QListWidget { background: rgba(255, 255, 255, 0.035); border: none; border-radius: 6px; outline: none; padding: 0; }"
            "QListWidget::viewport { background: transparent; }"
            "QListWidget::item { border: none; padding: 0; }"
            "QListWidget::item:selected { background: transparent; }"
            "QListWidget::item:hover { background: transparent; }"
        )
        self._list.set_vertical_padding(4)
        self._scrollbars = install_fluent_scrollbars(self._list, vertical=True, horizontal=False)
        layout.addWidget(self._list, 1)
        QWidget.setTabOrder(self._grouping_combo, self._search)
        QWidget.setTabOrder(self._search, self._list)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._list and event.type() == QEvent.Type.FocusIn:
            self._focus_first_strategy_row()
            self._update_current_strategy_accessibility(self._list.currentItem())
            return False
        if watched is self._list and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                item = self._list.currentItem()
                if item is not None:
                    self._on_item_activated(item)
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

    def _on_search_shortcut(self) -> None:
        if not self.isVisible() or not self.isEnabled():
            return
        # Ctrl+F работает как переключатель: повторное нажатие закрывает
        # поиск и сбрасывает фильтр, как Esc или кнопка закрытия.
        if self._search_row.isVisible():
            self.hide_search()
        else:
            self.show_search()

    def show_search(self) -> None:
        self._search_row.show()
        self._search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._search.selectAll()

    def hide_search(self) -> None:
        if self._search.text():
            self._search.clear()
        self._search_row.hide()
        self._list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _activate_current_search_result(self) -> None:
        item = self._list.currentItem()
        if item is None:
            self._focus_first_strategy_row()
            item = self._list.currentItem()
        if item is None:
            return
        self._list.setFocus(Qt.FocusReason.OtherFocusReason)
        self._on_item_activated(item)

    def keyPressEvent(self, event):  # noqa: N802
        if self._handle_strategy_keyboard_event(event):
            return
        super().keyPressEvent(event)

    def _navigate_strategy_results_from_search(self, key: int) -> None:
        self._move_strategy_current_row(int(key))

    def _handle_strategy_keyboard_event(self, event) -> bool:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            item = self._list.currentItem()
            if item is None:
                self._focus_first_strategy_row()
                item = self._list.currentItem()
            if item is not None:
                self._list.setFocus(Qt.FocusReason.OtherFocusReason)
                if _is_group_item(item):
                    self._toggle_group_item(item)
                else:
                    self._on_item_activated(item)
                event.accept()
                return True
            return False

        if int(key) not in {
            int(Qt.Key.Key_Down),
            int(Qt.Key.Key_Up),
            int(Qt.Key.Key_Home),
            int(Qt.Key.Key_End),
            int(Qt.Key.Key_PageDown),
            int(Qt.Key.Key_PageUp),
        }:
            return False

        if self._move_strategy_current_row(int(key)):
            event.accept()
            return True
        return False

    def _move_strategy_current_row(self, key: int) -> bool:
        if int(key) not in {
            int(Qt.Key.Key_Down),
            int(Qt.Key.Key_Up),
            int(Qt.Key.Key_Home),
            int(Qt.Key.Key_End),
            int(Qt.Key.Key_PageDown),
            int(Qt.Key.Key_PageUp),
        }:
            return False

        count = self._list.count()
        if count <= 0:
            return False

        row = _keyboard_target_row(self._list, int(key), count=count, current_row=self._list.currentRow())
        if row < 0:
            return False

        self._list.setFocus(Qt.FocusReason.OtherFocusReason)
        self._list.setCurrentRow(row)
        item = self._list.currentItem()
        if item is not None:
            self._update_current_strategy_accessibility(item)
        return True

    def _focus_first_strategy_row(self) -> None:
        if self._list.currentItem() is not None:
            return
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is None or _row_hidden(self._list, row):
                continue
            self._list.setCurrentItem(item)
            return

    # ------------------------------------------------------------------
    # Группы по способу обхода
    # ------------------------------------------------------------------

    def _group_headers(self) -> dict:
        return self.__dict__.setdefault("_group_header_items", {})

    def _group_choice(self) -> dict:
        return self.__dict__.setdefault("_group_expanded_choice", {})

    def _strategy_search_active(self) -> bool:
        try:
            return bool(str(self._search.text() or "").strip())
        except Exception:
            return False

    def _open_group_memory_dict(self) -> dict:
        return self.__dict__.setdefault("_open_group_memory", {})

    def _open_group_token_text(self) -> str:
        return str(self.__dict__.get("_open_group_token") or "")

    def _single_open_group(self) -> bool:
        """В длинном списке раскрыта одна группа, короткий раскрыт целиком."""
        return len(self.__dict__.get("_entries") or {}) > _AUTO_COLLAPSE_MIN_ROWS

    def _grouping_key(self) -> str:
        return normalize_strategy_grouping(self.__dict__.get("_grouping"))

    def _grouping_layout(self) -> StrategyGroupingLayout:
        """Раскладка нынешних стратегий по группам; считается один раз на набор."""
        entries = self.__dict__.get("_entries") or {}
        grouping = self._grouping_key()
        cached = self.__dict__.get("_grouping_layout_cache")
        if cached is not None and cached[0] is entries and cached[1] == grouping:
            return cached[2]
        layout = strategy_grouping_layout(entries, grouping)
        self.__dict__["_grouping_layout_cache"] = (entries, grouping, layout)
        return layout

    def _sync_grouping_combo(self) -> None:
        combo = self.__dict__.get("_grouping_combo")
        if combo is None:
            return
        index = combo.findData(self._grouping_key())
        if index < 0 or index == combo.currentIndex():
            return
        combo.blockSignals(True)
        try:
            combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)

    def _on_grouping_combo_changed(self, index: int) -> None:
        self._grouping_chosen_here = True
        self.set_grouping(str(self._grouping_combo.itemData(index) or ""), notify=True)

    def set_grouping(self, grouping: str, *, notify: bool = False) -> bool:
        """Раскладывает список по-другому; True — группировка изменилась."""
        grouping = normalize_strategy_grouping(grouping)
        if grouping == self._grouping_key():
            return False
        self._grouping = grouping
        self._sync_grouping_combo()
        # Ключи групп у каждой группировки свои: прежний выбор к ней не относится.
        self._group_choice().clear()
        token = self._open_group_token_text()
        open_group = ""
        if self._single_open_group():
            open_group = self._current_strategy_group()[0]
            self._open_group_memory_dict()[token] = open_group
        self._rows_signature = None
        if self.__dict__.get("_entries"):
            self._request_tree_rebuild(immediate=True)
        if notify:
            self.grouping_changed.emit(grouping)
            if self._single_open_group():
                self.open_group_changed.emit(token, open_group)
        return True

    def _auto_expanded_group_keys(self) -> set[str] | None:
        """Какая группа раскрыта в длинном списке.

        Та, которую человек оставил открытой у этого профиля; если он ещё
        ничего не открывал — группа выбранной стратегии. Остальные свёрнуты:
        длинный список открывается картой способов обхода. None — раскрыто
        всё (короткий список).
        """
        if not self._single_open_group():
            return None
        placements = self._grouping_layout().placements
        remembered = self._open_group_memory_dict().get(self._open_group_token_text())
        if remembered is not None:
            if not remembered:
                return set()
            if any(placement.group_key == remembered for placement in placements.values()):
                return {remembered}
        current_id = str(self.__dict__.get("_current_strategy_id") or "")
        if current_id in placements:
            return {placements[current_id].group_key}
        return set()

    def _group_is_expanded(self, group_key: str, auto_keys: set[str] | None) -> bool:
        if self._strategy_search_active():
            # Поиск показывает все совпадения, свёрнутые группы их не прячут.
            return True
        if auto_keys is None:
            return bool(self._group_choice().get(group_key, True))
        return group_key in auto_keys

    def set_open_group_memory(self, token: str, open_group: str | None) -> bool:
        """Чей это список и какую группу человек оставил открытой в прошлый раз.

        Страница вызывает это перед set_rows. Сохранённое значение нужно только
        при первом показе профиля: дальше список помнит свой выбор сам, и
        запоздавший ответ из настроек его не перебивает. Возвращает True, если
        список теперь принадлежит другому профилю.
        """
        token = str(token or "")
        memory = self._open_group_memory_dict()
        if token not in memory and open_group is not None:
            memory[token] = str(open_group)
        if token == self._open_group_token_text():
            return False
        self._open_group_token = token
        self._group_choice().clear()
        return True

    def _apply_open_group_to_built_rows(self) -> None:
        """Раскрывает нужную группу в уже собранном списке, без пересборки."""
        headers = self._group_headers()
        if not headers or self._strategy_search_active():
            return
        auto_keys = self._auto_expanded_group_keys()
        for group_key, header in headers.items():
            expanded = self._group_is_expanded(group_key, auto_keys)
            if bool(header.data(self._ROLE_GROUP_EXPANDED)) != expanded:
                self._set_group_expanded(group_key, expanded)
        current = self._item_by_strategy_id.get(str(self.__dict__.get("_current_strategy_id") or ""))
        if current is not None and not current.isHidden():
            self._list.setCurrentItem(current)
            self._list.scrollToItem(current)

    def _current_strategy_group(self) -> tuple[str, str]:
        """(ключ группы, название) выбранной стратегии; ("", "") — её нет в каталоге."""
        entries = self.__dict__.get("_entries") or {}
        current_id = str(self.__dict__.get("_current_strategy_id") or "")
        entry = entries.get(current_id)
        if entry is None:
            return "", ""
        name = str(getattr(entry, "name", "") or current_id)
        return self._grouping_layout().group_key(current_id), name

    def _apply_group_header_texts(self, header) -> None:
        """Текст для экранного диктора и подсказка заголовка по его данным."""
        current_name = str(header.data(self._ROLE_GROUP_CURRENT_NAME) or "")
        description = str(header.data(self._ROLE_VISUAL_DESCRIPTION) or "")
        accessible_text = ProfileStrategyListGroup(
            key=str(header.data(self._ROLE_GROUP_KEY) or ""),
            title=str(header.data(self._ROLE_NAME_TEXT) or ""),
            description=description,
            icon_name="",
            color="",
            count=int(header.data(self._ROLE_GROUP_COUNT) or 0),
        ).accessible_text(expanded=bool(header.data(self._ROLE_GROUP_EXPANDED)))
        tooltip = group_description_text(str(header.data(self._ROLE_GROUP_KEY) or ""), description)
        if current_name:
            accessible_text = f"{accessible_text} В этой группе выбранная стратегия: {current_name}."
            tooltip = "\n\n".join(
                part for part in (tooltip, f"В этой группе выбранная стратегия: {current_name}") if part
            )
        header.setText(accessible_text)
        header.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text)
        header.setData(self._ROLE_TOOLTIP_TEXT, tooltip)

    def _sync_group_current_marks(self) -> None:
        """Помечает группу, в которой стоит выбранная стратегия.

        Когда группы свёрнуты, выбранной строки не видно: пометка на заголовке
        показывает, где её искать.
        """
        headers = self._group_headers()
        if not headers:
            return
        group_key, name = self._current_strategy_group()
        changed = False
        for key, header in headers.items():
            value = name if key == group_key else ""
            if str(header.data(self._ROLE_GROUP_CURRENT_NAME) or "") == value:
                continue
            header.setData(self._ROLE_GROUP_CURRENT_NAME, value)
            self._apply_group_header_texts(header)
            changed = True
        if changed:
            # Заголовок может быть приклеен к верху списка — перерисовать и его.
            self._list.viewport().update()
            self._update_current_strategy_accessibility(self._list.currentItem())

    def _make_group_header_item(self, group: ProfileStrategyListGroup, *, expanded: bool) -> QListWidgetItem:
        item = QListWidgetItem()
        accessible_text = group.accessible_text(expanded=expanded)
        item.setText(accessible_text)
        # Заголовок не стратегия: первой строкой для выбора он не считается.
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        item.setData(self._ROLE_ROW_KIND, _ROW_KIND_GROUP)
        item.setData(self._ROLE_GROUP_KEY, group.key)
        item.setData(self._ROLE_GROUP_COUNT, int(group.count))
        item.setData(self._ROLE_GROUP_EXPANDED, bool(expanded))
        item.setData(self._ROLE_NAME_TEXT, group.title)
        item.setData(self._ROLE_VISUAL_ICON_NAME, group.icon_name)
        item.setData(self._ROLE_VISUAL_COLOR, group.color)
        item.setData(self._ROLE_VISUAL_DESCRIPTION, group.description)
        item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text)
        item.setData(self._ROLE_TOOLTIP_TEXT, group_description_text(group.key, group.description))
        return item

    def _make_subgroup_item(self, group_key: str, title: str, count: int) -> QListWidgetItem:
        """Подзаголовок внутри группы: подпись над плитками, выбрать её нельзя."""
        item = QListWidgetItem()
        accessible_text = f"{title}, {strategy_count_text(count)}"
        item.setText(accessible_text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setData(self._ROLE_ROW_KIND, _ROW_KIND_SUBGROUP)
        item.setData(self._ROLE_GROUP_KEY, group_key)
        item.setData(self._ROLE_GROUP_COUNT, int(count))
        item.setData(self._ROLE_NAME_TEXT, title)
        item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text)
        return item

    def _toggle_group_item(self, item) -> None:
        group_key = str(item.data(self._ROLE_GROUP_KEY) or "") if item is not None else ""
        if not group_key:
            return
        expanded = not bool(item.data(self._ROLE_GROUP_EXPANDED))
        if self._strategy_search_active():
            # В результатах поиска группу можно свернуть на время: к обычному
            # списку это не относится.
            self._set_group_expanded(group_key, expanded)
            return
        if not self._single_open_group():
            self._group_choice()[group_key] = expanded
            self._set_group_expanded(group_key, expanded)
            return
        # Заголовок мог быть приклеен к верху списка: тогда его место — верхний край.
        header_top = max(0, self._list.visualItemRect(item).top())
        for other_key, other in self._group_headers().items():
            if other_key != group_key and bool(other.data(self._ROLE_GROUP_EXPANDED)):
                self._set_group_expanded(other_key, False)
        self._set_group_expanded(group_key, expanded)
        self._keep_group_header_in_place(item, header_top)
        open_group = group_key if expanded else ""
        token = self._open_group_token_text()
        self._open_group_memory_dict()[token] = open_group
        self.open_group_changed.emit(token, open_group)

    def _keep_group_header_in_place(self, header, header_top: int) -> None:
        """После щелчка заголовок остаётся на виду, по возможности под курсором.

        Прежняя раскрытая группа могла стоять выше: её строки исчезли, и без
        поправки прокрутки нажатый заголовок улетел бы за верхний край списка.
        """
        current_top = self._list.visualItemRect(header).top()
        if current_top == int(header_top):
            return
        scroll_bar = self._list.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.value() + current_top - int(header_top))

    def _set_group_expanded(self, group_key: str, expanded: bool) -> None:
        """Сворачивает группу, пряча её строки: список при этом не пересобирается."""
        header = self._group_headers().get(group_key)
        if header is not None:
            header.setData(self._ROLE_GROUP_EXPANDED, bool(expanded))
            self._apply_group_header_texts(header)
        for item in (*self._item_by_strategy_id.values(), *(self.__dict__.get("_subgroup_items") or ())):
            if str(item.data(self._ROLE_GROUP_KEY) or "") == group_key:
                item.setHidden(not expanded)
        current = self._list.currentItem()
        if header is not None and current is not None and current.isHidden():
            self._list.setCurrentItem(header)
        self._update_current_strategy_accessibility(self._list.currentItem())

    def set_rows(
        self,
        *,
        entries,
        states,
        current_strategy_id: str,
        open_group_token: str | None = None,
        open_group: str | None = None,
        grouping: str | None = None,
    ) -> None:
        """Показывает стратегии профиля.

        open_group_token — постоянный ключ профиля, open_group — группа, которую
        человек оставил открытой у него в прошлый раз (None — ещё не открывал).
        Без метки список считается тем же, что и раньше. grouping — сохранённая
        группировка списка; она применяется, пока человек не выбрал другую сам.
        """
        grouping_changed = False
        if grouping is not None and not self.__dict__.get("_grouping_chosen_here"):
            if normalize_strategy_grouping(grouping) != self._grouping_key():
                self._grouping = normalize_strategy_grouping(grouping)
                self._sync_grouping_combo()
                self._group_choice().clear()
                # Уже собранные строки разложены по-старому: обновлять их на
                # месте нельзя, список собирается заново.
                grouping_changed = True
        grouping_row = self.__dict__.get("_grouping_row")
        if grouping_row is not None:
            grouping_row.setVisible(len(dict(entries or {})) > _AUTO_COLLAPSE_MIN_ROWS)
        next_entries = dict(entries or {})
        next_states = dict(states or {})
        next_current_id = str(current_strategy_id or "none").strip() or "none"
        next_signature = _strategy_rows_signature(next_entries, next_states)
        owner_changed = open_group_token is not None and self.set_open_group_memory(open_group_token, open_group)
        if set(next_entries.keys()) != set((self.__dict__.get("_entries") or {}).keys()):
            # Другой набор стратегий (другой профиль или протокол): свёрнутое
            # вручную относилось к прежнему списку.
            self._group_choice().clear()
            if open_group_token is None:
                self._open_group_memory_dict().pop(self._open_group_token_text(), None)
        if grouping_changed:
            self._entries = next_entries
            self._states = next_states
            self._current_strategy_id = next_current_id
            self._rows_signature = next_signature
            self._request_tree_rebuild(immediate=True)
            return
        if self.__dict__.get("_rows_signature") == next_signature:
            self._entries = next_entries
            self._states = next_states
            if next_current_id != self._current_strategy_id:
                self.set_current_strategy_id(next_current_id)
            if owner_changed:
                self._apply_open_group_to_built_rows()
            self._sync_group_current_marks()
            return
        if self._can_update_strategy_rows_in_place(next_entries, next_states):
            changed_strategy_ids = [
                strategy_id
                for strategy_id in next_entries
                if self._states.get(strategy_id) != next_states.get(strategy_id)
            ]
            self._entries = next_entries
            self._states = next_states
            self._current_strategy_id = next_current_id
            self._rows_signature = next_signature
            for strategy_id in changed_strategy_ids:
                item = self._item_by_strategy_id.get(strategy_id)
                self._refresh_strategy_item(item, strategy_id, is_current=strategy_id == self._current_strategy_id)
            if owner_changed:
                self._apply_open_group_to_built_rows()
            self._sync_group_current_marks()
            self._supersede_running_strategy_filter()
            return
        single_move = self._move_strategy_row_in_place(next_entries, next_states)
        if single_move is not None:
            changed_strategy_ids = [
                strategy_id
                for strategy_id in next_entries
                if self._states.get(strategy_id) != next_states.get(strategy_id)
            ]
            self._entries = next_entries
            self._states = next_states
            self._current_strategy_id = next_current_id
            self._rows_signature = next_signature
            item = self._item_by_strategy_id.get(single_move)
            self._refresh_strategy_item(item, single_move, is_current=single_move == self._current_strategy_id)
            for strategy_id in changed_strategy_ids:
                if strategy_id == single_move:
                    continue
                item = self._item_by_strategy_id.get(strategy_id)
                self._refresh_strategy_item(item, strategy_id, is_current=strategy_id == self._current_strategy_id)
            if owner_changed:
                self._apply_open_group_to_built_rows()
            self._sync_group_current_marks()
            self._supersede_running_strategy_filter()
            return
        self._entries = next_entries
        self._states = next_states
        self._current_strategy_id = next_current_id
        self._rows_signature = next_signature
        self._request_tree_rebuild(immediate=True)

    def _supersede_running_strategy_filter(self) -> None:
        """Строки обновлены на месте, а фильтр ещё считает по старому снимку:
        его план затёр бы свежие оценки/избранное/порядок. Новый запрос
        делает старый результат устаревшим (has_pending) и пересчитывает."""
        runtime = self.__dict__.get("_strategy_filter_runtime")
        try:
            running = runtime is not None and runtime.is_running()
        except Exception:
            running = False
        if running:
            self._request_tree_rebuild()

    def _can_update_strategy_rows_in_place(self, next_entries: dict, next_states: dict) -> bool:
        if set(self._entries.keys()) != set(next_entries.keys()):
            return False
        if not self._item_by_strategy_id:
            return False
        grouping = self._grouping_key()
        return _strategy_visible_order(self._entries, self._states, grouping) == _strategy_visible_order(
            next_entries, next_states, grouping
        )

    def _move_strategy_row_in_place(self, next_entries: dict, next_states: dict) -> str | None:
        if set(self._entries.keys()) != set(next_entries.keys()):
            return None
        if set(self._item_by_strategy_id.keys()) != set(self._entries.keys()):
            return None
        if _strategy_entry_signature(self._entries) != _strategy_entry_signature(next_entries):
            return None
        grouping = self._grouping_key()
        current_order = _strategy_visible_order(self._entries, self._states, grouping)
        next_order = _strategy_visible_order(next_entries, next_states, grouping)
        changed_strategy_ids = {
            strategy_id
            for strategy_id in next_entries
            if self._states.get(strategy_id) != next_states.get(strategy_id)
        }
        move = _single_strategy_order_move(current_order, next_order, preferred_strategy_ids=changed_strategy_ids)
        if move is None:
            return None
        source_index, insert_index = move
        strategy_id = current_order[source_index]
        item = self._item_by_strategy_id.get(strategy_id)
        if item is None:
            return None
        # Избранное меняет место только внутри своей группы и подзаголовка,
        # поэтому заголовки и их счётчики остаются как были. Новое место ищем
        # по соседу оттуда же: номера строк списка сдвинуты заголовками.
        placements = strategy_grouping_layout(next_entries, grouping).placements
        family_key = placements[strategy_id].group_key
        anchor_item = None
        insert_after = False
        for neighbor_index, after in ((insert_index - 1, True), (insert_index + 1, False)):
            if not 0 <= neighbor_index < len(next_order):
                continue
            neighbor_id = next_order[neighbor_index]
            if placements[neighbor_id] != placements[strategy_id]:
                continue
            anchor_item = self._item_by_strategy_id.get(neighbor_id)
            insert_after = after
            if anchor_item is not None:
                break
        if anchor_item is None:
            return None
        moved_item = self._list.takeItem(self._list.row(item))
        self._list.insertItem(self._list.row(anchor_item) + (1 if insert_after else 0), moved_item)
        header = self._group_headers().get(family_key)
        if header is not None:
            # Вынутая и вставленная строка снова видима — возвращаем ей
            # состояние её группы.
            moved_item.setHidden(not bool(header.data(self._ROLE_GROUP_EXPANDED)))
        return strategy_id

    def set_current_strategy_id(self, strategy_id: str) -> None:
        next_id = str(strategy_id or "none").strip() or "none"
        if next_id == self._current_strategy_id:
            return
        previous_id = self._current_strategy_id
        self._current_strategy_id = next_id
        previous_item = self._item_by_strategy_id.get(previous_id)
        next_item = self._item_by_strategy_id.get(next_id)
        self._refresh_strategy_item(previous_item, previous_id, is_current=False)
        if next_item is not previous_item:
            self._refresh_strategy_item(next_item, next_id, is_current=True)
        self._sync_group_current_marks()

    def _rebuild_tree(self) -> None:
        """Собирает список сразу, без фонового потока."""
        self._apply_strategy_list_plan(
            build_profile_strategy_list_plan(
                entries=self._entries,
                states=self._states,
                current_strategy_id=self._current_strategy_id,
                search_text=self._search.text(),
                grouping=self._grouping_key(),
            )
        )

    def _refresh_strategy_item(self, item, strategy_id: str, *, is_current: bool) -> None:
        if item is None:
            return
        state = self._states.get(strategy_id)
        status_parts = _strategy_status_parts(state, is_current=is_current, include_unselected=False)
        accessible_status_parts = _strategy_status_parts(state, is_current=is_current, include_unselected=True)
        changed = False
        status_text = " • ".join(status_parts)
        if str(item.data(self._ROLE_STATUS_TEXT) or "") != status_text:
            item.setData(self._ROLE_STATUS_TEXT, status_text)
            changed = True
        if bool(item.data(self._ROLE_IS_ACTIVE)) != bool(is_current):
            item.setData(self._ROLE_IS_ACTIVE, is_current)
            changed = True
        rating = str(getattr(state, "rating", "") or "")
        if str(item.data(self._ROLE_RATING) or "") != rating:
            item.setData(self._ROLE_RATING, rating)
            changed = True
        favorite = bool(getattr(state, "favorite", False))
        if bool(item.data(self._ROLE_FAVORITE)) != favorite:
            item.setData(self._ROLE_FAVORITE, favorite)
            changed = True
        if is_current:
            try:
                if self._list.currentItem() is not item:
                    self._list.setCurrentItem(item)
                    changed = True
            except Exception:
                self._list.setCurrentItem(item)
                changed = True
        name = str(item.data(self._ROLE_NAME_TEXT) or "")
        if name:
            accessible_text = _strategy_screen_reader_text(
                name=name,
                status_parts=accessible_status_parts,
                visual_label=str(item.data(self._ROLE_VISUAL_LABEL_TEXT) or ""),
                visual_description=str(item.data(self._ROLE_VISUAL_DESCRIPTION) or ""),
                payload_badge=str(item.data(self._ROLE_PAYLOAD_BADGE_TEXT) or ""),
            )
            if str(item.data(Qt.ItemDataRole.AccessibleTextRole) or "") != accessible_text:
                item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text)
                changed = True
            try:
                if str(item.text() or "") != accessible_text:
                    item.setText(accessible_text)
                    changed = True
            except Exception:
                pass
        if changed:
            self._list.viewport().update(self._list.visualItemRect(item))
            if self._list.currentItem() is item:
                self._update_current_strategy_accessibility(item)

    def _update_current_strategy_accessibility(self, item=None, _previous=None) -> None:
        accessible_text = ""
        if item is not None:
            accessible_text = str(item.data(Qt.ItemDataRole.AccessibleTextRole) or "").strip()
        if accessible_text:
            set_state_text(self._list, f"Готовая стратегия: {accessible_text}")
        else:
            set_state_text(self._list, self._empty_strategy_list_state_text())

    def _empty_strategy_list_state_text(self) -> str:
        search_text = ""
        try:
            search_text = str(self._search.text() or "").strip()
        except Exception:
            search_text = ""
        if search_text:
            return "Список готовых стратегий: по фильтру ничего не найдено"
        if not self._entries:
            return "Список готовых стратегий: список пуст"
        return "Список готовых стратегий"

    def _apply_filter(self) -> None:
        self._request_tree_rebuild()

    def _request_tree_rebuild(self, *, immediate: bool = False) -> None:
        runtime = self.__dict__.get("_strategy_filter_runtime")
        if runtime is None:
            self._rebuild_tree()
            return
        search = ""
        try:
            search = self._search.text().strip().lower()
        except Exception:
            search = ""
        self._strategy_filter_state_obj().pending = (
            dict(self._entries),
            dict(self._states),
            str(self._current_strategy_id or "none").strip() or "none",
            search,
        )
        if immediate:
            try:
                self._strategy_filter_timer.stop()
            except Exception:
                pass
            self._run_debounced_tree_rebuild()
            return
        try:
            self._strategy_filter_timer.start(120)
        except Exception:
            self._run_debounced_tree_rebuild()

    def _run_debounced_tree_rebuild(self) -> None:
        state = self._strategy_filter_state_obj()
        pending = state.pending
        if pending is None:
            return
        if state.is_busy():
            return
        state.pending = None
        entries, states, current_strategy_id, search_text = pending
        self._start_strategy_filter_worker(entries, states, current_strategy_id, search_text)

    def _start_strategy_filter_worker(self, entries, states, current_strategy_id: str, search_text: str) -> None:
        self._strategy_filter_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self.create_strategy_filter_worker(
                request_id,
                entries=entries,
                states=states,
                current_strategy_id=current_strategy_id,
                search_text=search_text,
            ),
            on_loaded=self._on_strategy_filter_loaded,
            on_failed=self._on_strategy_filter_failed,
            on_finished=self._on_strategy_filter_worker_finished,
        )

    def create_strategy_filter_worker(
        self,
        request_id: int,
        *,
        entries,
        states,
        current_strategy_id: str,
        search_text: str,
    ) -> ProfileStrategyListFilterWorker:
        return ProfileStrategyListFilterWorker(
            request_id,
            entries=entries,
            states=states,
            current_strategy_id=current_strategy_id,
            search_text=search_text,
            grouping=self._grouping_key(),
            # Без родителя — см. ProfilesList._start_view_state_worker: виджет
            # удаляется вместе со страницей профиля, пока фильтр ещё считает.
            parent=None,
        )

    def _on_strategy_filter_loaded(self, request_id: int, plan: ProfileStrategyListPlan) -> None:
        runtime = self.__dict__.get("_strategy_filter_runtime")
        if runtime is None or not runtime.is_current(request_id):
            return
        if self._strategy_filter_state_obj().has_pending():
            return
        if str(getattr(plan, "current_strategy_id", "") or "") != str(self._current_strategy_id or ""):
            self._request_tree_rebuild()
            return
        if normalize_strategy_grouping(getattr(plan, "grouping", "")) != self._grouping_key():
            # Пока план считался, человек переключил группировку.
            self._request_tree_rebuild()
            return
        self._apply_strategy_list_plan(plan)

    def _on_strategy_filter_failed(self, request_id: int, error: str) -> None:
        runtime = self.__dict__.get("_strategy_filter_runtime")
        if runtime is None or not runtime.is_current(request_id):
            return
        if self._strategy_filter_state_obj().has_pending():
            return
        log(f"Ошибка подготовки списка готовых стратегий profile: {error}", "DEBUG")

    def _on_strategy_filter_worker_finished(self, worker) -> None:
        self._strategy_filter_state_obj().schedule_pending_after_finish(
            worker,
            is_current_worker_finish=lambda _runtime, current_worker: self._is_current_strategy_filter_worker_finish(
                current_worker,
            ),
            single_shot=QTimer.singleShot,
            run_scheduled=self._run_scheduled_strategy_filter_worker_start,
            cleanup_in_progress=bool(getattr(self, "_cleanup_in_progress", False)),
        )

    def _schedule_strategy_filter_worker_start(self) -> None:
        self._strategy_filter_state_obj().schedule_start(
            QTimer.singleShot,
            self._run_scheduled_strategy_filter_worker_start,
            pending_when_already_scheduled=self._strategy_filter_state_obj().pending,
        )

    def _run_scheduled_strategy_filter_worker_start(self) -> None:
        pending = self._strategy_filter_state_obj().take_pending_for_scheduled_start()
        if pending is None:
            return
        entries, states, current_strategy_id, search_text = pending
        self._start_strategy_filter_worker(entries, states, current_strategy_id, search_text)

    def _is_current_strategy_filter_worker_finish(self, worker) -> bool:
        runtime = self.__dict__.get("_strategy_filter_runtime")
        if runtime is None:
            return False
        if getattr(runtime, "worker", None) is worker:
            return True
        request_id = getattr(worker, "_request_id", None)
        if request_id is None:
            return False
        return int(request_id) == int(getattr(runtime, "request_id", 0) or 0)

    def _strategy_filter_state_obj(self) -> LatestValueWorkerState:
        state = self.__dict__.get("_strategy_filter_state")
        runtime = self.__dict__.get("_strategy_filter_runtime")
        if state is None:
            pending = self.__dict__.pop("_strategy_filter_pending", None)
            start_scheduled = bool(self.__dict__.pop("_strategy_filter_start_scheduled", False))
            state = LatestValueWorkerState(
                runtime,
                empty_value=None,
                pending=pending,
                start_scheduled=start_scheduled,
            )
            self.__dict__["_strategy_filter_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state

    def _apply_strategy_list_plan(self, plan: ProfileStrategyListPlan) -> None:
        self._item_by_strategy_id.clear()
        headers = self._group_headers()
        headers.clear()
        subgroup_items = self.__dict__.setdefault("_subgroup_items", [])
        subgroup_items.clear()
        self._list.clear()
        current_item = None
        first_item = None
        first_header = None
        groups = {group.key: group for group in tuple(getattr(plan, "groups", ()) or ())}
        auto_keys = self._auto_expanded_group_keys() if groups else None
        rows = tuple(getattr(plan, "rows", ()) or ())
        subgroup_counts: dict[str, int] = {}
        for row in rows:
            subgroup_key = str(getattr(row, "subgroup_key", "") or "")
            if subgroup_key:
                subgroup_counts[subgroup_key] = subgroup_counts.get(subgroup_key, 0) + 1
        shown_subgroups: set[str] = set()

        for row in rows:
            family_key = str(getattr(row, "family_key", "") or "")
            expanded = True
            if family_key in groups:
                expanded = self._group_is_expanded(family_key, auto_keys)
                if family_key not in headers:
                    header = self._make_group_header_item(groups[family_key], expanded=expanded)
                    headers[family_key] = header
                    self._list.addItem(header)
                    if first_header is None:
                        first_header = header
            subgroup_key = str(getattr(row, "subgroup_key", "") or "")
            if subgroup_key and subgroup_key not in shown_subgroups:
                shown_subgroups.add(subgroup_key)
                subgroup_item = self._make_subgroup_item(
                    family_key,
                    str(getattr(row, "subgroup_title", "") or ""),
                    subgroup_counts[subgroup_key],
                )
                subgroup_items.append(subgroup_item)
                self._list.addItem(subgroup_item)
                if not expanded:
                    subgroup_item.setHidden(True)
            item = QListWidgetItem()
            item.setText(row.accessible_text or row.name)
            item.setData(self._ROLE_STRATEGY_ID, row.strategy_id)
            item.setData(self._ROLE_NAME_TEXT, row.name)
            item.setData(self._ROLE_TITLE_TEXT, str(getattr(row, "title", "") or row.name))
            item.setData(self._ROLE_DETAIL_TEXT, str(getattr(row, "detail", "") or ""))
            item.setData(self._ROLE_PAYLOAD_BADGE_TEXT, row.payload_badge)
            item.setData(self._ROLE_STATUS_TEXT, row.status_text)
            item.setData(self._ROLE_IS_ACTIVE, row.is_current)
            item.setData(self._ROLE_GROUP_KEY, family_key)
            item.setData(self._ROLE_RATING, str(getattr(row, "rating", "") or ""))
            item.setData(self._ROLE_FAVORITE, bool(getattr(row, "favorite", False)))
            item.setData(self._ROLE_VISUAL_ICON_NAME, row.visual_icon_name)
            item.setData(self._ROLE_VISUAL_COLOR, row.visual_color)
            item.setData(self._ROLE_VISUAL_LABEL_TEXT, row.visual_label)
            item.setData(self._ROLE_VISUAL_DESCRIPTION, row.visual_description)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, row.accessible_text)
            item.setData(self._ROLE_TOOLTIP_TEXT, row.tooltip_text)
            self._item_by_strategy_id[row.strategy_id] = item
            self._list.addItem(item)
            if not expanded:
                item.setHidden(True)
            elif first_item is None:
                first_item = item
            if row.is_current and expanded:
                current_item = item

        summary_text = f"{int(getattr(plan, 'visible_count', 0) or 0)} из {int(getattr(plan, 'total_count', 0) or 0)}"
        _set_widget_text_if_changed(self._summary, summary_text)
        set_state_text(self._summary, f"Показано готовых стратегий: {summary_text}")
        # Текущей становится выбранная стратегия; если её группа свёрнута —
        # первая видимая стратегия, а когда свёрнуто всё — первый заголовок.
        focus_item = current_item or first_item or first_header
        if focus_item is not None:
            self._list.setCurrentItem(focus_item)
        self._sync_group_current_marks()
        self._update_current_strategy_accessibility(self._list.currentItem())

    def _cleanup_strategy_filter_worker(self, *_args) -> None:
        try:
            self._strategy_filter_timer.stop()
        except Exception:
            pass
        try:
            self._strategy_filter_state_obj().reset()
        except Exception:
            pass
        runtime = self.__dict__.get("_strategy_filter_runtime")
        if runtime is not None:
            runtime.stop(
                blocking=False,
                log_fn=log,
                warning_prefix="Profile strategy filter worker",
            )
            runtime.cancel()

    def _strategy_id_for_item(self, item) -> str:
        return str(item.data(self._ROLE_STRATEGY_ID) or "").strip() if item is not None else ""

    def _on_item_clicked(self, item) -> None:
        strategy_id = self._strategy_id_for_item(item)
        if strategy_id == self._current_strategy_id:
            return
        if strategy_id:
            self.strategy_activated.emit(strategy_id)

    def _on_item_activated(self, item) -> None:
        strategy_id = self._strategy_id_for_item(item)
        if strategy_id == self._current_strategy_id:
            return
        if strategy_id:
            self.strategy_activated.emit(strategy_id)


def _strategy_rows_signature(entries, states) -> tuple[tuple, tuple]:
    entry_rows = []
    for strategy_id, entry in dict(entries or {}).items():
        visual = getattr(entry, "visual", None)
        entry_rows.append((
            str(strategy_id),
            str(getattr(entry, "name", "") or ""),
            str(getattr(entry, "args", "") or ""),
            str(getattr(visual, "icon_name", "") or ""),
            str(getattr(visual, "color", "") or ""),
            str(getattr(visual, "label", "") or ""),
            str(getattr(visual, "description", "") or ""),
        ))
    state_rows = []
    for strategy_id, state in dict(states or {}).items():
        state_rows.append((
            str(strategy_id),
            str(getattr(state, "rating", "") or ""),
            bool(getattr(state, "favorite", False)),
        ))
    return tuple(sorted(entry_rows)), tuple(sorted(state_rows))


def _strategy_status_parts(state, *, is_current: bool, include_unselected: bool) -> list[str]:
    status_parts = []
    if is_current:
        status_parts.append("Выбрана")
    elif include_unselected:
        status_parts.append("Не выбрана")
    if bool(getattr(state, "favorite", False)):
        status_parts.append("В избранном")
    rating = str(getattr(state, "rating", "") or "")
    if rating == "work":
        status_parts.append("Работает")
    elif rating == "notwork":
        status_parts.append("Не работает")
    return status_parts


def _set_strategy_feedback_button_state(button, *, action_name: str, selected: bool) -> None:
    state_text = "выбрана" if selected else "не выбрана"
    set_state_text(button, f"{action_name}. Оценка стратегии: {state_text}.")


def _set_strategy_favorite_button_state(button, *, action_name: str, favorite: bool) -> None:
    state_text = "включено" if favorite else "не включено"
    set_state_text(button, f"{action_name}. Избранное: {state_text}.")


def _strategy_screen_reader_text(
    *,
    name: str,
    status_parts: list[str],
    visual_label: str,
    visual_description: str,
    payload_badge: str = "",
) -> str:
    parts = [str(name or "").strip(), payload_badge_accessible_text(payload_badge)]
    parts.extend(_lower_first(part) for part in status_parts if str(part or "").strip())
    parts.extend(
        str(part or "").strip()
        for part in (visual_label, visual_description)
        if str(part or "").strip()
    )
    text = ", ".join(part for part in parts if part)
    return f"{text}. Нажмите Enter или Пробел, чтобы выбрать стратегию." if text else ""


def _lower_first(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    return value[:1].lower() + value[1:]


def _strategy_entry_signature(entries) -> tuple:
    return _strategy_rows_signature(entries, {})[0]


def _strategy_visible_order(entries, states, grouping: str = GROUPING_METHOD) -> tuple[str, ...]:
    entries = dict(entries or {})
    states = dict(states or {})
    layout = strategy_grouping_layout(entries, grouping)
    return tuple(
        strategy_id
        for strategy_id, _entry in sorted(
            entries.items(),
            key=lambda pair: layout.sort_key(pair[0], pair[1], states.get(pair[0])),
        )
    )


def _single_strategy_order_move(
    current_order: tuple[str, ...],
    next_order: tuple[str, ...],
    *,
    preferred_strategy_ids: set[str] | None = None,
) -> tuple[int, int] | None:
    if len(current_order) != len(next_order):
        return None
    if current_order == next_order:
        return None
    if len(set(current_order)) != len(current_order):
        return None
    if set(current_order) != set(next_order):
        return None

    current_positions = {strategy_id: index for index, strategy_id in enumerate(current_order)}
    first_changed = next(
        (
            index
            for index, strategy_id in enumerate(next_order)
            if current_positions.get(strategy_id) != index
        ),
        -1,
    )
    if first_changed < 0:
        return None

    last_changed = len(next_order) - 1
    while last_changed > first_changed and current_order[last_changed] == next_order[last_changed]:
        last_changed -= 1

    candidates = []
    if current_order[first_changed] == next_order[last_changed]:
        candidates.append((first_changed, last_changed))
    if current_order[last_changed] == next_order[first_changed]:
        candidates.append((last_changed, first_changed))
    if not candidates:
        return None

    preferred = set(preferred_strategy_ids or ())
    for source_index, insert_index in candidates:
        if current_order[source_index] in preferred:
            return source_index, insert_index
    return candidates[0]


def _current_strategy_id(payload) -> str:
    item = getattr(payload, "item", None)
    return str(getattr(item, "strategy_id", "") or "").strip()


def _combo_item_accessible_text(
    *,
    name: str,
    label: str,
    selected: bool,
    selected_word: str = "выбран",
    unselected_word: str = "не выбран",
) -> str:
    state = selected_word if selected else unselected_word
    return f"{str(name or '').strip()}: {str(label or '').strip()}, {state}"


def _sync_combo_items_accessibility(
    combo,
    *,
    name: str,
    selected_word: str = "выбран",
    unselected_word: str = "не выбран",
) -> None:
    if combo is None:
        return
    try:
        current_index = int(combo.currentIndex())
        count = int(combo.count())
    except Exception:
        return
    set_item_accessible_text = getattr(combo, "setItemAccessibleText", None)
    if not callable(set_item_accessible_text):
        return
    for index in range(count):
        try:
            label = str(combo.itemText(index) or "").strip()
        except Exception:
            label = ""
        if not label:
            continue
        set_item_accessible_text(
            index,
            _combo_item_accessible_text(
                name=name,
                label=label,
                selected=index == current_index,
                selected_word=selected_word,
                unselected_word=unselected_word,
            ),
        )


def _join_accessible_options(labels: list[str]) -> str:
    clean_labels = [str(label or "").strip() for label in labels if str(label or "").strip()]
    if not clean_labels:
        return ""
    if len(clean_labels) == 1:
        return clean_labels[0]
    return f"{', '.join(clean_labels[:-1])} или {clean_labels[-1]}"
