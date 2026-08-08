from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QUrl
from PyQt6.QtWidgets import QApplication


class _DropEvent:
    def __init__(self, event_type, urls) -> None:
        self._event_type = event_type
        self._mime_data = SimpleNamespace(
            hasUrls=lambda: True,
            urls=lambda: list(urls),
        )
        self.accepted = False

    def type(self):
        return self._event_type

    def mimeData(self):  # noqa: N802
        return self._mime_data

    def acceptProposedAction(self) -> None:  # noqa: N802
        self.accepted = True


class _Receiver:
    def __init__(self, window) -> None:
        self._window = window

    def window(self):
        return self._window


class WindowPresetFileDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_main_window_installs_filter_and_routes_to_current_page(self) -> None:
        from ui.fluent_app_window import ZapretFluentWindow
        from ui.window_preset_file_drop import (
            PresetFileDropOverlay,
            WindowPresetFileDropFilter,
        )

        with patch(
            "ui.fluent_app_window.enable_windows_file_drop",
            return_value=True,
        ) as enable_native_drop:
            window = ZapretFluentWindow()
        self.addCleanup(window.deleteLater)
        event_filter = window._preset_file_drop_filter
        self.addCleanup(self._app.removeEventFilter, event_filter)
        target = SimpleNamespace(import_dropped_preset_files=Mock(return_value=True))
        window.ui_session = SimpleNamespace(
            page_host=SimpleNamespace(current_page=lambda: target),
        )

        self.assertTrue(window.acceptDrops())
        self.assertIsInstance(event_filter, WindowPresetFileDropFilter)
        self.assertIsInstance(event_filter.overlay, PresetFileDropOverlay)
        self.assertTrue(event_filter.overlay.isHidden())
        self.assertIs(window._current_preset_file_drop_target(), target)
        self.assertTrue(window._windows_file_drop_enabled)
        enable_native_drop.assert_called_once_with(window)

    def test_windows_window_disables_qt_ole_drop_owner(self) -> None:
        from ui.fluent_app_window import ZapretFluentWindow

        with (
            patch("ui.fluent_app_window.use_qt_file_drop", return_value=False),
            patch("ui.fluent_app_window.enable_windows_file_drop", return_value=True),
        ):
            window = ZapretFluentWindow()
        self.addCleanup(window.deleteLater)
        event_filter = window._preset_file_drop_filter
        self.addCleanup(self._app.removeEventFilter, event_filter)

        self.assertFalse(window.acceptDrops())
        self.assertTrue(window._windows_file_drop_enabled)

    def test_rebinds_native_drop_after_qt_recreates_window_handle(self) -> None:
        from ui.fluent_app_window import ZapretFluentWindow

        with patch(
            "ui.fluent_app_window.enable_windows_file_drop",
            return_value=True,
        ) as enable_native_drop:
            window = ZapretFluentWindow()
            self.addCleanup(window.deleteLater)
            event_filter = window._preset_file_drop_filter
            self.addCleanup(self._app.removeEventFilter, event_filter)
            enable_native_drop.reset_mock()
            window.event(QEvent(QEvent.Type.WinIdChange))

        enable_native_drop.assert_called_once_with(window)

    def test_collects_only_unique_existing_txt_files(self) -> None:
        from ui.window_preset_file_drop import dropped_preset_file_paths

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "First.txt"
            second = root / "SECOND.TXT"
            ignored = root / "notes.log"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")
            ignored.write_text("ignored", encoding="utf-8")

            mime_data = SimpleNamespace(
                hasUrls=lambda: True,
                urls=lambda: [
                    QUrl.fromLocalFile(str(first)),
                    QUrl.fromLocalFile(str(ignored)),
                    QUrl.fromLocalFile(str(second)),
                    QUrl.fromLocalFile(str(first)),
                    QUrl("https://example.com/remote.txt"),
                ],
            )

            self.assertEqual(
                dropped_preset_file_paths(mime_data),
                [str(first), str(second)],
            )

    def test_filter_accepts_drag_and_imports_drop_anywhere_in_own_window(self) -> None:
        from ui.window_preset_file_drop import WindowPresetFileDropFilter

        with tempfile.TemporaryDirectory() as tmp:
            preset_path = Path(tmp) / "Dragged.txt"
            preset_path.write_text("preset", encoding="utf-8")
            url = QUrl.fromLocalFile(str(preset_path))
            window = object()
            receiver = _Receiver(window)
            target = SimpleNamespace(import_dropped_preset_files=Mock(return_value=True))
            overlay = Mock()
            event_filter = WindowPresetFileDropFilter(
                window,
                target_resolver=lambda: target,
                overlay=overlay,
            )

            drag_event = _DropEvent(QEvent.Type.DragEnter, [url])
            self.assertTrue(event_filter.eventFilter(receiver, drag_event))
            self.assertTrue(drag_event.accepted)
            target.import_dropped_preset_files.assert_not_called()
            overlay.show_hint.assert_called_once_with([str(preset_path)])

            drop_event = _DropEvent(QEvent.Type.Drop, [url])
            self.assertTrue(event_filter.eventFilter(receiver, drop_event))
            self.assertTrue(drop_event.accepted)
            target.import_dropped_preset_files.assert_called_once_with([str(preset_path)])
            overlay.show_accepted.assert_called_once_with([str(preset_path)])
            overlay.hide_hint.assert_not_called()

            leave_event = _DropEvent(QEvent.Type.DragLeave, [])
            self.assertFalse(event_filter.eventFilter(receiver, leave_event))
            self.assertEqual(overlay.hide_hint.call_count, 1)

    def test_native_drop_uses_the_same_filter_import_path(self) -> None:
        from ui.window_preset_file_drop import (
            WindowPresetFileDropFilter,
            handle_native_preset_file_drop,
        )

        with tempfile.TemporaryDirectory() as tmp:
            preset_path = Path(tmp) / "Native.txt"
            preset_path.write_text("preset", encoding="utf-8")
            target = SimpleNamespace(import_dropped_preset_files=Mock(return_value=True))
            event_filter = WindowPresetFileDropFilter(
                object(),
                target_resolver=lambda: target,
                overlay=Mock(),
            )
            window = SimpleNamespace(_preset_file_drop_filter=event_filter)

            with patch(
                "ui.window_preset_file_drop.windows_dropped_file_paths",
                return_value=[str(preset_path)],
            ):
                handled = handle_native_preset_file_drop(window, object())

            self.assertTrue(handled)
            target.import_dropped_preset_files.assert_called_once_with([str(preset_path)])
            event_filter.overlay.show_accepted.assert_called_once_with([str(preset_path)])
            event_filter.overlay.hide_hint.assert_not_called()

    def test_native_drop_message_is_consumed_even_without_valid_txt(self) -> None:
        from ui.window_preset_file_drop import handle_native_preset_file_drop

        event_filter = Mock()
        window = SimpleNamespace(_preset_file_drop_filter=event_filter)
        with patch(
            "ui.window_preset_file_drop.windows_dropped_file_paths",
            return_value=["C:/Temp/ignored.log"],
        ):
            handled = handle_native_preset_file_drop(window, object())

        self.assertTrue(handled)
        event_filter.import_file_paths.assert_called_once_with(["C:/Temp/ignored.log"])

    def test_non_drop_native_message_is_not_consumed(self) -> None:
        from ui.window_preset_file_drop import handle_native_preset_file_drop

        window = SimpleNamespace(_preset_file_drop_filter=Mock())
        with patch(
            "ui.window_preset_file_drop.windows_dropped_file_paths",
            return_value=None,
        ):
            handled = handle_native_preset_file_drop(window, object())

        self.assertFalse(handled)
        window._preset_file_drop_filter.import_file_paths.assert_not_called()

    def test_overlay_covers_whole_window_and_uses_selected_language(self) -> None:
        from ui.window_preset_file_drop import PresetFileDropOverlay
        from PyQt6.QtWidgets import QWidget
        from qfluentwidgets import InfoBar

        window = QWidget()
        self.addCleanup(window.deleteLater)
        window.resize(900, 600)
        overlay = PresetFileDropOverlay(window, language_resolver=lambda: "en")
        overlay.show_hint(["C:/Temp/My preset.txt"])

        self.assertEqual(overlay.geometry(), window.rect())
        self.assertFalse(overlay.isHidden())
        self.assertIsInstance(overlay._info_bar, InfoBar)
        self.assertEqual(overlay._title, "Drop the file to import")
        self.assertEqual(overlay._subtitle, "My preset.txt")
        self.assertEqual(overlay._info_bar.title, overlay._title)
        self.assertEqual(overlay._info_bar.content, overlay._subtitle)
        self.assertFalse(overlay._info_bar.titleLabel.isHidden())
        self.assertFalse(overlay._info_bar.contentLabel.isHidden())
        self.assertEqual(overlay._info_bar.accessibleName(), overlay._title)
        self.assertEqual(overlay._info_bar.accessibleDescription(), overlay._subtitle)
        self.assertNotIn("paintEvent", PresetFileDropOverlay.__dict__)
        self.assertIsNone(overlay.graphicsEffect())
        self.assertIsNotNone(overlay._backdrop.graphicsEffect())
        self.assertIsNotNone(overlay._info_bar.graphicsEffect())

        overlay.hide_immediately()
        self.assertTrue(overlay.isHidden())

    def test_overlay_flashes_accepted_message_and_auto_hides(self) -> None:
        from ui.window_preset_file_drop import (
            ACCEPTED_FLASH_DURATION_MS,
            PresetFileDropOverlay,
        )
        from PyQt6.QtWidgets import QWidget

        window = QWidget()
        self.addCleanup(window.deleteLater)
        window.resize(900, 600)
        overlay = PresetFileDropOverlay(window, language_resolver=lambda: "en")

        overlay.show_accepted(["C:/Temp/My preset.txt"])

        self.assertFalse(overlay.isHidden())
        self.assertEqual(overlay._title, "File accepted — importing…")
        self.assertEqual(overlay._subtitle, "My preset.txt")
        self.assertTrue(overlay._auto_hide_timer.isActive())
        self.assertEqual(overlay._auto_hide_timer.interval(), ACCEPTED_FLASH_DURATION_MS)

        # Наведение снова показывает подсказку и отменяет авто-скрытие броска.
        overlay.show_hint(["C:/Temp/My preset.txt"])
        self.assertFalse(overlay._auto_hide_timer.isActive())
        self.assertEqual(overlay._title, "Drop the file to import")

        overlay.hide_immediately()
        self.assertTrue(overlay.isHidden())
        self.assertFalse(overlay._auto_hide_timer.isActive())

    def test_failed_import_hides_overlay_without_accepted_flash(self) -> None:
        from ui.window_preset_file_drop import WindowPresetFileDropFilter

        with tempfile.TemporaryDirectory() as tmp:
            preset_path = Path(tmp) / "Rejected.txt"
            preset_path.write_text("preset", encoding="utf-8")
            target = SimpleNamespace(import_dropped_preset_files=Mock(return_value=False))
            overlay = Mock()
            event_filter = WindowPresetFileDropFilter(
                object(),
                target_resolver=lambda: target,
                overlay=overlay,
            )

            imported = event_filter.import_file_paths([str(preset_path)])

            self.assertFalse(imported)
            overlay.show_accepted.assert_not_called()
            overlay.hide_hint.assert_called_once_with()

    def test_filter_ignores_other_windows_and_pages_without_import_action(self) -> None:
        from ui.window_preset_file_drop import WindowPresetFileDropFilter

        with tempfile.TemporaryDirectory() as tmp:
            preset_path = Path(tmp) / "Dragged.txt"
            preset_path.write_text("preset", encoding="utf-8")
            event = _DropEvent(QEvent.Type.Drop, [QUrl.fromLocalFile(str(preset_path))])
            window = object()

            other_window_filter = WindowPresetFileDropFilter(
                window,
                target_resolver=lambda: SimpleNamespace(import_dropped_preset_files=Mock()),
            )
            self.assertFalse(other_window_filter.eventFilter(_Receiver(object()), event))

            ordinary_page_filter = WindowPresetFileDropFilter(
                window,
                target_resolver=lambda: object(),
            )
            self.assertFalse(ordinary_page_filter.eventFilter(_Receiver(window), event))
            self.assertFalse(event.accepted)

    def test_user_presets_page_queues_every_dropped_file(self) -> None:
        from presets.ui.common.user_presets_page import UserPresetsPageBase

        page = UserPresetsPageBase.__new__(UserPresetsPageBase)
        page._cleanup_in_progress = False
        page._request_preset_bulk_action = Mock(return_value=True)

        result = UserPresetsPageBase.import_dropped_preset_files(
            page,
            ["C:/Temp/First.txt", "C:/Temp/Second.TXT"],
        )

        self.assertTrue(result)
        self.assertEqual(
            page._request_preset_bulk_action.call_args_list,
            [
                unittest.mock.call("import", file_path="C:/Temp/First.txt"),
                unittest.mock.call("import", file_path="C:/Temp/Second.TXT"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
