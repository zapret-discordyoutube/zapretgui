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
        QTest.qWait(20)
        initial_angle = spinner.startAngle
        QTest.qWait(80)

        self.assertIsInstance(spinner, IndeterminateProgressRing)
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Running)
        self.assertNotEqual(spinner.startAngle, initial_angle)
        self.assertEqual(spinner.spanAngle, 90)

    def test_animation_waits_for_hidden_page_and_restarts_after_every_show(self) -> None:
        from ui.widgets.win11_spinner import Win11Spinner

        parent = QWidget()
        spinner = Win11Spinner(size=24, parent=parent)
        self.addCleanup(parent.deleteLater)
        spinner.start()
        QTest.qWait(20)

        self.assertFalse(spinner.isVisible())
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Stopped)

        parent.show()
        QTest.qWait(20)
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Running)
        self.assertEqual(spinner.spanAngle, 90)
        first_visible_angle = spinner.startAngle
        QTest.qWait(60)
        self.assertNotEqual(spinner.startAngle, first_visible_angle)

        parent.hide()
        QApplication.processEvents()
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Stopped)

        parent.show()
        QTest.qWait(20)
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Running)
        self.assertEqual(spinner.spanAngle, 90)
        restarted_angle = spinner.startAngle
        QTest.qWait(60)
        self.assertNotEqual(spinner.startAngle, restarted_angle)

        spinner.stop()
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Stopped)
        self.assertTrue(spinner.isHidden())


if __name__ == "__main__":
    unittest.main()
