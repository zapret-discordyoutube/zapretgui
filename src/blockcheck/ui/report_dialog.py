"""Окно с подробным техническим отчётом BlockCheck.

Отчёт открывается по кнопке, а не висит на странице: новичку он не нужен, а
поддержке — нужен целиком (адреса, ответы DNS, время ответа каждого сервера).
"""

from __future__ import annotations

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import StrongBodyLabel

from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_dialog import MessageBoxBase
from ui.message_box_accessibility import set_message_box_button_accessibility
from ui.pages.base_page import ScrollBlockingTextEdit


class BlockcheckReportDialog(MessageBoxBase):
    """Показывает текст отчёта; «Скопировать» кладёт его в буфер обмена."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self._text = str(text or "")

        self.titleLabel = StrongBodyLabel("Подробный отчёт BlockCheck", self)
        self.viewLayout.addWidget(self.titleLabel)

        self.textEdit = ScrollBlockingTextEdit(self)
        self.textEdit.setReadOnly(True)
        self.textEdit.setFont(QFont("Consolas", 9))
        self.textEdit.setPlainText(self._text or "Проверка ещё не запускалась.")
        self.textEdit.setMinimumSize(760, 460)
        set_control_accessibility(
            self.textEdit,
            name="Подробный отчёт BlockCheck",
            description="Технические подробности проверки: адреса, ответы DNS и время ответа серверов.",
        )
        set_state_text(self.textEdit, "Подробный отчёт BlockCheck")
        self.viewLayout.addWidget(self.textEdit)

        self.yesButton.setText("Скопировать")
        self.cancelButton.setText("Закрыть")
        set_message_box_button_accessibility(
            self,
            yes_name="Скопировать отчёт",
            yes_description="Копирует весь отчёт в буфер обмена.",
            cancel_name="Закрыть отчёт",
            cancel_description="Закрывает окно отчёта.",
        )
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._copy)

    def _copy(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._text)
        self.yesButton.setText("Скопировано")


def show_report_dialog(parent, text: str) -> None:
    BlockcheckReportDialog(text, parent).exec()
