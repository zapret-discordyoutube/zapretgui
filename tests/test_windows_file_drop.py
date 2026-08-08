from __future__ import annotations

import unittest
from ctypes import addressof, wintypes
from unittest.mock import Mock


def _value(value):
    return getattr(value, "value", value)


class WindowsFileDropTests(unittest.TestCase):
    def test_uses_legacy_drop_on_windows_and_qt_drop_elsewhere(self) -> None:
        from ui.windows_file_drop import use_qt_file_drop

        self.assertFalse(use_qt_file_drop(platform="win32"))
        self.assertTrue(use_qt_file_drop(platform="linux"))
        self.assertTrue(use_qt_file_drop(platform="darwin"))

    def test_enables_drop_messages_for_the_native_window(self) -> None:
        from ui.windows_file_drop import (
            ALLOWED_DROP_MESSAGES,
            MSGFLT_ALLOW,
            enable_windows_file_drop,
        )

        allowed: list[tuple[int, int, int]] = []
        accepted: list[tuple[int, bool]] = []
        revoked: list[int] = []

        class User32:
            @staticmethod
            def ChangeWindowMessageFilterEx(hwnd, message, action, details):
                self.assertIsNone(details)
                allowed.append((_value(hwnd), _value(message), _value(action)))
                return True

        class Shell32:
            @staticmethod
            def DragAcceptFiles(hwnd, accept):
                accepted.append((_value(hwnd), bool(_value(accept))))

        class Ole32:
            @staticmethod
            def RevokeDragDrop(hwnd):
                revoked.append(_value(hwnd))
                return 0

        window = Mock()
        window.winId.return_value = 0x123456789

        enabled = enable_windows_file_drop(
            window,
            platform="win32",
            user32=User32(),
            shell32=Shell32(),
            ole32=Ole32(),
        )

        self.assertTrue(enabled)
        self.assertEqual(
            allowed,
            [(0x123456789, message, MSGFLT_ALLOW) for message in ALLOWED_DROP_MESSAGES],
        )
        self.assertEqual(accepted, [(0x123456789, True)])
        self.assertEqual(revoked, [0x123456789])

    def test_restores_saved_qt_drop_target_for_internal_preset_drag(self) -> None:
        from ui.windows_file_drop import restore_windows_qt_file_drop

        accepted: list[tuple[int, bool]] = []
        registered: list[tuple[int, int]] = []

        class Shell32:
            @staticmethod
            def DragAcceptFiles(hwnd, accept):
                accepted.append((_value(hwnd), bool(_value(accept))))

        class Ole32:
            @staticmethod
            def RegisterDragDrop(hwnd, pointer):
                registered.append((_value(hwnd), _value(pointer)))
                return 0

        window = Mock()
        window.winId.return_value = 0x123456789
        window._windows_qt_drop_target_state = {
            "hwnd": 0x123456789,
            "pointer": 0xABCDEF,
        }

        self.assertTrue(
            restore_windows_qt_file_drop(
                window,
                platform="win32",
                shell32=Shell32(),
                ole32=Ole32(),
            )
        )
        self.assertEqual(accepted, [(0x123456789, False)])
        self.assertEqual(registered, [(0x123456789, 0xABCDEF)])

    def test_does_not_call_windows_api_on_other_platforms(self) -> None:
        from ui.windows_file_drop import enable_windows_file_drop

        window = Mock()
        self.assertFalse(
            enable_windows_file_drop(
                window,
                platform="linux",
                user32=Mock(),
                shell32=Mock(),
            )
        )
        window.winId.assert_not_called()

    def test_reads_all_unicode_paths_and_always_releases_drop_handle(self) -> None:
        from ui.windows_file_drop import WM_DROPFILES, windows_dropped_file_paths

        paths = [r"C:\\Папка\\Первый.txt", r"D:\\SECOND.TXT"]
        released: list[int] = []

        class Shell32:
            @staticmethod
            def DragQueryFileW(handle, index, buffer, size):
                index_value = _value(index)
                if index_value == 0xFFFFFFFF:
                    return len(paths)
                path = paths[index_value]
                if buffer is None:
                    return len(path)
                self.assertGreaterEqual(_value(size), len(path) + 1)
                buffer.value = path
                return len(path)

            @staticmethod
            def DragFinish(handle):
                released.append(_value(handle))

        message = wintypes.MSG()
        message.message = WM_DROPFILES
        message.wParam = 0xABCDEF

        result = windows_dropped_file_paths(
            int(addressof(message)),
            platform="win32",
            shell32=Shell32(),
        )

        self.assertEqual(result, paths)
        self.assertEqual(released, [0xABCDEF])

    def test_ignores_other_native_messages_without_touching_shell(self) -> None:
        from ui.windows_file_drop import windows_dropped_file_paths

        shell32 = Mock()
        message = wintypes.MSG()
        message.message = 0x9999

        self.assertIsNone(
            windows_dropped_file_paths(
                int(addressof(message)),
                platform="win32",
                shell32=shell32,
            )
        )
        shell32.DragQueryFileW.assert_not_called()
        shell32.DragFinish.assert_not_called()

    def test_releases_drop_handle_when_reading_fails(self) -> None:
        from ui.windows_file_drop import WM_DROPFILES, windows_dropped_file_paths

        released: list[int] = []

        class Shell32:
            @staticmethod
            def DragQueryFileW(*_args):
                raise OSError("broken drop handle")

            @staticmethod
            def DragFinish(handle):
                released.append(_value(handle))

        message = wintypes.MSG()
        message.message = WM_DROPFILES
        message.wParam = 0x13579

        self.assertEqual(
            windows_dropped_file_paths(
                int(addressof(message)),
                platform="win32",
                shell32=Shell32(),
            ),
            [],
        )
        self.assertEqual(released, [0x13579])


if __name__ == "__main__":
    unittest.main()
