from __future__ import annotations

import inspect
import sys
import unittest

from main.qt_runtime import GIL_SWITCH_INTERVAL_SEC, apply_gui_gil_switch_interval, ensure_qt_runtime


class GuiGilSwitchIntervalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_interval = sys.getswitchinterval()
        self.addCleanup(sys.setswitchinterval, self._original_interval)

    def test_default_interval_is_lowered_for_gui_responsiveness(self) -> None:
        sys.setswitchinterval(0.005)

        apply_gui_gil_switch_interval()

        self.assertEqual(sys.getswitchinterval(), GIL_SWITCH_INTERVAL_SEC)

    def test_already_lower_interval_is_kept(self) -> None:
        sys.setswitchinterval(0.0005)

        apply_gui_gil_switch_interval()

        self.assertEqual(sys.getswitchinterval(), 0.0005)

    def test_qt_runtime_applies_the_interval(self) -> None:
        source = inspect.getsource(ensure_qt_runtime)

        self.assertIn("apply_gui_gil_switch_interval()", source)


if __name__ == "__main__":
    unittest.main()
