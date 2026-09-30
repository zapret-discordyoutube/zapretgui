import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

from ui.fluent_widgets import set_tooltip


class TooltipLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_tooltip_window_is_deleted_with_its_widget(self) -> None:
        window = QWidget()
        self.addCleanup(window.deleteLater)
        window.show()
        button = QPushButton("x", window)
        button.show()
        set_tooltip(button, "подсказка")

        QCoreApplication.sendEvent(button, QEvent(QEvent.Type.Enter))
        tooltip = button._fluent_tooltip_filter._tooltip
        self.assertIsNotNone(tooltip)
        self.assertIs(tooltip.parent(), window)

        sip.delete(button)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)

        self.assertTrue(sip.isdeleted(tooltip))


if __name__ == "__main__":
    unittest.main()
