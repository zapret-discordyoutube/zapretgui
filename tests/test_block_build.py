"""Блочная сборка страницы: видимое сразу, остальное позже (ui.block_build)."""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QLabel, QStackedWidget, QVBoxLayout, QWidget

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


class OpenBudgetTests(_QueueCase):
    """Первый экран собирается при показе, только пока страница укладывается во время на открытие."""

    def _clicked(self, page: _Page, *, spent_ms: float) -> None:
        # Так страницу открывает щелчок: счёт времени идёт от него.
        page._begin_page_open_metric("page", started_at=time.perf_counter() - spent_ms / 1000.0, first_show=True)

    def test_page_that_already_spent_its_time_shows_first_and_builds_after(self) -> None:
        page = self._page()
        self._clicked(page, spent_ms=block_build.OPEN_BUDGET_MS + 50)
        with mock.patch.object(block_build, "BACKGROUND_START_DELAY_MS", 60_000):
            page.show()
            self.assertEqual(page.built_order, [], "время вышло на сборке: показ блоки не собирает")
            _pump(0.1)
            # Место блока перерисовалось — он собран уже после первого кадра.
            self.assertEqual(page.built_order, ["near"])
            self.assertTrue(float_module.is_floating_in(page.near_card), "блок, собранный после показа, выплывает")

    def test_page_within_its_time_is_ready_before_the_first_frame(self) -> None:
        page = self._page()
        self._clicked(page, spent_ms=1)
        # Запас с избытком: тест не должен зависеть от скорости машины.
        with mock.patch.object(block_build, "BACKGROUND_START_DELAY_MS", 60_000), \
                mock.patch.object(base_page_module, "OPEN_BUDGET_MS", 1_000):
            page.show()
            self.assertEqual(page.built_order, ["near"])

    def test_time_is_counted_from_the_click(self) -> None:
        page = self._page()
        self.assertEqual(page._open_budget_left_ms(), block_build.OPEN_BUDGET_MS, "страницу не открывали щелчком")
        self._clicked(page, spent_ms=5)
        self.assertLess(page._open_budget_left_ms(), block_build.OPEN_BUDGET_MS - 4)
        # Щелчок был давно: страница собрана заранее, показ начинает с полного запаса.
        self._clicked(page, spent_ms=60_000)
        self.assertEqual(page._open_budget_left_ms(), block_build.OPEN_BUDGET_MS)

    def test_warm_up_has_no_time_limit(self) -> None:
        page = self._page()
        self._clicked(page, spent_ms=block_build.OPEN_BUDGET_MS + 50)
        page.build_first_screen_blocks()
        self.assertEqual(page.built_order, ["near"])


class _TabbedPage(BasePage):
    """Страница с вкладками: блоки лежат внутри вкладок, а не прямо на странице."""

    def __init__(self) -> None:
        super().__init__("Вкладки", "")
        self.built_order: list[str] = []
        self.stack = QStackedWidget(self.content)
        self.tab_layouts = []
        for _ in range(2):
            tab = QWidget(self.stack)
            layout = QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(12)
            self.stack.addWidget(tab)
            self.tab_layouts.append(layout)
        self.add_widget(self.stack)
        first, second = self.tab_layouts
        # Высокая карточка: первый блок ещё виден в окне, второй уже под его краем.
        first.addWidget(_Card("сразу", 300))
        self.add_lazy_block(
            "near", lambda: self._build("near"), estimated_height=150, provides=("near_card",), layout=first
        )
        self.add_lazy_block(
            "deep", lambda: self._build("deep"), estimated_height=900, provides=("deep_card",), layout=first
        )
        self.add_lazy_block(
            "far", lambda: self._build("far"), estimated_height=400, provides=("far_card",), layout=first
        )
        first.addStretch()
        self.add_lazy_block(
            "other", lambda: self._build("other"), estimated_height=150, provides=("other_card",), layout=second
        )
        second.addStretch()

    def _build(self, name: str) -> None:
        self.built_order.append(name)
        card = _Card(name, 150)
        setattr(self, f"{name}_card", card)
        self.add_widget(card)


class NestedBlockTests(_QueueCase):
    def _tabbed(self) -> _TabbedPage:
        page = _TabbedPage()
        self.addCleanup(page.deleteLater)
        page.resize(700, 520)
        return page

    def test_block_inside_the_open_tab_belongs_to_the_first_screen(self) -> None:
        page = self._tabbed()
        with mock.patch.object(block_build, "BACKGROUND_START_DELAY_MS", 60_000):
            page.show()
            # Блок под краем окна и блок скрытой вкладки показ не трогает.
            self.assertEqual(page.built_order, ["near"])
            self.assertIs(page.near_card.parentWidget(), page.lazy_block("near"))
            self.assertIs(page.lazy_block("near").parentWidget(), page.stack.widget(0))

    def test_block_of_a_hidden_tab_is_built_when_the_tab_is_opened(self) -> None:
        page = self._tabbed()
        with mock.patch.object(block_build, "BACKGROUND_START_DELAY_MS", 60_000):
            page.show()
            _pump(0.05)
            self.assertNotIn("other", page.built_order)
            page.stack.setCurrentIndex(1)
            _pump(0.1)
            self.assertIn("other", page.built_order)

    def test_hidden_tab_blocks_are_not_built_in_pauses(self) -> None:
        page = self._tabbed()
        page.show()
        _pump(0.3)
        self.assertEqual(page.built_order, ["near", "deep", "far"])


class BlockAttributeTests(_QueueCase):
    """Обращение к виджету несобранного блока достраивает блок."""

    def _tabbed(self) -> _TabbedPage:
        page = _TabbedPage()
        self.addCleanup(page.deleteLater)
        page.resize(700, 520)
        return page

    def test_reading_a_block_widget_builds_the_block(self) -> None:
        page = self._tabbed()
        self.assertEqual(page.built_order, [])
        self.assertEqual(page.deep_card.text(), "deep")
        self.assertEqual(page.built_order, ["deep"], "собран только нужный блок")
        self.assertEqual(page._forced_blocks, [("deep", "deep_card")])

    def test_soft_lookup_does_not_build_anything(self) -> None:
        page = self._tabbed()
        # Так код страницы спрашивает «собран ли уже виджет».
        self.assertIsNone(page.__dict__.get("deep_card"))
        self.assertIsNone(getattr(page, "no_such_widget", None))
        self.assertEqual(page.built_order, [])
        self.assertEqual(page._forced_blocks, [])

    def test_missing_attribute_is_still_an_error(self) -> None:
        page = self._tabbed()
        with self.assertRaises(AttributeError):
            page.no_such_widget  # noqa: B018

    def test_builder_reading_its_own_unset_widget_gets_an_error_not_a_loop(self) -> None:
        page = self._tabbed()
        seen: list[str] = []

        def build() -> None:
            try:
                page.loop_card  # noqa: B018
            except AttributeError:
                seen.append("error")
            page.loop_card = _Card("loop")
            page.add_widget(page.loop_card)

        page.add_lazy_block("loop", build, estimated_height=10, provides=("loop_card",))
        self.assertEqual(page.loop_card.text(), "loop")
        self.assertEqual(seen, ["error"])

    def test_page_without_blocks_is_not_affected(self) -> None:
        plain = BasePage("Обычная", "")
        self.addCleanup(plain.deleteLater)
        self.assertIsNone(getattr(plain, "anything", None))
        self.assertEqual(plain.width(), plain.size().width())


if __name__ == "__main__":
    unittest.main()
