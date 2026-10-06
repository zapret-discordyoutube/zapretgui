"""Раскладка строк списка плитками.

Строки модели остаются обычным плоским списком: «заголовок группы, её строки,
следующий заголовок…». Здесь считается только геометрия: каждая группа
становится плиткой, плитки встают в несколько столбцов, новая плитка идёт в
самый короткий столбец. Модуль ничего не рисует и не знает про виджеты.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from PyQt6.QtCore import QPoint, QRect


TILE_ROW_HEADER = "header"
TILE_ROW_ITEM = "item"
TILE_ROW_WIDE = "wide"


@dataclass(frozen=True)
class TileMetrics:
    min_column_width: int = 460
    max_columns: int = 3
    gap: int = 12
    card_padding_top: int = 4
    card_padding_bottom: int = 6


@dataclass(frozen=True)
class TileCard:
    rect: QRect
    column: int
    first_row: int
    last_row: int


@dataclass(frozen=True)
class TileGeometry:
    """Готовая раскладка в координатах содержимого (без учёта прокрутки)."""

    width: int
    column_count: int
    row_rects: tuple[QRect, ...]
    row_cards: tuple[int, ...]
    cards: tuple[TileCard, ...]
    content_height: int

    def row_rect(self, row: int) -> QRect:
        if 0 <= int(row) < len(self.row_rects):
            return QRect(self.row_rects[int(row)])
        return QRect()

    def row_at(self, point: QPoint) -> int:
        for row, rect in enumerate(self.row_rects):
            if rect.contains(point):
                return row
        return -1

    def card_at(self, point: QPoint, *, slack: int = 0) -> int:
        """Плитка под точкой; slack расширяет плитку на промежуток между соседями."""
        slack = max(0, int(slack))
        for card_index, card in enumerate(self.cards):
            if card.rect.adjusted(-slack, -slack, slack, slack).contains(point):
                return card_index
        return -1

    def nearest_row_in_card(self, card_index: int, y: int) -> int:
        if not 0 <= int(card_index) < len(self.cards):
            return -1
        card = self.cards[int(card_index)]
        best_row = -1
        best_distance = -1
        for row in range(card.first_row, card.last_row + 1):
            rect = self.row_rects[row]
            if rect.top() <= y <= rect.bottom():
                return row
            distance = min(abs(y - rect.top()), abs(y - rect.bottom()))
            if best_row < 0 or distance < best_distance:
                best_row = row
                best_distance = distance
        return best_row

    def neighbor_row(self, row: int, direction: int) -> int:
        """Строка в соседнем столбце на той же высоте (для стрелок влево/вправо)."""
        if not 0 <= int(row) < len(self.row_rects):
            return -1
        card_index = self.row_cards[int(row)]
        if card_index < 0:
            return -1
        target_column = self.cards[card_index].column + (1 if int(direction) > 0 else -1)
        if not 0 <= target_column < self.column_count:
            return -1
        y = self.row_rects[int(row)].center().y()
        best_row = -1
        best_distance = -1
        for card in self.cards:
            if card.column != target_column:
                continue
            for candidate in range(card.first_row, card.last_row + 1):
                distance = abs(self.row_rects[candidate].center().y() - y)
                if best_row < 0 or distance < best_distance:
                    best_row = candidate
                    best_distance = distance
        return best_row


def tile_column_count(width: int, metrics: TileMetrics) -> int:
    gap = max(0, int(metrics.gap))
    column = max(1, int(metrics.min_column_width)) + gap
    return max(1, min(max(1, int(metrics.max_columns)), (max(0, int(width)) + gap) // column))


def build_tile_geometry(
    row_kinds: Sequence[str],
    row_heights: Sequence[int],
    width: int,
    metrics: TileMetrics | None = None,
) -> TileGeometry:
    metrics = metrics or TileMetrics()
    width = max(0, int(width))
    gap = max(0, int(metrics.gap))
    columns = tile_column_count(width, metrics)
    column_width = max(0, (width - gap * (columns - 1)) // columns)
    bottoms = [0] * columns

    count = min(len(row_kinds), len(row_heights))
    row_rects = [QRect() for _ in range(count)]
    row_cards = [-1] * count
    cards: list[TileCard] = []

    for wide, first, last in _row_groups(row_kinds, count):
        if wide:
            # Строка на всю ширину (например, «ничего не найдено») стоит под
            # всеми столбцами и начинает их заново с одной высоты.
            top = max(bottoms)
            if top > 0:
                top += gap
            height = max(0, int(row_heights[first]))
            row_rects[first] = QRect(0, top, width, height)
            bottoms = [top + height] * columns
            continue

        body_height = sum(max(0, int(row_heights[row])) for row in range(first, last + 1))
        card_height = int(metrics.card_padding_top) + body_height + int(metrics.card_padding_bottom)
        column = min(range(columns), key=lambda candidate: (bottoms[candidate], candidate))
        top = bottoms[column] + (gap if bottoms[column] > 0 else 0)
        left = column * (column_width + gap)
        y = top + int(metrics.card_padding_top)
        card_index = len(cards)
        for row in range(first, last + 1):
            height = max(0, int(row_heights[row]))
            row_rects[row] = QRect(left, y, column_width, height)
            row_cards[row] = card_index
            y += height
        cards.append(TileCard(QRect(left, top, column_width, card_height), column, first, last))
        bottoms[column] = top + card_height

    return TileGeometry(
        width=width,
        column_count=columns,
        row_rects=tuple(row_rects),
        row_cards=tuple(row_cards),
        cards=tuple(cards),
        content_height=max(bottoms) if bottoms else 0,
    )


def _row_groups(row_kinds: Sequence[str], count: int) -> list[tuple[bool, int, int]]:
    """Режет плоский список строк на плитки: (на всю ширину?, первая, последняя)."""
    groups: list[tuple[bool, int, int]] = []
    start = -1
    for row in range(count):
        kind = str(row_kinds[row] or "")
        if kind == TILE_ROW_WIDE:
            if start >= 0:
                groups.append((False, start, row - 1))
                start = -1
            groups.append((True, row, row))
            continue
        if kind == TILE_ROW_HEADER:
            if start >= 0:
                groups.append((False, start, row - 1))
            start = row
            continue
        if start < 0:
            # Строка без своего заголовка всё равно получает плитку.
            start = row
    if start >= 0:
        groups.append((False, start, count - 1))
    return groups


__all__ = [
    "TILE_ROW_HEADER",
    "TILE_ROW_ITEM",
    "TILE_ROW_WIDE",
    "TileCard",
    "TileGeometry",
    "TileMetrics",
    "build_tile_geometry",
    "tile_column_count",
]
