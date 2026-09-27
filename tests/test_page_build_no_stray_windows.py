from __future__ import annotations

import inspect
import os
import unittest
from importlib import import_module
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QApplication, QWidget

from ui.page_registry import PAGE_CLASS_SPECS


class _StrayWindowRecorder(QObject):
    """Запоминает виджеты, которые показались отдельным окном Windows.

    Виджет без родителя после setVisible(True)/show() становится
    самостоятельным окном с рамкой и заголовком. Во время фоновой сборки
    страницы при старте это выглядит как мигающее маленькое окно.
    """

    def __init__(self) -> None:
        super().__init__()
        self.shown: list[str] = []

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            text = obj.text() if hasattr(obj, "text") else ""
            self.shown.append(f"{type(obj).__name__} {text!r}".strip())
        return False


def _required_page_deps(page_cls) -> dict[str, MagicMock]:
    parameters = inspect.signature(page_cls.__init__).parameters.values()
    return {
        parameter.name: MagicMock()
        for parameter in parameters
        if parameter.kind is parameter.KEYWORD_ONLY and parameter.default is parameter.empty
    }


class PageBuildNoStrayWindowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_page_build_shows_no_separate_windows(self) -> None:
        # Как в UiPageFactory: страница создаётся внутри главного окна.
        host = QWidget()
        self.addCleanup(host.deleteLater)

        for page_name, (module_name, class_name) in PAGE_CLASS_SPECS.items():
            with self.subTest(page=page_name.name):
                page_cls = getattr(import_module(module_name), class_name)
                recorder = _StrayWindowRecorder()
                self.app.installEventFilter(recorder)
                try:
                    page_cls(parent=host, **_required_page_deps(page_cls))
                    self.app.processEvents()
                finally:
                    self.app.removeEventFilter(recorder)

                self.assertEqual(recorder.shown, [])


if __name__ == "__main__":
    unittest.main()
