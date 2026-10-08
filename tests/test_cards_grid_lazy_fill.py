"""Сетка карточек итога создаёт только то, что видно; остальное — позже.

Итог большой проверки — это десятки карточек, а в окно попадает один-два
ряда (см. ui.block_build и CardsGrid в blockcheck.ui.result_cards).
"""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget

import ui.block_build as block_build
import ui.widgets.stagger_float_in as float_module
from blockcheck.ui.result_cards import CardsGrid, ResultCard
from blockcheck.ui.result_cards_model import Card
from ui.block_build import BlockBuildQueue, DeferredFill


def _pump(seconds: float = 0.0) -> None:
    deadline = time.monotonic() + seconds
    QApplication.processEvents()
    while time.monotonic() < deadline:
        QApplication.processEvents()


def _cards(count: int) -> list[Card]:
    return [
        Card(key=f"site:{index}", icon="fa5s.globe", title=f"Сайт {index}", level="ok", status="работает", site=True)
        for index in range(count)
    ]


class _QueueCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.busy = True  # фон «занят»: в паузах ничего не достраивается
        self.queue = BlockBuildQueue(
            idle_ms=lambda: 10_000,
            is_background_busy=lambda: self.busy,
            mouse_pressed=lambda: False,
        )
        self.addCleanup(self.queue.deleteLater)
        patcher = mock.patch.object(block_build, "block_build_queue", return_value=self.queue)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (
            ("BACKGROUND_START_DELAY_MS", 0),
            ("BACKGROUND_GAP_MS", 2),
            ("BACKGROUND_POLL_MS", 2),
        ):
            patcher = mock.patch.object(block_build, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(float_module, "are_live_animations_enabled", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _scroll_with(self, grid: QWidget, *, above: int) -> QScrollArea:
        """Окно 600x400, над сеткой — содержимое высотой ``above``."""
        scroll = QScrollArea()
        self.addCleanup(scroll.deleteLater)
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        spacer = QLabel("итог")
        spacer.setFixedHeight(above)
        layout.addWidget(spacer)
        layout.addWidget(grid)
        layout.addStretch(1)
        scroll.setWidget(content)
        scroll.resize(600, 400)
        scroll.show()
        _pump(0.02)
        return scroll


class CardsGridLazyFillTests(_QueueCase):
    def test_grid_below_the_window_edge_creates_no_cards_but_reserves_its_place(self) -> None:
        grid = CardsGrid(250)
        scroll = self._scroll_with(grid, above=900)
        grid.show_cards(_cards(40), animate=False)
        _pump(0.05)

        self.assertEqual(grid.findChildren(ResultCard), [], "невидимая сетка не создаёт ни одной карточки")
        self.assertGreater(grid.height(), 1000, "место под карточки занято сразу")
        self.assertGreater(scroll.verticalScrollBar().maximum(), 1500)

    def test_scrolling_to_the_grid_creates_one_chunk_not_everything(self) -> None:
        grid = CardsGrid(250)
        scroll = self._scroll_with(grid, above=900)
        grid.show_cards(_cards(40), animate=False)
        _pump(0.05)

        scroll.verticalScrollBar().setValue(700)
        _pump(0.05)
        built = len(grid.findChildren(ResultCard))
        self.assertEqual(built, CardsGrid.CHUNK, "в окно попало начало сетки — создана первая порция")

        # Долистали до места карточек, которых ещё нет: создаётся следующая порция.
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        _pump(0.1)
        self.assertGreater(len(grid.findChildren(ResultCard)), built)

    def test_visible_grid_shows_the_first_chunk_at_once_and_the_rest_in_pauses(self) -> None:
        grid = CardsGrid(250)
        self._scroll_with(grid, above=20)
        grid.show_cards(_cards(40), animate=False)
        _pump(0.05)
        self.assertEqual(len(grid.findChildren(ResultCard)), CardsGrid.CHUNK)

        self.busy = False
        self.queue.page_shown()
        _pump(0.2)
        self.assertEqual(len(grid.findChildren(ResultCard)), 40)

    def test_asking_for_cards_builds_all_of_them(self) -> None:
        grid = CardsGrid(250)
        self._scroll_with(grid, above=900)
        grid.show_cards(_cards(40), animate=False)
        self.assertEqual([widget.card.key for widget in grid.cards()], [f"site:{index}" for index in range(40)])
        self.assertEqual(self.queue.pending_count(), 0)

    def test_height_matches_the_cards_once_everything_is_built(self) -> None:
        grid = CardsGrid(250)
        self._scroll_with(grid, above=900)
        grid.show_cards(_cards(30), animate=False)
        reserved = grid.height()
        widgets = grid.cards()
        _pump(0.02)
        self.assertEqual(grid.height(), max(widget.geometry().bottom() for widget in widgets) + 1)
        self.assertLess(abs(grid.height() - reserved) / reserved, 0.15, "место под сетку было близко к настоящему")

    def test_reserved_place_is_sane_for_a_real_report(self) -> None:
        # Место считается по данным карточек, без виджетов: точно — для
        # карточек со строками, примерно — для меток и точек хостингов
        # (их переносы зависят от ширины). Полосе прокрутки этого хватает:
        # настоящая высота встаёт, когда карточки созданы.
        from blockcheck.ui.onboarding_demo import demo_report
        from blockcheck.ui.result_cards_model import build_cards

        cards = build_cards(demo_report()) * 4
        for min_width in (250, 330):
            with self.subTest(min_width=min_width):
                grid = CardsGrid(min_width)
                self._scroll_with(grid, above=900)
                grid.show_cards(cards, animate=False)
                reserved = grid.height()
                grid.cards()
                _pump(0.02)
                self.assertGreater(reserved, grid.height() * 0.5, (reserved, grid.height()))
                self.assertLess(reserved, grid.height() * 1.5, (reserved, grid.height()))

    def test_new_result_replaces_cards_that_were_still_waiting(self) -> None:
        grid = CardsGrid(250)
        self._scroll_with(grid, above=900)
        grid.show_cards(_cards(40), animate=False)
        grid.show_cards(_cards(3), animate=False)
        self.assertEqual(len(grid.cards()), 3)

        grid.show_cards(_cards(40), animate=False)
        grid.clear()
        _pump(0.05)
        self.assertEqual(grid.findChildren(ResultCard), [])
        self.assertEqual(grid.height(), 0)
        self.assertEqual(self.queue.pending_count(), 0)

    def test_few_cards_are_shown_in_one_go(self) -> None:
        grid = CardsGrid(250)
        self._scroll_with(grid, above=20)
        grid.show_cards(_cards(5), animate=False)
        _pump(0.05)
        self.assertEqual(len(grid.findChildren(ResultCard)), 5)
        self.assertEqual(self.queue.pending_count(), 0)


class DeferredFillTests(_QueueCase):
    def test_visible_widget_is_filled_at_once(self) -> None:
        widget = QLabel("виден")
        self.addCleanup(widget.deleteLater)
        widget.resize(200, 50)
        widget.show()
        QApplication.processEvents()
        done: list[str] = []
        fill = DeferredFill(widget, "now")
        self.assertTrue(fill.schedule(lambda: done.append("filled")))
        self.assertEqual(done, ["filled"])
        self.assertTrue(fill.is_built())

    def test_hidden_widget_waits_and_keeps_only_the_latest_job(self) -> None:
        widget = QLabel("скрыт")
        self.addCleanup(widget.deleteLater)
        done: list[str] = []
        fill = DeferredFill(widget, "later")
        self.assertFalse(fill.schedule(lambda: done.append("old")))
        self.assertFalse(fill.schedule(lambda: done.append("new")))
        self.assertEqual(self.queue.pending_count(), 1)
        self.assertTrue(fill.ensure_built())
        self.assertFalse(fill.ensure_built())
        self.assertEqual(done, ["new"])
        self.assertEqual(self.queue.pending_count(), 0)

    def test_cancel_drops_the_job(self) -> None:
        widget = QLabel("скрыт")
        self.addCleanup(widget.deleteLater)
        done: list[str] = []
        fill = DeferredFill(widget, "cancelled")
        fill.schedule(lambda: done.append("ran"))
        fill.cancel()
        self.assertTrue(fill.is_built())
        self.assertEqual(self.queue.pending_count(), 0)
        widget.show()
        _pump(0.05)
        self.assertEqual(done, [])


if __name__ == "__main__":
    unittest.main()
