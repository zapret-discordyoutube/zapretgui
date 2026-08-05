from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QAbstractAnimation
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget
from qfluentwidgets import IndeterminateProgressRing


class Win11SpinnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_spinner_uses_stock_fluent_animation_with_visible_arc(self) -> None:
        from ui.widgets.win11_spinner import Win11Spinner

        spinner = Win11Spinner(size=24)
        self.addCleanup(spinner.deleteLater)
        spinner.show()
        spinner.start()
        initial_angle = spinner.startAngle

        self.assertEqual(spinner.spanAngle, 90)

        QTest.qWait(80)

        self.assertIsInstance(spinner, IndeterminateProgressRing)
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Running)
        self.assertNotEqual(spinner.startAngle, initial_angle)
        self.assertEqual(spinner.spanAngle, 90)

    def test_animation_started_on_hidden_page_keeps_running_until_explicit_stop(self) -> None:
        from ui.widgets.win11_spinner import Win11Spinner

        parent = QWidget()
        spinner = Win11Spinner(size=24, parent=parent)
        self.addCleanup(parent.deleteLater)
        spinner.start()

        self.assertFalse(spinner.isVisible())
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Running)
        self.assertEqual(spinner.spanAngle, 90)

        parent.show()
        QTest.qWait(30)
        first_visible_angle = spinner.startAngle
        parent.hide()
        QTest.qWait(30)
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Running)

        parent.show()
        QTest.qWait(30)
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Running)
        self.assertNotEqual(spinner.startAngle, first_visible_angle)
        self.assertEqual(spinner.spanAngle, 90)

        spinner.stop()
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Stopped)
        self.assertTrue(spinner.isHidden())


if __name__ == "__main__":
    unittest.main()
