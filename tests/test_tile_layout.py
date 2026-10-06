from __future__ import annotations

import unittest

from PyQt6.QtCore import QPoint

from ui.widgets.tile_layout import (
    TILE_ROW_HEADER,
    TILE_ROW_ITEM,
    TILE_ROW_WIDE,
    TileMetrics,
    build_tile_geometry,
    tile_column_count,
)


METRICS = TileMetrics(min_column_width=460, max_columns=3, gap=12, card_padding_top=4, card_padding_bottom=6)


def _rows(*group_sizes: int) -> tuple[list[str], list[int]]:
    """Группы «заголовок + N строк»: заголовок высотой 36, строка 32."""
    kinds: list[str] = []
    heights: list[int] = []
    for size in group_sizes:
        kinds.append(TILE_ROW_HEADER)
        heights.append(36)
        kinds.extend([TILE_ROW_ITEM] * size)
        heights.extend([32] * size)
    return kinds, heights


class TileColumnCountTests(unittest.TestCase):
    def test_column_count_follows_width_and_is_capped(self) -> None:
        self.assertEqual(tile_column_count(300, METRICS), 1)
        self.assertEqual(tile_column_count(931, METRICS), 1)
        # Два столбца по 460 и промежуток 12 помещаются ровно в 932.
        self.assertEqual(tile_column_count(932, METRICS), 2)
        self.assertEqual(tile_column_count(1150, METRICS), 2)
        self.assertEqual(tile_column_count(1404, METRICS), 3)
        self.assertEqual(tile_column_count(4000, METRICS), 3)


class TileGeometryTests(unittest.TestCase):
    def test_single_column_stacks_cards_with_gap(self) -> None:
        kinds, heights = _rows(2, 1)
        geometry = build_tile_geometry(kinds, heights, 600, METRICS)

        self.assertEqual(geometry.column_count, 1)
        self.assertEqual(len(geometry.cards), 2)
        first, second = geometry.cards
        self.assertEqual(first.rect.top(), 0)
        self.assertEqual(first.rect.height(), 4 + 36 + 32 * 2 + 6)
        self.assertEqual(first.rect.width(), 600)
        self.assertEqual(second.rect.top(), first.rect.bottom() + 1 + 12)
        self.assertEqual(geometry.content_height, second.rect.bottom() + 1)

    def test_rows_are_stacked_inside_their_card(self) -> None:
        kinds, heights = _rows(2)
        geometry = build_tile_geometry(kinds, heights, 600, METRICS)

        header, first_row, second_row = geometry.row_rects
        self.assertEqual(header.top(), 4)
        self.assertEqual(header.height(), 36)
        self.assertEqual(first_row.top(), header.bottom() + 1)
        self.assertEqual(second_row.top(), first_row.bottom() + 1)
        self.assertEqual(geometry.row_cards, (0, 0, 0))
        self.assertTrue(geometry.cards[0].rect.contains(second_row))

    def test_next_card_goes_to_the_shortest_column(self) -> None:
        # Высокая плитка слева; низкие уходят направо одна под другую, пока
        # правый столбец остаётся короче левого.
        kinds, heights = _rows(6, 1, 1, 1)
        geometry = build_tile_geometry(kinds, heights, 1000, METRICS)

        self.assertEqual(geometry.column_count, 2)
        self.assertEqual([card.column for card in geometry.cards], [0, 1, 1, 1])
        column_width = geometry.cards[0].rect.width()
        self.assertEqual(column_width, (1000 - 12) // 2)
        self.assertEqual(geometry.cards[1].rect.left(), column_width + 12)
        self.assertEqual(geometry.cards[1].rect.top(), 0)
        self.assertEqual(geometry.cards[2].rect.top(), geometry.cards[1].rect.bottom() + 1 + 12)

        for index, card in enumerate(geometry.cards):
            for other in geometry.cards[index + 1 :]:
                self.assertFalse(card.rect.intersects(other.rect))

    def test_equal_columns_prefer_the_left_one(self) -> None:
        kinds, heights = _rows(1, 1, 1)
        geometry = build_tile_geometry(kinds, heights, 1000, METRICS)

        self.assertEqual([card.column for card in geometry.cards], [0, 1, 0])

    def test_wide_row_spans_all_columns_and_restarts_them(self) -> None:
        kinds, heights = _rows(3, 1)
        kinds.append(TILE_ROW_WIDE)
        heights.append(64)
        tail_kinds, tail_heights = _rows(1)
        geometry = build_tile_geometry(kinds + tail_kinds, heights + tail_heights, 1000, METRICS)

        wide_row = len(kinds) - 1
        wide_rect = geometry.row_rects[wide_row]
        tallest_bottom = max(card.rect.bottom() for card in geometry.cards[:2])
        self.assertEqual(wide_rect.left(), 0)
        self.assertEqual(wide_rect.width(), 1000)
        self.assertEqual(wide_rect.top(), tallest_bottom + 1 + 12)
        self.assertEqual(geometry.row_cards[wide_row], -1)
        self.assertEqual(geometry.cards[2].column, 0)
        self.assertEqual(geometry.cards[2].rect.top(), wide_rect.bottom() + 1 + 12)

    def test_item_without_header_still_gets_a_card(self) -> None:
        geometry = build_tile_geometry([TILE_ROW_ITEM, TILE_ROW_ITEM], [32, 32], 600, METRICS)

        self.assertEqual(len(geometry.cards), 1)
        self.assertEqual((geometry.cards[0].first_row, geometry.cards[0].last_row), (0, 1))

    def test_empty_model_has_no_content(self) -> None:
        geometry = build_tile_geometry([], [], 600, METRICS)

        self.assertEqual(geometry.content_height, 0)
        self.assertEqual(geometry.cards, ())
        self.assertEqual(geometry.row_at(QPoint(10, 10)), -1)


class TileGeometryLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        kinds, heights = _rows(3, 2)
        self.geometry = build_tile_geometry(kinds, heights, 1000, METRICS)

    def test_row_at_finds_row_in_either_column(self) -> None:
        for row, rect in enumerate(self.geometry.row_rects):
            self.assertEqual(self.geometry.row_at(rect.center()), row)
        # Нижнее поле плитки строкой не считается.
        card = self.geometry.cards[0]
        self.assertEqual(self.geometry.row_at(QPoint(card.rect.left() + 20, card.rect.bottom() - 1)), -1)

    def test_card_padding_resolves_to_nearest_row_of_that_card(self) -> None:
        card = self.geometry.cards[1]
        below_last_row = QPoint(card.rect.center().x(), card.rect.bottom() - 1)

        card_index = self.geometry.card_at(below_last_row)
        self.assertEqual(card_index, 1)
        self.assertEqual(self.geometry.nearest_row_in_card(card_index, below_last_row.y()), card.last_row)

    def test_gap_between_cards_needs_slack(self) -> None:
        left, right = self.geometry.cards
        in_gap = QPoint(left.rect.right() + 3, 20)

        self.assertEqual(self.geometry.card_at(in_gap), -1)
        self.assertEqual(self.geometry.card_at(in_gap, slack=6), 0)
        self.assertEqual(self.geometry.card_at(QPoint(right.rect.left() - 3, 20), slack=6), 1)

    def test_neighbor_row_keeps_height_and_stops_at_edges(self) -> None:
        # Вторая строка левой плитки стоит на высоте первой строки правой.
        self.assertEqual(self.geometry.neighbor_row(1, 1), 5)
        self.assertEqual(self.geometry.neighbor_row(5, -1), 1)
        self.assertEqual(self.geometry.neighbor_row(1, -1), -1)
        self.assertEqual(self.geometry.neighbor_row(5, 1), -1)
        # Ниже правой плитки строк нет — берётся её последняя строка.
        self.assertEqual(self.geometry.neighbor_row(3, 1), 6)


if __name__ == "__main__":
    unittest.main()
