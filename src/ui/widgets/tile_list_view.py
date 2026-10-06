"""Раскладка строк QListView плитками.

Обычный QListView кладёт строки в один столбец. Эта примесь оставляет модель,
делегат, клавиатуру и перетаскивание как есть и подменяет только ответы на
вопросы «где лежит строка» и «какая строка под точкой»: группы строк встают
плитками в несколько столбцов (геометрию считает ui/widgets/tile_layout.py).
Пока режим плиток выключен, вид ведёт себя как обычный список.
"""

from __future__ import annotations

from PyQt6.QtCore import QModelIndex, QPoint, QRect, Qt
from PyQt6.QtGui import QCursor, QPainter
from PyQt6.QtWidgets import QAbstractItemView, QStyle, QStyleOptionViewItem

from ui.theme import get_theme_tokens, to_qcolor
from ui.widgets.tile_layout import TILE_ROW_ITEM, TileGeometry, TileMetrics, build_tile_geometry


TILE_CARD_RADIUS = 10
TILE_SCROLL_STEP = 32
# Сколько воздуха оставить над и под строкой, к которой прокрутили клавишами.
TILE_SCROLL_MARGIN = 6

_LAYOUT_ABOUT_SIGNAL_NAMES = (
    "modelAboutToBeReset",
    "layoutAboutToBeChanged",
    "rowsAboutToBeInserted",
    "rowsAboutToBeRemoved",
    "rowsAboutToBeMoved",
)
_LAYOUT_SIGNAL_NAMES = (
    "modelReset",
    "layoutChanged",
    "rowsInserted",
    "rowsRemoved",
    "rowsMoved",
    "dataChanged",
)


class TileListViewMixin:
    """Ставится первым в списке базовых классов перед QListView."""

    tile_metrics = TileMetrics()

    # Значения по умолчанию лежат в классе: Qt вызывает visualRect() и
    # updateGeometries() ещё из конструктора QListView, до любого __init__.
    _tile_enabled = False
    _tile_geometry_cache: TileGeometry | None = None
    _tile_model = None
    _tile_model_changing = False

    def set_tile_layout_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._tile_enabled:
            return
        self._tile_enabled = enabled
        self._invalidate_tile_geometry()

    def tile_layout_enabled(self) -> bool:
        return bool(self._tile_enabled)

    def tile_row_kind(self, index: QModelIndex) -> str:
        """Роль строки в раскладке: заголовок плитки, её строка или строка на всю ширину."""
        _ = index
        return TILE_ROW_ITEM

    def setModel(self, model) -> None:  # noqa: N802
        previous = self._tile_model
        if previous is not None and previous is not model:
            for name, handler in self._tile_model_handlers():
                try:
                    getattr(previous, name).disconnect(handler)
                except (TypeError, RuntimeError):
                    pass
        self._tile_model = model
        self._tile_model_changing = False
        if model is not None and previous is not model:
            # Подключаемся ДО super().setModel(): там создаётся модель выбора,
            # и наш обработчик «строки сейчас изменятся» должен сработать
            # раньше, чем она переставит текущую строку.
            for name, handler in self._tile_model_handlers():
                getattr(model, name).connect(handler)
        super().setModel(model)
        self._invalidate_tile_geometry()

    def _tile_model_handlers(self):
        return (
            *((name, self._on_tile_model_about_to_change) for name in _LAYOUT_ABOUT_SIGNAL_NAMES),
            *((name, self._on_tile_model_changed) for name in _LAYOUT_SIGNAL_NAMES),
        )

    def _on_tile_model_about_to_change(self, *_args) -> None:
        self._tile_model_changing = True

    def _on_tile_model_changed(self, *_args) -> None:
        self._tile_model_changing = False
        self._invalidate_tile_geometry()

    def _invalidate_tile_geometry(self) -> None:
        self._tile_geometry_cache = None
        if not self._tile_enabled:
            return
        # Отложенная раскладка Qt закончится вызовом updateGeometries(),
        # где пересчитывается диапазон прокрутки.
        self.scheduleDelayedItemsLayout()
        self.viewport().update()

    def tile_geometry(self) -> TileGeometry:
        width = max(0, self.viewport().width())
        cache = self._tile_geometry_cache
        if cache is not None and cache.width == width:
            return cache
        model = self.model()
        count = model.rowCount() if model is not None else 0
        delegate = self.itemDelegate()
        option = QStyleOptionViewItem()
        kinds: list[str] = []
        heights: list[int] = []
        for row in range(count):
            index = model.index(row, 0)
            kinds.append(self.tile_row_kind(index))
            heights.append(delegate.sizeHint(option, index).height() if delegate is not None else 0)
        cache = build_tile_geometry(kinds, heights, width, self.tile_metrics)
        self._tile_geometry_cache = cache
        return cache

    # ------------------------------------------------------------------
    # Геометрия, которую Qt и остальной код спрашивают у вида
    # ------------------------------------------------------------------

    def verticalOffset(self) -> int:  # noqa: N802
        if not self._tile_enabled:
            return super().verticalOffset()
        return int(self.verticalScrollBar().value())

    def horizontalOffset(self) -> int:  # noqa: N802
        if not self._tile_enabled:
            return super().horizontalOffset()
        return 0

    def visualRect(self, index: QModelIndex) -> QRect:  # noqa: N802
        if not self._tile_enabled:
            return super().visualRect(index)
        if not index.isValid():
            return QRect()
        rect = self.tile_geometry().row_rect(index.row())
        if not rect.isValid():
            return QRect()
        return rect.translated(0, -self.verticalOffset())

    def indexAt(self, point: QPoint) -> QModelIndex:  # noqa: N802
        if not self._tile_enabled:
            return super().indexAt(point)
        return self._tile_index_for_row(self.tile_geometry().row_at(self._tile_content_point(point)))

    def scrollTo(self, index: QModelIndex, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:  # noqa: N802
        if not self._tile_enabled:
            super().scrollTo(index, hint)
            return
        if not index.isValid() or self._tile_model_changing:
            # Пока модель удаляет или двигает строки, Qt переставляет текущую
            # строку и сам зовёт scrollTo(). Список при этом должен остаться
            # на месте, как и обычный QListView.
            return
        geometry = self.tile_geometry()
        rect = geometry.row_rect(index.row())
        if not rect.isValid():
            return
        self._apply_tile_scroll_range(geometry)
        scrollbar = self.verticalScrollBar()
        viewport_height = self.viewport().height()
        value = scrollbar.value()
        if hint == QAbstractItemView.ScrollHint.PositionAtTop:
            value = rect.top()
        elif hint == QAbstractItemView.ScrollHint.PositionAtBottom:
            value = rect.bottom() + 1 - viewport_height
        elif hint == QAbstractItemView.ScrollHint.PositionAtCenter:
            value = rect.center().y() - viewport_height // 2
        elif rect.top() - TILE_SCROLL_MARGIN < value:
            value = rect.top() - TILE_SCROLL_MARGIN
        elif rect.bottom() + 1 + TILE_SCROLL_MARGIN > value + viewport_height:
            value = rect.bottom() + 1 + TILE_SCROLL_MARGIN - viewport_height
        scrollbar.setValue(value)

    def updateGeometries(self) -> None:  # noqa: N802
        if not self._tile_enabled:
            super().updateGeometries()
            return
        self._apply_tile_scroll_range(self.tile_geometry())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._tile_enabled:
            # tile_geometry() сама пересчитывается под новую ширину.
            self._apply_tile_scroll_range(self.tile_geometry())

    def _apply_tile_scroll_range(self, geometry: TileGeometry) -> None:
        viewport_height = max(0, self.viewport().height())
        scrollbar = self.verticalScrollBar()
        scrollbar.setPageStep(viewport_height)
        scrollbar.setSingleStep(TILE_SCROLL_STEP)
        scrollbar.setRange(0, max(0, geometry.content_height - viewport_height))
        self.horizontalScrollBar().setRange(0, 0)

    # ------------------------------------------------------------------
    # Помощники для перетаскивания, клавиатуры и запоминания прокрутки
    # ------------------------------------------------------------------

    def tile_index_near(self, point: QPoint) -> QModelIndex:
        """Строка под точкой; на полях плитки и между плитками — ближайшая строка плитки.

        Нужна перетаскиванию: иначе бросок на несколько пикселей мимо строки
        считался бы броском «в пустоту», то есть в самый конец списка.
        """
        index = self.indexAt(point)
        if index.isValid() or not self._tile_enabled:
            return index
        geometry = self.tile_geometry()
        content_point = self._tile_content_point(point)
        card_index = geometry.card_at(content_point, slack=max(1, int(self.tile_metrics.gap) // 2))
        return self._tile_index_for_row(geometry.nearest_row_in_card(card_index, content_point.y()))

    def tile_neighbor_index(self, index: QModelIndex, direction: int) -> QModelIndex:
        """Строка в соседнем столбце на той же высоте."""
        if not self._tile_enabled or not index.isValid():
            return QModelIndex()
        return self._tile_index_for_row(self.tile_geometry().neighbor_row(index.row(), direction))

    def top_visible_index(self) -> QModelIndex:
        """Самая верхняя видимая строка (в плитках — в любом из столбцов)."""
        if not self._tile_enabled:
            return self.indexAt(QPoint(0, 0))
        offset = self.verticalOffset()
        best_row = -1
        best_top = 0
        for row, rect in enumerate(self.tile_geometry().row_rects):
            if not rect.isValid() or rect.bottom() < offset:
                continue
            if best_row < 0 or rect.top() < best_top:
                best_row = row
                best_top = rect.top()
        return self._tile_index_for_row(best_row)

    def _tile_content_point(self, point: QPoint) -> QPoint:
        return QPoint(point.x(), point.y() + self.verticalOffset())

    def _tile_index_for_row(self, row: int) -> QModelIndex:
        model = self.model()
        if model is None or row < 0 or row >= model.rowCount():
            return QModelIndex()
        return model.index(row, 0)

    # ------------------------------------------------------------------
    # Отрисовка
    # ------------------------------------------------------------------

    def paint_tile_card(self, painter: QPainter, rect: QRect) -> None:
        """Подложка плитки. Строки поверх неё рисует делегат."""
        tokens = get_theme_tokens()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(to_qcolor(tokens.surface_bg, "#1f1f1f"))
        painter.drawRoundedRect(rect, TILE_CARD_RADIUS, TILE_CARD_RADIUS)
        painter.restore()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._tile_enabled:
            super().paintEvent(event)
            return
        model = self.model()
        if model is None:
            return
        geometry = self.tile_geometry()
        offset = self.verticalOffset()
        clip = event.rect()
        count = model.rowCount()
        hover_row = self._tile_hover_row()
        current = self.currentIndex()
        focus_row = current.row() if current.isValid() and (self.hasFocus() or self.viewport().hasFocus()) else -1
        base_option = QStyleOptionViewItem()
        self.initViewItemOption(base_option)
        base_state = base_option.state & ~(
            QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_HasFocus | QStyle.StateFlag.State_Selected
        )

        painter = QPainter(self.viewport())
        try:
            for card in geometry.cards:
                card_rect = card.rect.translated(0, -offset)
                if card_rect.intersects(clip):
                    self.paint_tile_card(painter, card_rect)
            for row, content_rect in enumerate(geometry.row_rects):
                if row >= count:
                    break
                rect = content_rect.translated(0, -offset)
                if not rect.isValid() or not rect.intersects(clip):
                    continue
                index = model.index(row, 0)
                option = QStyleOptionViewItem(base_option)
                option.rect = rect
                state = base_state
                if row == hover_row:
                    state |= QStyle.StateFlag.State_MouseOver
                if row == focus_row:
                    state |= QStyle.StateFlag.State_HasFocus
                option.state = state
                self.itemDelegateForIndex(index).paint(painter, option, index)
        finally:
            painter.end()

    def _tile_hover_row(self) -> int:
        viewport = self.viewport()
        if not viewport.underMouse():
            return -1
        index = self.indexAt(viewport.mapFromGlobal(QCursor.pos()))
        return index.row() if index.isValid() else -1


__all__ = ["TILE_CARD_RADIUS", "TileListViewMixin"]
