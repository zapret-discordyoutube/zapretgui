from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication


WINDOW_HWND = 0x1111


def _window(*, visible=True, minimized=False):
    return SimpleNamespace(
        isVisible=lambda: visible,
        isMinimized=lambda: minimized,
        winId=lambda: WINDOW_HWND,
    )


class _FakeUser32:
    def __init__(
        self,
        *,
        button_down=True,
        capture=0,
        cursor_ok=True,
        under_cursor=0x2222,
        root=WINDOW_HWND,
        drag_image=0x3333,
    ) -> None:
        self.button_down = button_down
        self.capture = capture
        self.cursor_ok = cursor_ok
        self.under_cursor = under_cursor
        self.root = root
        self.drag_image = drag_image
        self.find_window_classes: list[str] = []

    def GetAsyncKeyState(self, _vk):  # noqa: N802 (WinAPI)
        return 0x8000 if self.button_down else 0

    def GetCapture(self):  # noqa: N802 (WinAPI)
        return self.capture

    def GetCursorPos(self, _point_ref):  # noqa: N802 (WinAPI)
        return 1 if self.cursor_ok else 0

    def WindowFromPoint(self, _point):  # noqa: N802 (WinAPI)
        return self.under_cursor

    def GetAncestor(self, _hwnd, _flags):  # noqa: N802 (WinAPI)
        return self.root

    def FindWindowW(self, class_name, _window_name):  # noqa: N802 (WinAPI)
        self.find_window_classes.append(class_name)
        return self.drag_image


class ShellFileDragOverWindowTests(unittest.TestCase):
    def test_detects_shell_drag_over_own_window(self) -> None:
        from ui.windows_drag_hover_detector import (
            SHELL_DRAG_IMAGE_CLASS,
            shell_file_drag_over_window,
        )

        user32 = _FakeUser32()
        self.assertTrue(
            shell_file_drag_over_window(_window(), user32=user32, platform="win32")
        )
        self.assertEqual(user32.find_window_classes, [SHELL_DRAG_IMAGE_CLASS])

    def test_requires_windows_platform(self) -> None:
        from ui.windows_drag_hover_detector import shell_file_drag_over_window

        self.assertFalse(
            shell_file_drag_over_window(_window(), user32=_FakeUser32(), platform="linux")
        )

    def test_rejects_hidden_or_minimized_window(self) -> None:
        from ui.windows_drag_hover_detector import shell_file_drag_over_window

        self.assertFalse(
            shell_file_drag_over_window(
                _window(visible=False), user32=_FakeUser32(), platform="win32"
            )
        )
        self.assertFalse(
            shell_file_drag_over_window(
                _window(minimized=True), user32=_FakeUser32(), platform="win32"
            )
        )

    def test_rejects_when_mouse_button_is_up(self) -> None:
        from ui.windows_drag_hover_detector import shell_file_drag_over_window

        user32 = _FakeUser32(button_down=False)
        self.assertFalse(
            shell_file_drag_over_window(_window(), user32=user32, platform="win32")
        )

    def test_rejects_own_mouse_capture(self) -> None:
        from ui.windows_drag_hover_detector import shell_file_drag_over_window

        user32 = _FakeUser32(capture=0x4444)
        self.assertFalse(
            shell_file_drag_over_window(_window(), user32=user32, platform="win32")
        )

    def test_rejects_cursor_over_foreign_window(self) -> None:
        from ui.windows_drag_hover_detector import shell_file_drag_over_window

        user32 = _FakeUser32(root=0x9999)
        self.assertFalse(
            shell_file_drag_over_window(_window(), user32=user32, platform="win32")
        )

    def test_rejects_without_shell_drag_image_window(self) -> None:
        from ui.windows_drag_hover_detector import shell_file_drag_over_window

        user32 = _FakeUser32(drag_image=0)
        self.assertFalse(
            shell_file_drag_over_window(_window(), user32=user32, platform="win32")
        )

    def test_api_failure_means_no_hover(self) -> None:
        from ui.windows_drag_hover_detector import shell_file_drag_over_window

        class _BrokenUser32:
            def GetAsyncKeyState(self, _vk):  # noqa: N802 (WinAPI)
                raise OSError("нет доступа")

        self.assertFalse(
            shell_file_drag_over_window(
                _window(), user32=_BrokenUser32(), platform="win32"
            )
        )


class WindowsDragHoverDetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_reports_hover_edges_only_once(self) -> None:
        from ui.windows_drag_hover_detector import WindowsDragHoverDetector

        started = Mock()
        ended = Mock()
        detector = WindowsDragHoverDetector(
            _window(),
            on_hover_start=started,
            on_hover_end=ended,
            platform="win32",
        )

        with patch(
            "ui.windows_drag_hover_detector.shell_file_drag_over_window",
            side_effect=[True, True, False, False],
        ):
            for _ in range(4):
                detector.poll()

        started.assert_called_once_with()
        ended.assert_called_once_with()

    def test_start_runs_polling_only_on_windows(self) -> None:
        from ui.windows_drag_hover_detector import WindowsDragHoverDetector

        detector = WindowsDragHoverDetector(
            _window(),
            on_hover_start=Mock(),
            on_hover_end=Mock(),
            platform="linux",
        )
        self.assertFalse(detector.start())
        self.assertFalse(detector._timer.isActive())

        windows_detector = WindowsDragHoverDetector(
            _window(),
            on_hover_start=Mock(),
            on_hover_end=Mock(),
            platform="win32",
        )
        self.assertTrue(windows_detector.start())
        self.assertTrue(windows_detector._timer.isActive())
        windows_detector.stop()
        self.assertFalse(windows_detector._timer.isActive())

    def test_stop_reports_hover_end_and_callback_errors_do_not_break_poll(self) -> None:
        from ui.windows_drag_hover_detector import WindowsDragHoverDetector

        started = Mock(side_effect=RuntimeError("плашка сломалась"))
        ended = Mock()
        detector = WindowsDragHoverDetector(
            _window(),
            on_hover_start=started,
            on_hover_end=ended,
            platform="win32",
        )

        with patch(
            "ui.windows_drag_hover_detector.shell_file_drag_over_window",
            return_value=True,
        ):
            detector.poll()

        started.assert_called_once_with()
        detector.stop()
        ended.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
