"""Главная страница: первый экран сразу, настройки — блоком позже (ui.block_build)."""

from __future__ import annotations

import inspect
import os
import time
import unittest
from importlib import import_module
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

import ui.block_build as block_build
from app.page_names import PageName
from presets.ui.control.control_page_shared import (
    FINE_TUNING_BLOCK,
    LAST_MESSAGE_BLOCK,
    PROGRAM_SETTINGS_BLOCK,
    SETTINGS_BLOCK_HEIGHTS,
    WINDOWS_SETTINGS_BLOCK,
)
from ui.page_registry import PAGE_CLASS_SPECS


_PAGES = (PageName.ZAPRET2_MODE_CONTROL, PageName.ZAPRET1_MODE_CONTROL)
_BLOCKS = (PROGRAM_SETTINGS_BLOCK, WINDOWS_SETTINGS_BLOCK, FINE_TUNING_BLOCK, LAST_MESSAGE_BLOCK)
_SETTINGS_WIDGETS = (
    "program_settings_card",
    "windows_settings_card",
    "additional_settings_card",
    "gui_autostart_toggle",
    "auto_dpi_toggle",
    "tray_close_mode_combo",
    "defender_toggle",
    "max_block_toggle",
    "state_media_block_toggle",
    "discord_restart_toggle",
    "wssize_toggle",
    "debug_log_toggle",
    "last_status_message_card",
)


def _pump(seconds: float = 0.0) -> None:
    deadline = time.monotonic() + seconds
    QApplication.processEvents()
    while time.monotonic() < deadline:
        QApplication.processEvents()


class ControlSettingsBlockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        # Фоновая достройка в этих тестах не нужна: проверяем, что делает показ.
        patcher = mock.patch.object(block_build, "BACKGROUND_START_DELAY_MS", 600_000)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _page(self, page_name: PageName, *, window_size=(1100, 750)):
        module_name, class_name = PAGE_CLASS_SPECS[page_name]
        page_cls = getattr(import_module(module_name), class_name)
        deps = {
            parameter.name: MagicMock()
            for parameter in inspect.signature(page_cls.__init__).parameters.values()
            if parameter.kind is parameter.KEYWORD_ONLY and parameter.default is parameter.empty
        }
        window = QWidget()
        self.addCleanup(window.deleteLater)
        window.resize(*window_size)
        layout = QVBoxLayout(window)
        layout.setContentsMargins(0, 0, 0, 0)
        page = page_cls(parent=window, **deps)
        self.addCleanup(page.cleanup)
        layout.addWidget(page)
        return window, page

    def test_constructor_builds_the_first_screen_and_reserves_the_settings(self) -> None:
        for page_name in _PAGES:
            with self.subTest(page=page_name.name):
                _window, page = self._page(page_name)
                # Первый экран готов сразу.
                for name in ("status_card", "top_summary", "quick_actions_grid", "onboarding_tour_card"):
                    self.assertIsNotNone(getattr(page, name), name)
                # Настройки ещё не собраны, но место под них занято.
                for name in _SETTINGS_WIDGETS:
                    self.assertIsNone(getattr(page, name), name)
                for name in _BLOCKS:
                    block = page.lazy_block(name)
                    self.assertFalse(block.is_built(), name)
                    self.assertGreater(block.minimumHeight(), 50, name)

    def test_settings_stay_unbuilt_while_they_are_below_the_window_edge(self) -> None:
        for page_name in _PAGES:
            with self.subTest(page=page_name.name):
                window, page = self._page(page_name, window_size=(1100, 700))
                window.show()
                _pump(0.05)
                self.assertFalse(page.is_page_built())
                self.assertIsNone(page.program_settings_card)
                self.assertGreater(page.verticalScrollBar().maximum(), sum(SETTINGS_BLOCK_HEIGHTS.values()) * 0.8)

    def test_tall_window_gets_the_visible_settings_right_at_show(self) -> None:
        for page_name in _PAGES:
            with self.subTest(page=page_name.name):
                window, page = self._page(page_name, window_size=(1600, 900))
                window.show()
                # Ни одного оборота цикла событий: группа попала в первый экран
                # и собрана самим показом, без таймеров и пустого места.
                self.assertIsNotNone(page.program_settings_card)
                self.assertTrue(page.lazy_block(PROGRAM_SETTINGS_BLOCK).is_built())
                # А то, что и в высоком окне ниже края, показ не собирает.
                self.assertFalse(page.lazy_block(LAST_MESSAGE_BLOCK).is_built())

    def test_scrolling_down_builds_the_settings(self) -> None:
        for page_name in _PAGES:
            with self.subTest(page=page_name.name):
                window, page = self._page(page_name, window_size=(1100, 700))
                window.show()
                _pump(0.05)
                bar = page.verticalScrollBar()
                bar.setValue(300)
                _pump(0.1)
                self.assertIsNotNone(page.program_settings_card)
                self.assertFalse(page.is_page_built(), "собирается то, до чего долистали, а не всё сразу")
                bar.setValue(bar.maximum())
                _pump(0.15)
                bar.setValue(bar.maximum())
                _pump(0.15)
                for name in ("additional_settings_card", "last_status_message_card", "wssize_toggle"):
                    self.assertIsNotNone(getattr(page, name), name)

    def test_reserved_height_is_close_to_the_real_one(self) -> None:
        for page_name in _PAGES:
            with self.subTest(page=page_name.name):
                window, page = self._page(page_name, window_size=(1100, 700))
                window.show()
                _pump(0.05)
                reserved = {name: page.lazy_block(name).height() for name in _BLOCKS}
                length = page.verticalScrollBar().maximum()
                page.ensure_all_blocks()
                _pump(0.05)
                for name in _BLOCKS:
                    real = page.lazy_block(name).height()
                    self.assertLess(abs(real - reserved[name]), 30, f"{name}: место {reserved[name]}, блок {real}")
                self.assertLess(abs(page.verticalScrollBar().maximum() - length), 40)

    def test_settings_that_arrived_before_the_block_are_applied_when_it_is_built(self) -> None:
        for page_name in _PAGES:
            with self.subTest(page=page_name.name):
                _window, page = self._page(page_name)
                snapshot = SimpleNamespace(
                    auto_dpi_enabled=True,
                    gui_autostart_enabled=True,
                    tray_close_mode="minimize_only",
                    defender_disabled=False,
                    max_blocked=True,
                    state_media_blocked=False,
                )
                plan = SimpleNamespace(discord_restart=True, wssize_enabled=True, debug_log_enabled=False)
                # Блока ещё нет: применять некуда, но и падать нельзя.
                page._apply_program_settings_snapshot(snapshot)
                page._apply_additional_settings_state(plan)

                page.ensure_all_blocks()
                self.assertTrue(page.auto_dpi_toggle.isChecked())
                self.assertTrue(page.gui_autostart_toggle.isChecked())
                self.assertEqual(page.tray_close_mode_combo.currentData(), "minimize_only")
                self.assertTrue(page.max_block_toggle.isChecked())
                self.assertFalse(page.defender_toggle.isChecked())
                self.assertTrue(page.discord_restart_toggle.isChecked())
                self.assertTrue(page.wssize_toggle.isChecked())
                self.assertFalse(page.debug_log_toggle.isChecked())

    def test_tour_targets_and_language_change_build_the_block_themselves(self) -> None:
        for page_name in _PAGES:
            with self.subTest(page=page_name.name):
                _window, page = self._page(page_name)
                self.assertIsNotNone(page.onboarding_target("quick_actions"))
                self.assertFalse(page.is_page_built(), "цель первого экрана блок не достраивает")
                self.assertIsNotNone(page.onboarding_target("program_settings"))
                self.assertTrue(page.lazy_block(PROGRAM_SETTINGS_BLOCK).is_built())
                self.assertFalse(page.lazy_block(WINDOWS_SETTINGS_BLOCK).is_built(), "достраивается только нужная группа")
                self.assertIsNotNone(page.onboarding_target("windows_settings"))
                self.assertIsNotNone(page.onboarding_target("fine_tuning"))

                _window, page = self._page(page_name)
                page.set_ui_language("en")
                self.assertTrue(page.is_page_built())
                self.assertIsNotNone(page.additional_settings_card)


if __name__ == "__main__":
    unittest.main()
