from __future__ import annotations

import inspect
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication


class FluentTranslatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        from ui.fluent_translator import install_fluent_translator

        install_fluent_translator(self.app, language="ru")
        self.app.processEvents()

    def _edit_menu_texts(self) -> list[str]:
        from qfluentwidgets import PlainTextEdit
        from qfluentwidgets.components.widgets.menu import TextEditMenu

        editor = PlainTextEdit()
        self.addCleanup(editor.deleteLater)
        editor.setPlainText("текст")
        menu = TextEditMenu(editor)
        self.addCleanup(menu.deleteLater)
        menu.createActions()
        return [action.text() for action in menu.action_list]

    def test_russian_translator_localizes_text_field_menu(self) -> None:
        from ui.fluent_translator import install_fluent_translator, installed_language

        self.assertTrue(install_fluent_translator(self.app, language="ru"))
        self.assertEqual(installed_language(), "ru")

        texts = self._edit_menu_texts()

        self.assertIn("Вырезать", texts)
        self.assertIn("Копировать", texts)
        self.assertIn("Вставить", texts)
        self.assertIn("Выбрать все", texts)
        self.assertNotIn("Cut", texts)
        self.assertNotIn("Select all", texts)

    def test_switching_to_english_restores_original_strings(self) -> None:
        from ui.fluent_translator import install_fluent_translator, installed_language

        install_fluent_translator(self.app, language="ru")
        self.assertTrue(install_fluent_translator(self.app, language="en"))
        self.assertEqual(installed_language(), "en")

        texts = self._edit_menu_texts()

        self.assertIn("Cut", texts)
        self.assertIn("Select all", texts)

    def test_repeated_install_keeps_single_translator(self) -> None:
        from ui import fluent_translator

        fluent_translator.install_fluent_translator(self.app, language="ru")
        first = fluent_translator._state["translator"]
        fluent_translator.install_fluent_translator(self.app, language="ru")

        self.assertIs(fluent_translator._state["translator"], first)

    def test_translator_survives_garbage_collection(self) -> None:
        import gc

        from ui.fluent_translator import install_fluent_translator

        install_fluent_translator(self.app, language="ru")
        gc.collect()

        self.assertIn("Вырезать", self._edit_menu_texts())

    def test_unknown_language_falls_back_to_default(self) -> None:
        from ui.fluent_translator import install_fluent_translator, installed_language

        install_fluent_translator(self.app, language="zz")

        self.assertEqual(installed_language(), "ru")

    def test_startup_installs_translator_and_language_switch_reinstalls_it(self) -> None:
        import main.qt_runtime as qt_runtime
        import ui.navigation.text_sync as text_sync

        runtime_source = inspect.getsource(qt_runtime.ensure_qt_runtime)
        switch_source = inspect.getsource(text_sync.on_ui_language_changed)

        self.assertIn("install_fluent_translator(app)", runtime_source)
        self.assertIn("install_fluent_translator(language=", switch_source)


if __name__ == "__main__":
    unittest.main()
