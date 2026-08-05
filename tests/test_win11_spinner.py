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

    def test_spinner_uses_stock_fluent_animation_and_changes_frame(self) -> None:
        from ui.widgets.win11_spinner import Win11Spinner

        spinner = Win11Spinner(size=24)
        self.addCleanup(spinner.deleteLater)
        spinner.show()
        spinner.start()
        initial_frame = (spinner.startAngle, spinner.spanAngle)

        QTest.qWait(80)

        self.assertIsInstance(spinner, IndeterminateProgressRing)
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Running)
        self.assertNotEqual((spinner.startAngle, spinner.spanAngle), initial_frame)

    def test_animation_stops_while_parent_is_hidden_and_resumes_after_show(self) -> None:
        from ui.widgets.win11_spinner import Win11Spinner

        parent = QWidget()
        spinner = Win11Spinner(size=24, parent=parent)
        self.addCleanup(parent.deleteLater)
        parent.show()
        spinner.start()
        QTest.qWait(30)
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Running)

        parent.hide()
        QApplication.processEvents()
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Stopped)

        parent.show()
        QTest.qWait(30)
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Running)

        spinner.stop()
        self.assertEqual(spinner.aniGroup.state(), QAbstractAnimation.State.Stopped)
        self.assertTrue(spinner.isHidden())


if __name__ == "__main__":
    unittest.main()
