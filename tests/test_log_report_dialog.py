import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from ui.log_report_dialog import LogReportDialog


class LogReportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _host(self) -> QWidget:
        host = QWidget()
        host.resize(1600, 1000)
        self.addCleanup(host.deleteLater)
        return host

    def test_shows_colored_html_and_copies_plain_text(self) -> None:
        dialog = LogReportDialog(
            title="Подробный лог",
            text="строка 1\nстрока 2",
            html='<span style="color: #ff0000;">строка 1</span><br>строка 2',
            parent=self._host(),
        )
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.textEdit.toPlainText(), "строка 1\nстрока 2")
        self.assertIn("#ff0000", dialog.textEdit.toHtml())
        dialog.yesButton.click()
        self.assertEqual(QApplication.clipboard().text(), "строка 1\nстрока 2")
        self.assertEqual(dialog.yesButton.text(), "Скопировано")

    def test_empty_log_shows_hint_and_disables_copy(self) -> None:
        dialog = LogReportDialog(title="Подробный лог", text="", empty_text="Пусто.", parent=self._host())
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.textEdit.toPlainText(), "Пусто.")
        self.assertFalse(dialog.yesButton.isEnabled())

    def test_text_area_scales_with_program_window(self) -> None:
        dialog = LogReportDialog(title="Подробный лог", text="x", parent=self._host())
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.textEdit.minimumWidth(), 1200)
        self.assertEqual(dialog.textEdit.minimumHeight(), 650)


if __name__ == "__main__":
    unittest.main()
