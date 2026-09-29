"""Нижняя панель черновика страницы Hosts: что изменится и кнопка «Применить»."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, PlainTextEdit, PrimaryPushButton, PushButton, TransparentPushButton

from ui.accessibility import set_control_accessibility, set_state_text
from ui.theme import get_theme_tokens, to_qcolor


class HostsDraftBar(QWidget):
    """Появляется, когда в черновике есть изменения. Сам файл не трогает."""

    apply_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()
    preview_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hostsDraftBar")
        self._preview_open = False
        self._texts = {
            "show": "Показать строки",
            "hide": "Скрыть строки",
            "cancel": "Отменить",
            "apply": "Применить",
            "applying": "Записываю…",
        }
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        self.preview = PlainTextEdit(self)
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.preview.setFont(mono)
        self.preview.setFixedHeight(220)
        self.preview.hide()
        set_control_accessibility(self.preview, name="Строки, которые изменятся в hosts")
        root.addWidget(self.preview)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.summary_label = BodyLabel(self)
        self.summary_label.setWordWrap(True)
        row.addWidget(self.summary_label, 1)

        self.preview_button = TransparentPushButton(self)
        self.preview_button.clicked.connect(self._toggle_preview)
        row.addWidget(self.preview_button)

        self.cancel_button = PushButton(self)
        self.cancel_button.clicked.connect(self.cancel_clicked.emit)
        row.addWidget(self.cancel_button)

        self.apply_button = PrimaryPushButton(self)
        self.apply_button.clicked.connect(self.apply_clicked.emit)
        row.addWidget(self.apply_button)
        root.addLayout(row)

        self._sync_texts()

    def set_texts(self, **texts: str) -> None:
        self._texts.update({key: str(value) for key, value in texts.items() if value})
        self._sync_texts()

    def set_summary(self, text: str) -> None:
        self.summary_label.setText(str(text))
        set_state_text(self.summary_label, str(text))

    def set_preview_text(self, text: str) -> None:
        self.preview.setPlainText(str(text))

    def is_preview_open(self) -> bool:
        return self._preview_open

    def set_preview_open(self, opened: bool) -> None:
        self._preview_open = bool(opened)
        self.preview.setVisible(self._preview_open)
        self._sync_texts()
        self.adjustSize()

    def set_busy(self, busy: bool, *, can_cancel: bool = True) -> None:
        self._busy = bool(busy)
        self.apply_button.setEnabled(not self._busy)
        # Отменять нечего, если в черновике только лишние строки из файла.
        self.cancel_button.setEnabled(not self._busy and bool(can_cancel))
        self._sync_texts()

    def _toggle_preview(self) -> None:
        self.set_preview_open(not self._preview_open)
        self.preview_toggled.emit(self._preview_open)

    def _sync_texts(self) -> None:
        self.preview_button.setText(self._texts["hide" if self._preview_open else "show"])
        self.cancel_button.setText(self._texts["cancel"])
        self.apply_button.setText(self._texts["applying" if self._busy else "apply"])
        set_control_accessibility(self.apply_button, name=self.apply_button.text())
        set_control_accessibility(self.cancel_button, name=self.cancel_button.text())
        set_control_accessibility(self.preview_button, name=self.preview_button.text())

    def paintEvent(self, event) -> None:  # noqa: N802
        # Панель висит поверх списка, поэтому подложка непрозрачная.
        tokens = get_theme_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f5f5f5") if tokens.is_light else QColor("#2c2c2c"))
        painter.drawRoundedRect(rect, 10, 10)
        painter.setBrush(to_qcolor(tokens.accent_soft_bg))
        painter.setPen(to_qcolor(tokens.surface_border))
        painter.drawRoundedRect(rect, 10, 10)
        painter.end()


__all__ = ["HostsDraftBar"]
