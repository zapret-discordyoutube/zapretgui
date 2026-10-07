"""Список стратегий: мышь, клавиатура и плитки в несколько столбцов.

Сам ничего не решает: о щелчках и клавишах сообщает сигналами, а что после
этого показать — определяет ``widget`` через модель.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QAbstractScrollArea, QListView, QSizePolicy, QStyle

from profile.strategy_list import ROW_GROUP, ROW_SECTION, ROW_STRATEGY
from profile.ui.strategy_list.delegate import StrategyListDelegate
from profile.ui.strategy_list.model import ACTIVE_ROLE, ROW_ROLE, StrategyListModel
from ui.widgets.active_row_motion import attach_active_row_motion
from ui.widgets.fluent_scrollbar import install_fluent_scrollbars
from ui.widgets.hover_row import profile_hover_row_rect
from ui.widgets.row_hover_motion import attach_row_hover_motion

GROUP_ROW_HEIGHT = 31
SECTION_ROW_HEIGHT = 26
STRATEGY_ROW_HEIGHT = 31
TILE_HEIGHT = 46
# Плитка широкая: название, способ словами и метка «в N пресетах» должны
# читаться целиком, а не обрываться многоточием.
TILE_MIN_WIDTH = 380
MAX_TILE_COLUMNS = 3


class StrategyListView(QListView):
    # Щёлкнули или нажали Enter на стратегии.
    strategy_chosen = pyqtSignal(str)
    # Заголовок группы просят свернуть или развернуть: (ключ группы, раскрыть).
    group_toggle_requested = pyqtSignal(str, bool)
    # Кнопка «ещё N» у стратегии с одноимёнными вариантами: ключ набора.
    twins_toggle_requested = pyqtSignal(str)
    # Правая кнопка или клавиша меню на стратегии: (стратегия, где открыть меню).
    menu_requested = pyqtSignal(str, QPoint)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._model = StrategyListModel(self)
        self.setModel(self._model)
        self._delegate = StrategyListDelegate(self)
        self.setItemDelegate(self._delegate)
        # Строки идут слева направо с переносом: заголовок занимает ряд
        # целиком, а плиток в ряд встаёт столько, сколько позволяет ширина.
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setSpacing(0)
        self.setUniformItemSizes(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.setMinimumHeight(420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setStyleSheet(
            "QListView { background: rgba(255, 255, 255, 0.035); border: none; border-radius: 6px; outline: none; padding: 4px 0; }"
            "QListView::item { border: none; padding: 0; }"
            "QListView::item:selected { background: transparent; }"
            "QListView::item:hover { background: transparent; }"
        )
        self._scrollbars = install_fluent_scrollbars(self, vertical=True, horizontal=False)
        # При выборе другой стратегии полоска акцента переезжает к новой строке.
        attach_active_row_motion(self, ACTIVE_ROLE, row_rect_fn=self.row_paint_rect)
        attach_row_hover_motion(self, row_filter=self._row_hover_allowed)

    # ------------------------------------------------------------------
    # Строки
    # ------------------------------------------------------------------
    def list_model(self) -> StrategyListModel:
        return self._model

    def row_for_index(self, index):
        return index.data(ROW_ROLE) if index is not None and index.isValid() else None

    def current_row(self):
        return self.row_for_index(self.currentIndex())

    def set_current_key(self, key: str, *, scroll: bool = False) -> bool:
        position = self._model.row_of_key(key)
        if position < 0:
            return False
        index = self._model.index(position, 0)
        self.setCurrentIndex(index)
        if scroll:
            self.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)
        return True

    def _row_hover_allowed(self, index) -> bool:
        row = self.row_for_index(index)
        return row is not None and row.kind != ROW_SECTION

    # ------------------------------------------------------------------
    # Плитки в несколько столбцов
    # ------------------------------------------------------------------
    def _layout_width(self) -> int:
        """Ширина, в которую Qt раскладывает ряд плиток."""
        width = min(self.viewport().width(), self.maximumViewportSize().width())
        if self.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
            # Под свою полосу прокрутки Qt оставляет место, даже пока её нет.
            width -= self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        # Плитка, вставшая вплотную к краю, переносится на следующий ряд.
        return max(0, width - 1)

    def column_count(self) -> int:
        return max(1, min(MAX_TILE_COLUMNS, self._layout_width() // TILE_MIN_WIDTH))

    def row_size(self, kind: str) -> QSize:
        """Размер строки: заголовки идут на всю ширину, плитки делят её."""
        width = self.viewport().width()
        if kind == ROW_GROUP:
            return QSize(width, GROUP_ROW_HEIGHT)
        if kind == ROW_SECTION:
            return QSize(width, SECTION_ROW_HEIGHT)
        columns = self.column_count()
        if columns <= 1:
            return QSize(width, STRATEGY_ROW_HEIGHT)
        return QSize(self._layout_width() // columns, TILE_HEIGHT)

    def row_paint_rect(self, rect: QRect) -> QRect:
        """Где в ячейке рисуется подложка строки или плитки."""
        columns = self.column_count()
        if columns <= 1 or rect.width() <= 0 or rect.width() >= self.viewport().width():
            return profile_hover_row_rect(rect)
        column = round(rect.left() / rect.width())
        return rect.adjusted(8 if column <= 0 else 4, 3, -8 if column >= columns - 1 else -4, -3)

    def paintEvent(self, event):  # noqa: N802
        # Цвета темы, шрифты и их мерки одинаковы у всех строк кадра: они
        # собираются один раз здесь, а не заново для каждой плитки.
        self._delegate.begin_pass()
        try:
            super().paintEvent(event)
        finally:
            self._delegate.end_pass()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        # Размер плитки зависит от ширины списка: раскладка пересчитывается.
        self.scheduleDelayedItemsLayout()

    # ------------------------------------------------------------------
    # Мышь
    # ------------------------------------------------------------------
    def _twin_key_at(self, index, pos: QPoint) -> str:
        row = self.row_for_index(index)
        if row is None or row.kind != ROW_STRATEGY or row.twin_count < 2:
            return ""
        chip = self._delegate.twin_chip_rect(self.visualRect(index), row, self.font())
        return row.item.twin_key if chip.contains(pos) else ""

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        index = self.indexAt(event.position().toPoint())
        row = self.row_for_index(index)
        super().mouseReleaseEvent(event)
        if row is None:
            return
        if row.kind == ROW_GROUP:
            self.group_toggle_requested.emit(row.group_key, not row.expanded)
        elif row.kind == ROW_STRATEGY:
            twin_key = self._twin_key_at(index, event.position().toPoint())
            if twin_key:
                self.twins_toggle_requested.emit(twin_key)
            else:
                self.strategy_chosen.emit(row.strategy_id)

    def contextMenuEvent(self, event):  # noqa: N802
        index = self.indexAt(event.pos())
        row = self.row_for_index(index)
        if row is not None and row.kind == ROW_STRATEGY:
            self.setCurrentIndex(index)
            self.menu_requested.emit(row.strategy_id, event.globalPos())
        event.accept()

    # ------------------------------------------------------------------
    # Клавиатура
    # ------------------------------------------------------------------
    def keyPressEvent(self, event):  # noqa: N802
        row = self.current_row()
        key = event.key()
        if row is not None and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if row.kind == ROW_GROUP:
                self.group_toggle_requested.emit(row.group_key, not row.expanded)
            elif row.kind == ROW_STRATEGY:
                self.strategy_chosen.emit(row.strategy_id)
            event.accept()
            return
        if row is not None and row.kind == ROW_GROUP and key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            # Как в дереве: вправо раскрывает группу, влево сворачивает.
            wanted = key == Qt.Key.Key_Right
            if row.expanded != wanted:
                self.group_toggle_requested.emit(row.group_key, wanted)
            event.accept()
            return
        if row is not None and row.kind == ROW_STRATEGY and key == Qt.Key.Key_Menu:
            rect = self.visualRect(self.currentIndex())
            self.menu_requested.emit(row.strategy_id, self.viewport().mapToGlobal(rect.center()))
            event.accept()
            return
        super().keyPressEvent(event)

    def move_from_search(self, key: int) -> None:
        """Стрелки из строки поиска двигают выбор по списку, фокус остаётся в поиске."""
        count = self._model.rowCount()
        if count <= 0:
            return
        selectable = [position for position in range(count) if self._model.row_at(position).selectable]
        if not selectable:
            return
        current = self.currentIndex().row()
        if key == int(Qt.Key.Key_Home) or current not in selectable:
            target = selectable[0]
        elif key == int(Qt.Key.Key_End):
            target = selectable[-1]
        else:
            step = 1 if key in (int(Qt.Key.Key_Down), int(Qt.Key.Key_PageDown)) else -1
            jump = 10 if key in (int(Qt.Key.Key_PageDown), int(Qt.Key.Key_PageUp)) else 1
            place = max(0, min(len(selectable) - 1, selectable.index(current) + step * jump))
            target = selectable[place]
        index = self._model.index(target, 0)
        self.setCurrentIndex(index)
        self.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)
