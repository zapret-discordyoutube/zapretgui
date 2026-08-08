from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


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
            overlay.hide_hint.assert_called_once_with()

            leave_event = _DropEvent(QEvent.Type.DragLeave, [])
            self.assertFalse(event_filter.eventFilter(receiver, leave_event))
            self.assertEqual(overlay.hide_hint.call_count, 2)

    def test_overlay_covers_whole_window_and_uses_selected_language(self) -> None:
        from ui.window_preset_file_drop import PresetFileDropOverlay
        from PyQt6.QtWidgets import QWidget

        window = QWidget()
        self.addCleanup(window.deleteLater)
        window.resize(900, 600)
        overlay = PresetFileDropOverlay(window, language_resolver=lambda: "en")
        overlay.show_hint(["C:/Temp/My preset.txt"])

        self.assertEqual(overlay.geometry(), window.rect())
        self.assertFalse(overlay.isHidden())
        self.assertEqual(overlay._title, "Drop to import the preset")
        self.assertEqual(overlay._subtitle, "TXT file: My preset.txt")

        overlay.hide_immediately()
        self.assertTrue(overlay.isHidden())

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
