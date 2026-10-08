"""Блочная сборка страницы: видимое сразу, остальное позже (ui.block_build)."""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

import ui.block_build as block_build
import ui.pages.base_page as base_page_module
import ui.widgets.stagger_float_in as float_module
from ui.block_build import BlockBuildQueue, LazyBlock, ensure_page_blocks
from ui.pages.base_page import BasePage


def _pump(seconds: float = 0.0) -> None:
    deadline = time.monotonic() + seconds
    QApplication.processEvents()
    while time.monotonic() < deadline:
        QApplication.processEvents()


class _Card(QLabel):
    def __init__(self, text: str, height: int = 120) -> None:
        super().__init__(text)
        self.setFixedHeight(height)


class _Page(BasePage):
    """Страница: шапка, блок первого экрана и два блока ниже края окна."""

    def __init__(self) -> None:
        super().__init__("Страница", "Описание")
        self.built_order: list[str] = []
        self.real_heights: dict[str, int] = {}
        self.top_card = _Card("сразу", 200)
        self.add_widget(self.top_card)
        self.add_lazy_block("near", lambda: self._build("near", 300), estimated_height=300)
        self.add_lazy_block("middle", lambda: self._build("middle", 900), estimated_height=900)
        self.add_lazy_block("far", lambda: self._build("far", 700), estimated_height=700)
        self.footer = _Card("подвал", 40)
        self.add_widget(self.footer)

    def _build(self, name: str, height: int) -> None:
        self.built_order.append(name)
        # Настоящая высота блока может не совпасть с местом под него.
        card = _Card(name, self.real_heights.get(name, height))
        setattr(self, f"{name}_card", card)
        self.add_section_title(f"Раздел {name}")
        self.add_widget(card)


class _QueueCase(unittest.TestCase):
    """Своя очередь на каждый тест: без ожидания настоящих пауз."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.idle_ms = 10_000
        self.lane_busy = False
        self.mouse_down = False
        self.queue = BlockBuildQueue(
            idle_ms=lambda: self.idle_ms,
            is_background_busy=lambda: self.lane_busy,
            mouse_pressed=lambda: self.mouse_down,
        )
        self.addCleanup(self.queue.deleteLater)
        for module in (block_build, base_page_module):
            patcher = mock.patch.object(module, "block_build_queue", return_value=self.queue)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (
            ("BACKGROUND_START_DELAY_MS", 20),
            ("BACKGROUND_GAP_MS", 5),
            ("BACKGROUND_POLL_MS", 5),
        ):
            patcher = mock.patch.object(block_build, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(float_module, "are_live_animations_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _page(self, *, size=(700, 520)) -> _Page:
        page = _Page()
        self.addCleanup(page.deleteLater)
        page.resize(*size)
        return page


class LazyBlockPageTests(_QueueCase):
    def test_nothing_deferred_is_built_by_the_constructor(self) -> None:
        page = self._page()
        self.assertEqual(page.built_order, [])
        self.assertFalse(page.is_page_built())
        self.assertFalse(hasattr(page, "near_card"))
        self.assertEqual(self.queue.pending_count(), 3)

    def test_first_screen_block_is_ready_before_the_first_frame(self) -> None:
        page = self._page()
        # Долгая пауза до фоновой сборки: видим только то, что сделал показ.
        with mock.patch.object(block_build, "BACKGROUND_START_DELAY_MS", 60_000):
            page.show()
            # Ни одного оборота цикла событий: блок первого экрана уже собран,
            # без таймеров и без кадра с пустым местом.
            self.assertEqual(page.built_order, ["near"])
            _pump(0.05)
            self.assertEqual(page.built_order, ["near"], "блоки ниже края окна показ не собирает")

    def test_blocks_keep_their_place_in_the_page(self) -> None:
        page = self._page()
        page.ensure_all_blocks()
        page.show()
        _pump(0.05)

        tops = [
            page.top_card.mapTo(page.content, page.top_card.rect().topLeft()).y(),
            page.near_card.mapTo(page.content, page.near_card.rect().topLeft()).y(),
            page.middle_card.mapTo(page.content, page.middle_card.rect().topLeft()).y(),
            page.far_card.mapTo(page.content, page.far_card.rect().topLeft()).y(),
            page.footer.mapTo(page.content, page.footer.rect().topLeft()).y(),
        ]
        self.assertEqual(tops, sorted(tops))
        self.assertEqual(page.built_order, ["near", "middle", "far"])
        self.assertTrue(page.is_page_built())
        # Заголовок раздела тоже лёг в блок, а не в конец страницы.
        self.assertIs(page.near_card.parentWidget(), page.lazy_block("near"))

    def test_page_height_is_reserved_before_blocks_are_built(self) -> None:
        page = self._page()
        with mock.patch.object(block_build, "BACKGROUND_START_DELAY_MS", 60_000):
            page.show()
            _pump(0.05)
            reserved = page.verticalScrollBar().maximum()
            page.ensure_all_blocks()
            _pump(0.05)
            built = page.verticalScrollBar().maximum()
        self.assertGreater(reserved, 900, "место под несобранные блоки занято сразу")
        self.assertLess(abs(built - reserved), 150, "после сборки длина страницы почти не меняется")

    def test_scrolling_to_a_block_builds_it_and_it_floats_in(self) -> None:
        page = self._page()
        with mock.patch.object(block_build, "BACKGROUND_START_DELAY_MS", 60_000):
            page.show()
            _pump(0.05)
            self.assertNotIn("far", page.built_order)

            far = page.lazy_block("far")
            page.verticalScrollBar().setValue(far.geometry().top() - 100)
            _pump(0.08)
            self.assertIn("far", page.built_order)
            self.assertTrue(float_module.is_floating_in(page.far_card))
            self.assertGreater(page.far_card.height(), 0)
            _pump(1.0)
            self.assertFalse(float_module.is_floating_in(page.far_card))
            self.assertTrue(page.far_card.mask().isEmpty())

    def test_remaining_blocks_are_built_in_pauses_one_per_turn(self) -> None:
        page = self._page()
        page.show()
        _pump(0.01)
        self.assertEqual(page.built_order, ["near"])
        _pump(0.25)
        self.assertEqual(page.built_order, ["near", "middle", "far"])
        self.assertEqual(self.queue.pending_count(), 0)

    def test_background_blocks_wait_while_the_user_is_busy(self) -> None:
        page = self._page()
        self.mouse_down = True
        page.show()
        _pump(0.2)
        self.assertEqual(page.built_order, ["near"], "пока кнопка мыши зажата, блоки не собираются")

        self.mouse_down = False
        self.idle_ms = 0
        _pump(0.15)
        self.assertEqual(page.built_order, ["near"], "человек двигает мышь — ждём паузы")

        self.idle_ms = 10_000
        self.lane_busy = True
        _pump(0.15)
        self.assertEqual(page.built_order, ["near"], "рядом с занятой фоновой дорожкой сборка в разы дольше")

        self.lane_busy = False
        _pump(0.25)
        self.assertEqual(page.built_order, ["near", "middle", "far"])

    def test_background_blocks_do_not_wait_forever(self) -> None:
        page = self._page()
        self.idle_ms = 0
        with mock.patch.object(block_build, "BACKGROUND_WAIT_MAX_MS", 80):
            page.show()
            _pump(0.6)
        self.assertEqual(page.built_order, ["near", "middle", "far"])

    def test_blocks_of_a_hidden_page_wait_until_it_is_opened(self) -> None:
        shown = self._page()
        hidden = self._page()
        shown.show()
        _pump(0.3)
        self.assertTrue(shown.is_page_built())
        self.assertEqual(hidden.built_order, [])

    def test_warm_up_builds_the_first_screen_in_advance(self) -> None:
        page = self._page()
        page.build_first_screen_blocks()
        self.assertEqual(page.built_order, ["near"])

    def test_view_stays_in_place_when_a_block_above_is_built(self) -> None:
        page = self._page()
        with mock.patch.object(block_build, "BACKGROUND_START_DELAY_MS", 60_000):
            page.show()
            _pump(0.05)
            bar = page.verticalScrollBar()
            # Прыжок в конец: блок middle остался выше окна несобранным.
            bar.setValue(bar.maximum())
            _pump(0.08)
            self.assertNotIn("middle", page.built_order)
            before = page.footer.mapTo(page, page.footer.rect().topLeft()).y()

            # Настоящий блок оказался ниже, чем место под него.
            page.real_heights["middle"] = 1300
            page.ensure_block("middle")
            # Без единого оборота цикла событий: кадра со сдвигом быть не должно.
            self.assertEqual(page.footer.mapTo(page, page.footer.rect().topLeft()).y(), before)
            _pump(0.05)
            after = page.footer.mapTo(page, page.footer.rect().topLeft()).y()
        self.assertEqual(after, before, "то, на что человек смотрит, не должно уехать")

    def test_building_a_block_does_not_collapse_the_page_for_a_moment(self) -> None:
        # Виджеты, добавленные на видимую страницу, Qt показывает отложенно.
        # Без явного показа блок на один оборот становился нулевой высоты,
        # страница укорачивалась, и прокрутка прыгала вверх.
        page = self._page()
        with mock.patch.object(block_build, "BACKGROUND_START_DELAY_MS", 60_000):
            page.show()
            _pump(0.05)
            bar = page.verticalScrollBar()
            bar.setValue(bar.maximum())
            QApplication.processEvents()
            value = bar.value()
            page.ensure_all_blocks()
            _pump(0.05)
        self.assertGreaterEqual(bar.value(), value)

    def test_ensure_page_blocks_completes_the_page(self) -> None:
        page = self._page()
        ensure_page_blocks(page)
        self.assertTrue(page.is_page_built())
        # Страница без блоков и объект без такого метода — не ошибка.
        plain = BasePage("Обычная")
        self.addCleanup(plain.deleteLater)
        ensure_page_blocks(plain)
        ensure_page_blocks(object())
        self.assertTrue(plain.is_page_built())

    def test_block_name_must_be_unique(self) -> None:
        page = self._page()
        with self.assertRaises(ValueError):
            page.add_lazy_block("near", lambda: None, estimated_height=10)


class LazyBlockQueueTests(_QueueCase):
    def _block(self, parent: QWidget, name: str, built: list[str], *, fail: bool = False) -> LazyBlock:
        def build(block: LazyBlock) -> None:
            built.append(name)
            if fail:
                raise RuntimeError("строитель упал")
            block.layout().addWidget(_Card(name, 40))

        return LazyBlock(name, build, estimated_height=40, parent=parent)

    def test_failed_block_does_not_stop_the_queue_and_is_not_retried(self) -> None:
        host = QWidget()
        self.addCleanup(host.deleteLater)
        host.resize(300, 300)
        built: list[str] = []
        broken = self._block(host, "broken", built, fail=True)
        broken.setGeometry(0, 0, 300, 40)
        fine = self._block(host, "fine", built)
        fine.setGeometry(0, 50, 300, 40)
        host.show()
        self.queue.page_shown()
        with mock.patch("log.log.log"):
            _pump(0.25)

        self.assertEqual(sorted(built), ["broken", "fine"])
        self.assertTrue(broken.is_built(), "упавший блок не собирается заново на каждой перерисовке")

    def test_deleted_block_is_forgotten(self) -> None:
        host = QWidget()
        self.addCleanup(host.deleteLater)
        built: list[str] = []
        block = self._block(host, "gone", built)
        self.assertEqual(self.queue.pending_count(), 1)
        sip.delete(block)
        self.assertEqual(self.queue.pending_count(), 0)
        self.assertEqual(built, [])

    def test_block_is_built_only_once(self) -> None:
        host = QWidget()
        self.addCleanup(host.deleteLater)
        built: list[str] = []
        block = self._block(host, "once", built)
        self.assertTrue(block.ensure_built())
        self.assertFalse(block.ensure_built())
        self.queue.request_visible(block)
        _pump(0.05)
        self.assertEqual(built, ["once"])
        self.assertEqual(block.minimumHeight(), 0, "собранный блок больше не держит запасную высоту")


if __name__ == "__main__":
    unittest.main()
