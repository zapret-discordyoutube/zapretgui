import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from ui.idle_memory_trim import IdleMemoryTrimmer, trim_process_memory


class IdleMemoryTrimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _window(self) -> QWidget:
        window = QWidget()
        self.addCleanup(window.deleteLater)
        window.show()
        return window

    def test_trim_waits_for_window_to_stay_hidden(self) -> None:
        window = self._window()
        trim = Mock()
        trimmer = IdleMemoryTrimmer(window, delay_ms=60_000, repeat_ms=600_000, trim=trim)

        self.assertFalse(trimmer._timer.isActive())
        window.hide()
        self.assertTrue(trimmer._timer.isActive())
        self.assertEqual(trimmer._timer.interval(), 60_000)

        trimmer._on_timeout()
        trim.assert_called_once_with()
        # Пока окно скрыто, очистка повторяется реже.
        self.assertEqual(trimmer._timer.interval(), 600_000)

    def test_showing_window_cancels_pending_trim(self) -> None:
        window = self._window()
        trim = Mock()
        trimmer = IdleMemoryTrimmer(window, delay_ms=60_000, trim=trim)

        window.hide()
        window.show()

        self.assertFalse(trimmer._timer.isActive())
        trimmer._on_timeout()
        trim.assert_not_called()

    def test_trim_runs_without_windows_api(self) -> None:
        trim_process_memory()


if __name__ == "__main__":
    unittest.main()
