"""Запас готовых текстов стилей qfluentwidgets отдаёт тот же текст, что библиотека."""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

import qfluentwidgets.common.style_sheet as fluent_style
from qfluentwidgets import BodyLabel, PushButton, Theme, setCustomStyleSheet
from qfluentwidgets.common.config import qconfig

import ui.qfluent_style_cache as style_cache


class QfluentStyleCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        # Библиотечные функции без запаса — с ними сверяем результат.
        cls._plain_read = staticmethod(fluent_style.getStyleSheetFromFile)
        cls._plain_render = staticmethod(fluent_style.renderQss)
        if getattr(fluent_style.renderQss, style_cache._PATCH_MARK, False):
            raise unittest.SkipTest("запас стилей уже установлен другим тестом: сверять не с чем")
        style_cache.install_qfluent_style_cache()

    @classmethod
    def tearDownClass(cls) -> None:
        fluent_style.getStyleSheetFromFile = cls._plain_read
        fluent_style.renderQss = cls._plain_render
        style_cache.clear_qfluent_style_cache()

    def setUp(self) -> None:
        style_cache.clear_qfluent_style_cache()

    def _set_theme(self, theme: Theme) -> None:
        previous = qconfig.theme
        qconfig.theme = theme
        self.addCleanup(setattr, qconfig, "theme", previous)

    def _set_theme_color(self, color: str) -> None:
        item = qconfig._cfg.themeColor
        previous = QColor(item.value)
        item.value = QColor(color)
        self.addCleanup(setattr, item, "value", previous)

    def test_every_library_sheet_is_the_same_text_in_both_themes(self) -> None:
        for theme in (Theme.LIGHT, Theme.DARK):
            self._set_theme(theme)
            for sheet in fluent_style.FluentStyleSheet:
                with self.subTest(theme=theme.value, sheet=sheet.value):
                    expected = self._plain_render(self._plain_read(sheet.path(theme)))
                    self.assertEqual(fluent_style.getStyleSheet(sheet, theme), expected)
                    # Второй раз — уже из запаса.
                    self.assertEqual(fluent_style.getStyleSheet(sheet, theme), expected)

    def test_file_is_read_once_and_rendered_once(self) -> None:
        self._set_theme(Theme.DARK)

        class _CountingFile(fluent_style.QFile):
            opened = 0

            def __init__(self, *args) -> None:
                type(self).opened += 1
                super().__init__(*args)

        with mock.patch.object(fluent_style, "QFile", _CountingFile):
            for _ in range(20):
                label = BodyLabel("текст")
                self.addCleanup(label.deleteLater)
            self.assertEqual(_CountingFile.opened, 1, "файл стиля надписи читается один раз")
            # Готовых текстов столько, сколько разных видов надписи, а не надписей.
            rendered = len(style_cache._rendered)
            for _ in range(20):
                label = BodyLabel("ещё текст")
                self.addCleanup(label.deleteLater)
            self.assertEqual(len(style_cache._rendered), rendered)
            self.assertEqual(_CountingFile.opened, 1)

    def test_changed_theme_color_gives_a_new_text(self) -> None:
        self._set_theme(Theme.DARK)
        sheet = fluent_style.FluentStyleSheet.BUTTON
        self._set_theme_color("#009faa")
        first = fluent_style.getStyleSheet(sheet)
        self._set_theme_color("#ff5500")
        second = fluent_style.getStyleSheet(sheet)

        self.assertNotEqual(first, second, "после смены цвета темы нельзя отдавать старый текст")
        self.assertEqual(second, self._plain_render(self._plain_read(sheet.path())))

    def test_changed_theme_gives_the_other_file(self) -> None:
        sheet = fluent_style.FluentStyleSheet.BUTTON
        self._set_theme(Theme.LIGHT)
        light = fluent_style.getStyleSheet(sheet)
        self._set_theme(Theme.DARK)
        dark = fluent_style.getStyleSheet(sheet)
        self.assertNotEqual(light, dark)

    def test_widget_custom_sheet_still_reaches_the_widget(self) -> None:
        self._set_theme(Theme.DARK)
        button = PushButton("кнопка")
        self.addCleanup(button.deleteLater)
        setCustomStyleSheet(button, "PushButton { color: #111111; }", "PushButton { color: #eeeeee; }")
        self.assertIn("#eeeeee", button.styleSheet())
        self.assertNotIn("--ThemeColor", button.styleSheet())

    def test_files_from_disk_are_not_remembered(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "own.qss"
            path.write_text("QWidget { color: red; }", encoding="utf-8")
            self.assertIn("red", fluent_style.getStyleSheetFromFile(str(path)))
            path.write_text("QWidget { color: blue; }", encoding="utf-8")
            self.assertIn("blue", fluent_style.getStyleSheetFromFile(str(path)))
        self.assertFalse(style_cache._file_texts)

    def test_rendered_texts_are_bounded(self) -> None:
        with mock.patch.object(style_cache, "_MAX_RENDERED", 8):
            for index in range(40):
                fluent_style.renderQss(f"QWidget {{ margin: {index}px; }}")
            self.assertLessEqual(len(style_cache._rendered), 8)

    def test_styled_widget_is_registered_without_a_destroyed_subscription(self) -> None:
        # Библиотека подключала сигнал destroyed у каждого виджета со стилем.
        # Подключение отпускает общий замок Python: рядом с занятым фоновым
        # потоком это было самым дорогим местом сборки страницы.
        from PyQt6.QtWidgets import QWidget

        widget = QWidget()
        self.addCleanup(widget.deleteLater)
        before = widget.receivers(widget.destroyed)
        fluent_style.setStyleSheet(widget, fluent_style.FluentStyleSheet.BUTTON)
        self.assertEqual(widget.receivers(widget.destroyed), before)
        self.assertIn(widget, fluent_style.styleSheetManager.widgets)
        self.assertTrue(widget.styleSheet())

    def test_theme_update_still_restyles_live_widgets_and_drops_dead_ones(self) -> None:
        from PyQt6 import sip

        self._set_theme(Theme.DARK)
        alive = PushButton("живая")
        self.addCleanup(alive.deleteLater)
        dead = PushButton("удалённая")
        sip.delete(dead)
        self.assertIn(dead, fluent_style.styleSheetManager.widgets)

        qconfig.theme = Theme.LIGHT
        fluent_style.updateStyleSheet()
        expected = fluent_style.getStyleSheet(fluent_style.FluentStyleSheet.BUTTON, Theme.LIGHT)
        self.assertEqual(alive.styleSheet().strip(), expected.strip())
        self.assertNotIn(dead, fluent_style.styleSheetManager.widgets, "мёртвую запись библиотека выбрасывает сама")

    def test_install_is_idempotent(self) -> None:
        patched = fluent_style.renderQss
        style_cache.install_qfluent_style_cache()
        self.assertIs(fluent_style.renderQss, patched)


if __name__ == "__main__":
    unittest.main()
