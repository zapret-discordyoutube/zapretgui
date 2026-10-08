"""Появление есть у каждой страницы: его нельзя тихо выключить.

Все страницы реестра — BasePage (у неё выплывание подключено к content).
Большой виджет с ручной отрисовкой выплывает своим входом play_float_in,
который вызывает общий модуль ui.widgets.stagger_float_in.
"""

from __future__ import annotations

import os
import time
import unittest
from importlib import import_module
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

import ui.widgets.stagger_float_in as float_module
from ui.widgets.stagger_float_in import attach_stagger_float_in, stagger_float_in


class _OwnEntrance(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.played: list[int] = []
        self.finished = 0

    def play_float_in(self, delay_ms: int) -> None:
        self.played.append(delay_ms)

    def finish_float_in(self) -> None:
        self.finished += 1


class PageFloatInContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        patcher = mock.patch.object(float_module, "are_live_animations_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_block_is_painted_once_for_the_whole_flight(self) -> None:
        # Раньше каждый кадр выплывания (а потом каждые 0,12 с) заново рисовал
        # карточку со всеми детьми на CPU. Теперь блок снимается в картинку
        # один раз, а летит картинка.
        class _Counting(QWidget):
            paints = 0

            def paintEvent(self, event) -> None:  # noqa: N802
                type(self).paints += 1

        container = QWidget()
        self.addCleanup(container.deleteLater)
        layout = QVBoxLayout(container)
        card = _Counting()
        card.setMinimumHeight(60)
        layout.addWidget(card)
        container.resize(240, 160)
        controller = attach_stagger_float_in(container)
        container.show()
        QApplication.processEvents()
        _Counting.paints = 0

        deadline = time.monotonic() + float_module.FLOAT_IN_DURATION_MS / 1000 * 0.6
        while time.monotonic() < deadline:
            QApplication.processEvents()
        self.assertTrue(controller.is_running())
        self.assertEqual(_Counting.paints, 1, "за полёт блок рисуется один раз — в снимок")

        deadline = time.monotonic() + float_module.FLOAT_IN_DURATION_MS / 1000 * 0.6 + 0.2
        while time.monotonic() < deadline:
            QApplication.processEvents()
        self.assertFalse(controller.is_running())
        self.assertEqual(_Counting.paints, 2, "второй раз — когда встал на место")

    def test_every_registered_page_gets_float_in(self) -> None:
        from ui.page_registry import PAGE_CLASS_SPECS
        from ui.pages.base_page import BasePage

        for page_name, (module_name, class_name) in PAGE_CLASS_SPECS.items():
            with self.subTest(page=class_name):
                page_cls = getattr(import_module(module_name), class_name)
                if class_name == "OrchestraSettingsPage":
                    # Контейнер вкладок: выплывает каждая вкладка — сама BasePage.
                    for tab_module, tab_class in (
                        ("orchestra.ui.locked_page", "OrchestraLockedPage"),
                        ("orchestra.ui.blocked_page", "OrchestraBlockedPage"),
                        ("orchestra.ui.whitelist_page", "OrchestraWhitelistPage"),
                        ("orchestra.ui.ratings_page", "OrchestraRatingsPage"),
                    ):
                        tab_cls = getattr(import_module(tab_module), tab_class)
                        self.assertTrue(issubclass(tab_cls, BasePage), tab_class)
                    continue
                self.assertTrue(issubclass(page_cls, BasePage), f"{page_name}: {class_name} без выплывания")

    def test_base_page_attaches_float_in_to_content(self) -> None:
        from ui.pages.base_page import BasePage

        page = BasePage(title="Проверка")
        self.addCleanup(page.deleteLater)
        self.assertIsNotNone(stagger_float_in(page.content))

    def test_own_entrance_is_played_in_the_common_queue_and_finished(self) -> None:
        container = QWidget()
        self.addCleanup(container.deleteLater)
        layout = QVBoxLayout(container)
        first = QWidget()
        first.setMinimumHeight(20)
        own = _OwnEntrance()
        own.setMinimumHeight(40)
        layout.addWidget(first)
        layout.addWidget(own)
        controller = attach_stagger_float_in(container)
        container.resize(300, 200)
        container.show()
        controller.play()

        self.assertEqual(own.played, [float_module.FLOAT_IN_STEP_MS])
        # Свой вход виджет рисует сам: общий слой его не прячет и не снимает.
        self.assertFalse(float_module.is_floating_in(own))
        self.assertTrue(own.mask().isEmpty())
        controller.finish_all()
        self.assertEqual(own.finished, 1)

    def test_hosts_tiles_float_in_on_every_show(self) -> None:
        from test_hosts_page_draft import HostsPageTests, _manual_snapshot

        helper = HostsPageTests("test_page_scrolls_as_a_whole")
        self.addCleanup(helper.doCleanups)
        page = helper._page(_manual_snapshot())
        grid = page.tiles
        controller = stagger_float_in(page.content)
        self.assertIsNotNone(controller)
        for _show in range(2):
            page.hide()
            QApplication.processEvents()
            page.show()
            QApplication.processEvents()
            controller.play()
            self.assertIsNotNone(grid._entrance_start, "сетка hosts не выплывает при показе")
            self.assertTrue(grid._entrance_order)
            # В начале входа плитки ещё прозрачны, к концу — на месте.
            first = next(iter(grid._entrance_order))
            self.assertEqual(grid._entrance_progress(first, grid._entrance_start), 0.0)
            late = grid._entrance_start + 10.0
            self.assertEqual(grid._entrance_progress(first, late), 1.0)
            self.assertTrue(grid._entrance_finished(late))
            controller.finish_all()
            self.assertIsNone(grid._entrance_start)

    def test_hosts_tiles_float_in_when_services_arrive_on_an_open_page(self) -> None:
        # Список сервисов готовит фоновый поток: при первом показе в сетке
        # одна плитка-заглушка, и вход при показе доставался только ей.
        from test_hosts_page_draft import HostsPageTests, _manual_snapshot

        helper = HostsPageTests("test_page_scrolls_as_a_whole")
        self.addCleanup(helper.doCleanups)
        page = helper._page(_manual_snapshot())
        grid = page.tiles
        tiles = grid.tiles()
        self.assertGreater(len(tiles), 1)

        grid.set_tiles(tiles[:1])
        page.show()
        QApplication.processEvents()
        stagger_float_in(page.content).finish_all()
        self.assertIsNone(grid._entrance_start)

        grid.set_tiles(tiles)
        QApplication.processEvents()
        QApplication.processEvents()
        self.assertIsNotNone(grid._entrance_start, "настоящие плитки должны выплыть, а не просто возникнуть")
        self.assertGreater(len(grid._entrance_order), 1)
        grid.finish_float_in()


if __name__ == "__main__":
    unittest.main()
