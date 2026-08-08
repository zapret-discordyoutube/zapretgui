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


def _signals(user32, *, window=None, platform="win32"):
    from ui.windows_drag_hover_detector import read_shell_drag_signals

    return read_shell_drag_signals(
        window if window is not None else _window(),
        user32=user32,
        platform=platform,
    )


class ReadShellDragSignalsTests(unittest.TestCase):
    def test_reads_full_drag_over_own_window(self) -> None:
        from ui.windows_drag_hover_detector import SHELL_DRAG_IMAGE_CLASS

        user32 = _FakeUser32()
        signals = _signals(user32)
        self.assertTrue(signals.button_down)
        self.assertFalse(signals.own_capture)
        self.assertTrue(signals.over_window)
        self.assertTrue(signals.drag_image_present)
        self.assertEqual(user32.find_window_classes, [SHELL_DRAG_IMAGE_CLASS])

    def test_requires_windows_platform(self) -> None:
        signals = _signals(_FakeUser32(), platform="linux")
        self.assertFalse(signals.button_down)
        self.assertFalse(signals.over_window)

    def test_hidden_or_minimized_window_is_not_over(self) -> None:
        signals = _signals(_FakeUser32(), window=_window(visible=False))
        self.assertTrue(signals.button_down)
        self.assertFalse(signals.over_window)

        signals = _signals(_FakeUser32(), window=_window(minimized=True))
        self.assertFalse(signals.over_window)

    def test_button_up_resets_everything(self) -> None:
        signals = _signals(_FakeUser32(button_down=False))
        self.assertEqual(
            (signals.button_down, signals.over_window, signals.drag_image_present),
            (False, False, False),
        )

    def test_own_capture_and_foreign_window_are_reported(self) -> None:
        signals = _signals(_FakeUser32(capture=0x4444))
        self.assertTrue(signals.own_capture)

        signals = _signals(_FakeUser32(root=0x9999))
        self.assertFalse(signals.over_window)

    def test_drag_image_is_read_even_away_from_window(self) -> None:
        # Картинка переноса живёт над чужими окнами; сессия должна успеть
        # зафиксировать её ДО того, как курсор дойдёт до нашего окна.
        signals = _signals(_FakeUser32(root=0x9999, drag_image=0x77))
        self.assertTrue(signals.drag_image_present)
        self.assertFalse(signals.over_window)

    def test_api_failure_means_empty_signals(self) -> None:
        class _BrokenUser32:
            def GetAsyncKeyState(self, _vk):  # noqa: N802 (WinAPI)
                raise OSError("нет доступа")

        signals = _signals(_BrokenUser32())
        self.assertEqual(signals, type(signals)())


class WindowsDragHoverDetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _detector(self, user32):
        from ui.windows_drag_hover_detector import WindowsDragHoverDetector

        started = Mock()
        ended = Mock()
        detector = WindowsDragHoverDetector(
            _window(),
            on_hover_start=started,
            on_hover_end=ended,
            user32=user32,
            platform="win32",
        )
        return detector, started, ended

    def test_hover_shows_over_window_after_drag_image_vanishes(self) -> None:
        """Регрессия: над повышенным окном Проводник уничтожает SysDragImage."""
        user32 = _FakeUser32(root=0x9999, drag_image=0x77)
        detector, started, ended = self._detector(user32)

        detector.poll()  # тащат над Проводником: картинка есть, курсор не у нас
        started.assert_not_called()

        user32.root = WINDOW_HWND
        user32.drag_image = 0  # над нами картинки больше нет
        detector.poll()
        started.assert_called_once_with()

        user32.button_down = False
        detector.poll()
        ended.assert_called_once_with()

    def test_no_hover_without_drag_image_during_button_hold(self) -> None:
        """Простое зажатие кнопки над окном (выделение текста) — не перенос."""
        user32 = _FakeUser32(drag_image=0)
        detector, started, _ended = self._detector(user32)

        detector.poll()
        detector.poll()
        started.assert_not_called()

    def test_session_resets_on_button_release(self) -> None:
        user32 = _FakeUser32(root=0x9999, drag_image=0x77)
        detector, started, _ended = self._detector(user32)

        detector.poll()  # сессия зафиксирована
        user32.button_down = False
        detector.poll()  # кнопку отпустили — сессия сброшена

        user32.button_down = True
        user32.root = WINDOW_HWND
        user32.drag_image = 0
        detector.poll()  # новое зажатие без картинки — не перенос
        started.assert_not_called()

    def test_own_capture_suppresses_hover(self) -> None:
        user32 = _FakeUser32(capture=0x4444)
        detector, started, _ended = self._detector(user32)

        detector.poll()
        started.assert_not_called()

    def test_reports_hover_edges_only_once(self) -> None:
        user32 = _FakeUser32()
        detector, started, ended = self._detector(user32)

        detector.poll()
        detector.poll()
        user32.button_down = False
        detector.poll()
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
        user32 = _FakeUser32()
        detector, started, ended = self._detector(user32)
        started.side_effect = RuntimeError("плашка сломалась")

        detector.poll()

        started.assert_called_once_with()
        detector.stop()
        ended.assert_called_once_with()
        self.assertFalse(detector._shell_drag_session)


if __name__ == "__main__":
    unittest.main()
