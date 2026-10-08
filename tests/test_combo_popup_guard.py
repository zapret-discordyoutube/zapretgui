"""Выпадающие списки закрываются, когда программа теряет активность."""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QApplication, QLabel

from ui.combo_popup_guard import GlobalComboPopupCloser


class ComboPopupGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _closer(self) -> GlobalComboPopupCloser:
        closer = GlobalComboPopupCloser(self._app)
        self.addCleanup(closer.deleteLater)
        self.addCleanup(closer.cleanup)
        return closer

    def test_popups_close_when_the_application_becomes_inactive(self) -> None:
        closer = self._closer()
        with mock.patch.object(closer, "close_all_popups") as close:
            self._app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)
            close.assert_called_once()
            close.reset_mock()
            self._app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
            close.assert_not_called()

    def test_closer_does_not_watch_every_event_of_the_application(self) -> None:
        # Раньше это был перехватчик на всё приложение: через функцию на Python
        # проходило каждое событие каждого объекта.
        closer = self._closer()
        self.assertNotIn("eventFilter", vars(GlobalComboPopupCloser))
        with mock.patch.object(closer, "close_all_popups") as close:
            label = QLabel("текст")
            self.addCleanup(label.deleteLater)
            QApplication.sendEvent(label, QEvent(QEvent.Type.ApplicationDeactivate))
            close.assert_not_called()

    def test_cleanup_stops_listening(self) -> None:
        closer = GlobalComboPopupCloser(self._app)
        self.addCleanup(closer.deleteLater)
        closer.cleanup()
        with mock.patch.object(closer, "close_all_popups") as close:
            self._app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)
            close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
