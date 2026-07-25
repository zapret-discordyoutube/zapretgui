from __future__ import annotations

import ast
import gc
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QWidget

from ui.close_dialog import CloseDialog
from ui.fluent_dialog import MessageBox


ROOT = Path(__file__).resolve().parents[1]


class _EventFilterTrackingParent(QWidget):
    def installEventFilter(self, event_filter) -> None:  # noqa: N802
        self.__dict__.setdefault("installed_filters", []).append(event_filter)
        super().installEventFilter(event_filter)

    def removeEventFilter(self, event_filter) -> None:  # noqa: N802
        self.__dict__.setdefault("removed_filters", []).append(event_filter)
        super().removeEventFilter(event_filter)


class FluentDialogLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _parent(self) -> _EventFilterTrackingParent:
        parent = _EventFilterTrackingParent()
        parent.resize(640, 480)
        parent.show()
        self.addCleanup(parent.deleteLater)
        return parent

    def test_close_dialog_detaches_from_parent_event_filter_after_exec(self) -> None:
        parent = self._parent()
        dialog = CloseDialog(parent, launch_running=True)
        self.addCleanup(dialog.deleteLater)

        self.assertIn(dialog, parent.installed_filters)
        QTimer.singleShot(0, dialog.reject)
        self.assertEqual(dialog.exec(), 0)
        self.assertIn(dialog, parent.removed_filters)
        self.assertIsNone(dialog._mask_event_filter_host)

    def test_standard_message_box_uses_same_managed_lifecycle(self) -> None:
        parent = self._parent()
        dialog = MessageBox("Проверка", "Текст", parent)
        self.addCleanup(dialog.deleteLater)

        self.assertIn(dialog, parent.installed_filters)
        QTimer.singleShot(0, dialog.reject)
        self.assertEqual(dialog.exec(), 0)
        self.assertIn(dialog, parent.removed_filters)

    def test_closed_dialog_does_not_receive_later_parent_events(self) -> None:
        parent = QWidget()
        parent.resize(640, 480)
        parent.show()
        self.addCleanup(parent.deleteLater)
        uncaught: list[BaseException] = []
        previous_excepthook = sys.excepthook
        sys.excepthook = lambda exc_type, exc, traceback: uncaught.append(exc)
        try:
            for width in (660, 680):
                dialog = CloseDialog(parent, launch_running=True)
                QTimer.singleShot(0, dialog.reject)
                self.assertEqual(dialog.exec(), 0)
                del dialog
                gc.collect()
                parent.resize(width, 480)
                self.app.processEvents()
        finally:
            sys.excepthook = previous_excepthook

        self.assertEqual(uncaught, [])

    def test_event_filter_is_silent_without_window_mask_attribute(self) -> None:
        # Регрессия v21.1.5.19: событие приходило в eventFilter во время
        # зачистки Python-объекта, когда windowMask уже удалён →
        # AttributeError: 'CloseDialog' object has no attribute 'windowMask'.
        from PyQt6.QtCore import QEvent

        parent = self._parent()
        dialog = CloseDialog(parent, launch_running=True)
        self.addCleanup(dialog.deleteLater)

        dialog.__dict__.pop("windowMask")

        result = dialog.eventFilter(parent, QEvent(QEvent.Type.Resize))

        self.assertFalse(result)

    def test_event_filter_is_silent_without_center_widget_attribute(self) -> None:
        from PyQt6.QtCore import QEvent

        parent = self._parent()
        dialog = CloseDialog(parent, launch_running=True)
        self.addCleanup(dialog.deleteLater)
        center_widget = dialog.widget

        dialog.__dict__.pop("widget")

        result = dialog.eventFilter(center_widget, QEvent(QEvent.Type.Resize))

        self.assertFalse(result)

    def test_exec_detaches_window_mask_and_center_widget_filters(self) -> None:
        parent = self._parent()
        dialog = CloseDialog(parent, launch_running=True)
        self.addCleanup(dialog.deleteLater)

        removed: list[object] = []

        def _track_removal(target):
            original = target.removeEventFilter

            def _tracking_remove(event_filter):
                removed.append((target, event_filter))
                original(event_filter)

            target.removeEventFilter = _tracking_remove

        _track_removal(dialog.windowMask)
        _track_removal(dialog.widget)

        QTimer.singleShot(0, dialog.reject)
        self.assertEqual(dialog.exec(), 0)

        self.assertIn((dialog.windowMask, dialog), removed)
        self.assertIn((dialog.widget, dialog), removed)

    def test_application_does_not_import_unmanaged_message_boxes(self) -> None:
        offenders: list[str] = []
        for path in (ROOT / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module != "qfluentwidgets":
                    continue
                if any(alias.name in {"MessageBox", "MessageBoxBase"} for alias in node.names):
                    offenders.append(path.relative_to(ROOT).as_posix())

        self.assertEqual(offenders, ["src/ui/fluent_dialog.py"])


if __name__ == "__main__":
    unittest.main()
