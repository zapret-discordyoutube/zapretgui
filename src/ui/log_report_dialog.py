"""Окно с подробным текстовым логом/отчётом.

Подробный лог открывается по кнопке в отдельном окне, а не висит на странице
маленькой панелью: так его удобно читать целиком и скопировать для поддержки.
"""

from __future__ import annotations

from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import StrongBodyLabel

from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_dialog import MessageBoxBase
from ui.message_box_accessibility import set_message_box_button_accessibility
from ui.pages.base_page import ScrollBlockingTextEdit


_MIN_WIDTH = 760
_MIN_HEIGHT = 460
_MAX_WIDTH = 1400
_MAX_HEIGHT = 900


class LogReportDialog(MessageBoxBase):
    """Показывает лог; «Скопировать» кладёт его текст в буфер обмена."""

    def __init__(
        self,
        *,
        title: str,
        text: str,
        html: str | None = None,
        empty_text: str = "Лог пока пуст.",
        description: str = "",
        scroll_to_end: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._text = str(text or "")

        self.titleLabel = StrongBodyLabel(title, self)
        self.viewLayout.addWidget(self.titleLabel)

        self.textEdit = ScrollBlockingTextEdit(self)
        self.textEdit.setReadOnly(True)
        self.textEdit.setFont(QFont("Consolas", 9))
        if html and self._text:
            self.textEdit.setHtml(html)
        else:
            self.textEdit.setPlainText(self._text or empty_text)
        if scroll_to_end:
            self.textEdit.moveCursor(QTextCursor.MoveOperation.End)
        width, height = _dialog_text_size(parent)
        self.textEdit.setMinimumSize(width, height)
        set_control_accessibility(self.textEdit, name=title, description=description)
        set_state_text(self.textEdit, title)
        self.viewLayout.addWidget(self.textEdit)

        self.yesButton.setText("Скопировать")
        self.yesButton.setEnabled(bool(self._text))
        self.cancelButton.setText("Закрыть")
        set_message_box_button_accessibility(
            self,
            yes_name="Скопировать лог",
            yes_description="Копирует весь текст в буфер обмена.",
            cancel_name="Закрыть окно",
            cancel_description="Закрывает окно с подробным логом.",
        )
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._copy)

    def _copy(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._text)
        self.yesButton.setText("Скопировано")


def _dialog_text_size(parent) -> tuple[int, int]:
    """Поле лога занимает большую часть окна программы, но не меньше минимума."""
    try:
        window = parent.window() if parent is not None else None
        host_width = int(window.width()) if window is not None else 0
        host_height = int(window.height()) if window is not None else 0
    except Exception:
        host_width = host_height = 0
    width = max(_MIN_WIDTH, min(_MAX_WIDTH, int(host_width * 0.75)))
    height = max(_MIN_HEIGHT, min(_MAX_HEIGHT, int(host_height * 0.65)))
    return width, height


def show_log_report_dialog(parent, **kwargs) -> None:
    LogReportDialog(parent=parent, **kwargs).exec()


__all__ = ["LogReportDialog", "show_log_report_dialog"]
