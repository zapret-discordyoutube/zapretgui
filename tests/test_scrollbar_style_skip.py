from __future__ import annotations

import unittest
from unittest.mock import Mock

from PyQt6.QtWidgets import QApplication, QProxyStyle, QStyle, QStyleFactory

from main.qt_runtime import _install_non_transient_scrollbars_style


class _TransientScrollbarStyle(QProxyStyle):
    """Стиль с исчезающими скроллбарами — как на macOS."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):  # noqa: N802 - Qt API
        if hint == QStyle.StyleHint.SH_ScrollBar_Transient:
            return 1
        return super().styleHint(hint, option, widget, returnData)


class _FakeApp:
    def __init__(self, style) -> None:
        self._style = style
        self.setStyle = Mock()

    def style(self):
        return self._style


class ScrollbarStyleSkipTests(unittest.TestCase):
    """Подмена QStyle заново полирует все виджеты — делать её вхолостую нельзя."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_permanent_scrollbars_skip_style_replacement(self) -> None:
        app = _FakeApp(QStyleFactory.create("Fusion"))

        replaced = _install_non_transient_scrollbars_style(app)

        self.assertFalse(replaced)
        app.setStyle.assert_not_called()

    def test_transient_scrollbars_get_the_proxy_style(self) -> None:
        app = _FakeApp(_TransientScrollbarStyle(QStyleFactory.create("Fusion")))

        replaced = _install_non_transient_scrollbars_style(app)

        self.assertTrue(replaced)
        app.setStyle.assert_called_once()
        proxy = app.setStyle.call_args.args[0]
        self.assertEqual(proxy.styleHint(QStyle.StyleHint.SH_ScrollBar_Transient), 0)

    def test_missing_style_still_installs_proxy(self) -> None:
        app = _FakeApp(None)

        replaced = _install_non_transient_scrollbars_style(app)

        self.assertTrue(replaced)
        app.setStyle.assert_called_once()


if __name__ == "__main__":
    unittest.main()
