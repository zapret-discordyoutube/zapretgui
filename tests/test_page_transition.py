from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QGraphicsBlurEffect, QLabel, QStackedWidget, QVBoxLayout, QWidget

import ui.page_transition as transition_module
from ui.page_transition import finish_page_reveal, is_page_revealing, reveal_page


class PageTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        patcher = mock.patch.object(transition_module, "are_live_animations_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.stack = QStackedWidget()
        self.addCleanup(self.stack.deleteLater)
        self.first = self._page("Главная")
        self.second = self._page("Оформление")
        self.stack.addWidget(self.first)
        self.stack.addWidget(self.second)
        self.stack.resize(300, 300)
        self.stack.show()
        QApplication.processEvents()

    def _page(self, title: str) -> QWidget:
        page = QWidget()
        QVBoxLayout(page).addWidget(QLabel(title))
        return page

    def _switch(self) -> bool:
        self.stack.setCurrentWidget(self.second)
        return reveal_page(self.second, previous=self.first)

    def test_reveal_runs_then_leaves_page_clean(self) -> None:
        base = self.second.pos()
        self.assertTrue(self._switch())
        self.assertTrue(is_page_revealing(self.second))
        self.assertIsNotNone(self.second.graphicsEffect())
        self.assertGreater(self.second.pos().y(), base.y())

        anim = self.second._zapret_page_transition.anim
        anim.setCurrentTime(anim.duration())

        self.assertFalse(is_page_revealing(self.second))
        self.assertIsNone(self.second.graphicsEffect())
        self.assertEqual(self.second.pos(), base)

    def test_fast_switch_back_finishes_previous_reveal(self) -> None:
        self._switch()
        self.stack.setCurrentWidget(self.first)
        reveal_page(self.first, previous=self.second)

        self.assertFalse(is_page_revealing(self.second))
        self.assertIsNone(self.second.graphicsEffect())
        self.assertTrue(is_page_revealing(self.first))
        finish_page_reveal(self.first)
        self.assertIsNone(self.first.graphicsEffect())

    def test_no_reveal_when_live_animations_are_off(self) -> None:
        with mock.patch.object(transition_module, "are_live_animations_enabled", return_value=False):
            self.assertFalse(self._switch())
        self.assertIsNone(self.second.graphicsEffect())

    def test_page_with_own_effect_is_left_alone(self) -> None:
        own = QGraphicsBlurEffect(self.second)
        self.second.setGraphicsEffect(own)

        self.assertFalse(self._switch())
        self.assertIs(self.second.graphicsEffect(), own)

    def test_window_disables_stock_page_animation(self) -> None:
        import inspect

        from ui.fluent_app_window import ZapretFluentWindow

        self.assertIn("self.stackedWidget.setAnimationEnabled(False)", inspect.getsource(ZapretFluentWindow.__init__))


if __name__ == "__main__":
    unittest.main()
