"""Виджеты списка готовых стратегий profile и их free-функции.

Вынесено из profile_setup_page.py (этап 4, фаза B, чанк M1) без изменения
поведения: классы реэкспортируются из profile.ui.profile_setup_page.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QEvent, QModelIndex, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QFontMetrics, QKeySequence, QPainter, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QHBoxLayout,
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
from profile.strategy_families import strategy_family_keys, strategy_sort_key
from profile.strategy_list_filter import (
    STRATEGY_SELECTED_TEXT,
    ProfileStrategyListFilterWorker,
    ProfileStrategyListGroup,
    ProfileStrategyListPlan,
    strategy_list_groups,
)
from profile.strategy_state import ProfileStrategyState
from profile.strategy_shape import payload_badge_accessible_text, payload_badge_text
from profile.strategy_visuals import describe_strategy_visual
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
_STRATEGY_ROW_HEIGHT = 31
# В коротком списке сворачивать нечего: все группы раскрыты сразу.
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


def _is_group_item(item) -> bool:
    """Строка списка — заголовок группы стратегий, а не сама стратегия."""
    if item is None:
        return False
    try:
        return str(item.data(ProfileStrategyListWidget._ROLE_ROW_KIND) or "") == _ROW_KIND_GROUP
    except Exception:
        return False


def _row_hidden(list_widget, row: int) -> bool:
    checker = getattr(list_widget, "isRowHidden", None)
    if not callable(checker):
        return False
    try:
        return bool(checker(row))
    except Exception:
        return False


def _visible_row_from(list_widget, row: int, step: int, count: int) -> int:
    """Ближайшая видимая строка начиная с row в сторону step; -1, если таких нет."""
    while 0 <= row < count:
        if not _row_hidden(list_widget, row):
            return row
        row += step
    return -1


def _keyboard_target_row(list_widget, key: int, *, count: int, current_row: int) -> int:
    """Куда клавиша навигации переводит текущую строку.

    Строки свёрнутых групп скрыты, поэтому счёт идёт только по видимым.
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
    row = current_row
    for _ in range(moves):
        next_row = _visible_row_from(list_widget, row + step, step, count)
        if next_row < 0:
            break
        row = next_row
    if _row_hidden(list_widget, row):
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
        if str(index.data(ProfileStrategyListWidget._ROLE_ROW_KIND) or "") == _ROW_KIND_GROUP:
            self._paint_group_header(painter, option, index)
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

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        _ = (option, index)
        return QSize(0, _STRATEGY_ROW_HEIGHT)

    def helpEvent(self, event, view, option, index: QModelIndex) -> bool:  # noqa: N802
        _ = (view, option)
        text = str(index.data(ProfileStrategyListWidget._ROLE_TOOLTIP_TEXT) or "").strip()
        if not text:
            self._tooltip.hide()
            return True
        self._tooltip.show_text(text, event.globalPos())
        return True


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

    def keyPressEvent(self, event):  # noqa: N802
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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_strategy_id = "none"
        self._entries = {}
        self._states = {}
        self._item_by_strategy_id = {}
        self._group_header_items = {}
        # Что пользователь сам свернул или развернул; остальное решает
        # _auto_expanded_group_keys.
        self._group_expanded_choice = {}
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

        top_row = QWidget(self)
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)

        self._search = ProfileStrategySearchLineEdit(self)
        self._search.setPlaceholderText("Поиск по готовым стратегиям")
        set_control_accessibility(self._search, name="Поиск готовых стратегий")
        set_tooltip(
            self._search,
            "Поиск по названию, параметрам --lua-desync и описанию готовой стратегии. "
            "Ctrl+F — открыть или закрыть поиск, Esc — закрыть.",
        )
        set_control_accessibility(
            self._search,
            name="Поиск готовых стратегий",
            description=(
                "Поиск по названию, параметрам --lua-desync и описанию готовой стратегии. "
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
        attach_active_row_motion(self._list, self._ROLE_IS_ACTIVE, row_rect_fn=profile_hover_row_rect)
        self._list.setUniformItemSizes(True)
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
            "QListWidget { background: rgba(255, 255, 255, 0.035); border: none; border-radius: 6px; outline: none; padding: 4px 0; }"
            "QListWidget::viewport { background: transparent; }"
            "QListWidget::item { border: none; padding: 0; }"
            "QListWidget::item:selected { background: transparent; }"
            "QListWidget::item:hover { background: transparent; }"
        )
        self._scrollbars = install_fluent_scrollbars(self._list, vertical=True, horizontal=False)
        layout.addWidget(self._list, 1)
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

    def _auto_expanded_group_keys(self) -> set[str] | None:
        """Какие группы раскрыты, пока пользователь сам их не трогал.

        Раскрыто то, с чем человек уже работал: группа выбранной стратегии и
        группы с избранным или с отметкой «работает». Остальные свёрнуты —
        длинный список открывается картой способов обхода. None — раскрыто
        всё (короткий список).
        """
        entries = self.__dict__.get("_entries") or {}
        states = self.__dict__.get("_states") or {}
        if len(entries) <= _AUTO_COLLAPSE_MIN_ROWS:
            return None
        current_id = str(self.__dict__.get("_current_strategy_id") or "")
        family_keys = strategy_family_keys(entries)
        keys: set[str] = set()
        for strategy_id in entries:
            state = states.get(strategy_id)
            if (
                strategy_id == current_id
                or bool(getattr(state, "favorite", False))
                or str(getattr(state, "rating", "") or "") == "work"
            ):
                keys.add(family_keys[strategy_id])
        return keys

    def _group_is_expanded(self, group_key: str, auto_keys: set[str] | None) -> bool:
        if self._strategy_search_active():
            # Поиск показывает все совпадения, свёрнутые группы их не прячут.
            return True
        choice = self._group_choice()
        if group_key in choice:
            return bool(choice[group_key])
        return auto_keys is None or group_key in auto_keys

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
        item.setData(self._ROLE_TOOLTIP_TEXT, f"Способ обхода: {group.description}.")
        item.setSizeHint(QSize(0, _STRATEGY_ROW_HEIGHT))
        return item

    def _toggle_group_item(self, item) -> None:
        group_key = str(item.data(self._ROLE_GROUP_KEY) or "") if item is not None else ""
        if not group_key:
            return
        expanded = not bool(item.data(self._ROLE_GROUP_EXPANDED))
        self._group_choice()[group_key] = expanded
        self._set_group_expanded(group_key, expanded)

    def _set_group_expanded(self, group_key: str, expanded: bool) -> None:
        """Сворачивает группу, пряча её строки: список при этом не пересобирается."""
        header = self._group_headers().get(group_key)
        if header is not None:
            header.setData(self._ROLE_GROUP_EXPANDED, bool(expanded))
            accessible_text = ProfileStrategyListGroup(
                key=group_key,
                title=str(header.data(self._ROLE_NAME_TEXT) or ""),
                description=str(header.data(self._ROLE_VISUAL_DESCRIPTION) or ""),
                icon_name="",
                color="",
                count=int(header.data(self._ROLE_GROUP_COUNT) or 0),
            ).accessible_text(expanded=bool(expanded))
            header.setText(accessible_text)
            header.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text)
        for item in self._item_by_strategy_id.values():
            if str(item.data(self._ROLE_GROUP_KEY) or "") == group_key:
                item.setHidden(not expanded)
        current = self._list.currentItem()
        if header is not None and current is not None and current.isHidden():
            self._list.setCurrentItem(header)
        self._update_current_strategy_accessibility(self._list.currentItem())

    def set_rows(self, *, entries, states, current_strategy_id: str) -> None:
        next_entries = dict(entries or {})
        next_states = dict(states or {})
        next_current_id = str(current_strategy_id or "none").strip() or "none"
        next_signature = _strategy_rows_signature(next_entries, next_states)
        if set(next_entries.keys()) != set((self.__dict__.get("_entries") or {}).keys()):
            # Другой набор стратегий (другой профиль или протокол): свёрнутое
            # вручную относилось к прежнему списку.
            self._group_choice().clear()
        if self.__dict__.get("_rows_signature") == next_signature:
            self._entries = next_entries
            self._states = next_states
            if next_current_id != self._current_strategy_id:
                self.set_current_strategy_id(next_current_id)
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
        return _strategy_visible_order(self._entries, self._states) == _strategy_visible_order(next_entries, next_states)

    def _move_strategy_row_in_place(self, next_entries: dict, next_states: dict) -> str | None:
        if set(self._entries.keys()) != set(next_entries.keys()):
            return None
        if set(self._item_by_strategy_id.keys()) != set(self._entries.keys()):
            return None
        if _strategy_entry_signature(self._entries) != _strategy_entry_signature(next_entries):
            return None
        current_order = _strategy_visible_order(self._entries, self._states)
        next_order = _strategy_visible_order(next_entries, next_states)
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
        # Избранное меняет место только внутри своей группы, поэтому заголовки
        # и их счётчики остаются как были. Новое место ищем по соседу из той же
        # группы: номера строк списка сдвинуты заголовками.
        family_keys = strategy_family_keys(next_entries)
        family_key = family_keys[strategy_id]
        anchor_item = None
        insert_after = False
        for neighbor_index, after in ((insert_index - 1, True), (insert_index + 1, False)):
            if not 0 <= neighbor_index < len(next_order):
                continue
            neighbor_id = next_order[neighbor_index]
            if family_keys[neighbor_id] != family_key:
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

    def _rebuild_tree(self) -> None:
        search_text = self._search.text().strip().lower()
        self._item_by_strategy_id.clear()
        headers = self._group_headers()
        headers.clear()
        self._list.clear()
        visible = 0
        current_item = None
        first_item = None
        first_header = None

        family_keys = strategy_family_keys(self._entries)
        rows = list(self._entries.items())
        rows.sort(key=lambda pair: strategy_sort_key(family_keys[pair[0]], pair[1], self._states.get(pair[0])))

        matched = []
        group_counts: dict[str, int] = {}
        for strategy_id, entry in rows:
            name = str(getattr(entry, "name", "") or strategy_id)
            args = str(getattr(entry, "args", "") or "")
            visual = getattr(entry, "visual", None) or describe_strategy_visual(args)
            visual_label = str(visual.label or "")
            visual_description = str(visual.description or "")
            visual_search = f"{visual_label} {visual_description}".lower()
            if search_text and search_text not in name.lower() and search_text not in args.lower() and search_text not in visual_search:
                continue
            family_key = family_keys[strategy_id]
            group_counts[family_key] = group_counts.get(family_key, 0) + 1
            matched.append((strategy_id, entry, name, args, visual, visual_label, visual_description, family_key))

        groups = {group.key: group for group in strategy_list_groups(group_counts)}
        auto_keys = self._auto_expanded_group_keys() if groups else None

        for strategy_id, entry, name, args, visual, visual_label, visual_description, family_key in matched:
            expanded = True
            if family_key in groups:
                expanded = self._group_is_expanded(family_key, auto_keys)
                if family_key not in headers:
                    header = self._make_group_header_item(groups[family_key], expanded=expanded)
                    headers[family_key] = header
                    self._list.addItem(header)
                    if first_header is None:
                        first_header = header
            payload_badge = payload_badge_text(getattr(entry, "payload_scopes", ()) or ())

            item = QListWidgetItem()
            state = self._states.get(strategy_id)
            is_current = strategy_id == self._current_strategy_id
            status_parts = _strategy_status_parts(state, is_current=is_current, include_unselected=False)
            accessible_status_parts = _strategy_status_parts(state, is_current=is_current, include_unselected=True)
            status_text = " • ".join(status_parts)

            accessible_text = _strategy_screen_reader_text(
                name=name,
                status_parts=accessible_status_parts,
                visual_label=visual_label,
                visual_description=visual_description,
                payload_badge=payload_badge,
            )
            item.setText(accessible_text)
            item.setData(self._ROLE_STRATEGY_ID, strategy_id)
            item.setData(self._ROLE_NAME_TEXT, name)
            item.setData(self._ROLE_PAYLOAD_BADGE_TEXT, payload_badge)
            item.setData(self._ROLE_STATUS_TEXT, status_text)
            item.setData(self._ROLE_IS_ACTIVE, is_current)
            item.setData(self._ROLE_GROUP_KEY, family_key)
            item.setData(self._ROLE_RATING, str(getattr(state, "rating", "") or ""))
            item.setData(self._ROLE_FAVORITE, bool(getattr(state, "favorite", False)))
            item.setData(self._ROLE_VISUAL_ICON_NAME, str(visual.icon_name or ""))
            item.setData(self._ROLE_VISUAL_COLOR, str(visual.color or ""))
            item.setData(self._ROLE_VISUAL_LABEL_TEXT, visual_label)
            item.setData(self._ROLE_VISUAL_DESCRIPTION, visual_description)
            item.setData(
                Qt.ItemDataRole.AccessibleTextRole,
                accessible_text,
            )
            tooltip_parts = [visual_description.strip(), args]
            item.setData(self._ROLE_TOOLTIP_TEXT, "\n\n".join(part for part in tooltip_parts if part))
            item.setSizeHint(QSize(0, _STRATEGY_ROW_HEIGHT))
            self._item_by_strategy_id[strategy_id] = item
            self._list.addItem(item)
            if not expanded:
                item.setHidden(True)
            elif first_item is None:
                first_item = item
            if is_current and expanded:
                current_item = item
            visible += 1

        summary_text = f"{visible} из {len(self._entries)}"
        _set_widget_text_if_changed(self._summary, summary_text)
        set_state_text(self._summary, f"Показано готовых стратегий: {summary_text}")
        # Текущей становится выбранная стратегия; если её группа свёрнута —
        # первая видимая стратегия, а когда свёрнуто всё — первый заголовок.
        focus_item = current_item or first_item or first_header
        if focus_item is not None:
            self._list.setCurrentItem(focus_item)
        self._update_current_strategy_accessibility(self._list.currentItem())

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
        self._list.clear()
        current_item = None
        first_item = None
        first_header = None
        groups = {group.key: group for group in tuple(getattr(plan, "groups", ()) or ())}
        auto_keys = self._auto_expanded_group_keys() if groups else None

        for row in tuple(getattr(plan, "rows", ()) or ()):
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
            item = QListWidgetItem()
            item.setText(row.accessible_text or row.name)
            item.setData(self._ROLE_STRATEGY_ID, row.strategy_id)
            item.setData(self._ROLE_NAME_TEXT, row.name)
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
            item.setSizeHint(QSize(0, _STRATEGY_ROW_HEIGHT))
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


def _set_strategy_clear_feedback_button_state(button, *, rating: str) -> None:
    rating_value = str(rating or "").strip()
    if rating_value == "work":
        rating_text = "работает"
    elif rating_value == "notwork":
        rating_text = "не работает"
    else:
        rating_text = "не задана"
    set_state_text(button, f"Убрать оценку стратегии. Текущая оценка: {rating_text}.")


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


def _strategy_visible_order(entries, states) -> tuple[str, ...]:
    entries = dict(entries or {})
    states = dict(states or {})
    family_keys = strategy_family_keys(entries)
    return tuple(
        strategy_id
        for strategy_id, _entry in sorted(
            entries.items(),
            key=lambda pair: strategy_sort_key(family_keys[pair[0]], pair[1], states.get(pair[0])),
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
