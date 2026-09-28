"""Список подсказок при наборе (автодополнение) внутри редактора.

Окошко — обычный дочерний виджет области текста, а не отдельное окно:
фокус остаётся в редакторе (печатать можно дальше), и на Windows не
появляется лишних окон. Клавиши (стрелки, Enter, Esc) перехватывает сам
редактор и вызывает методы этого виджета.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QFrame, QListWidgetItem, QVBoxLayout
from qfluentwidgets import CaptionLabel, ListWidget

from ui.accessibility import set_control_accessibility, set_state_text

MAX_VISIBLE_ROWS = 9
POPUP_MIN_WIDTH = 320
POPUP_MAX_WIDTH = 560


class CompletionPopup(QFrame):
    """Список вариантов и описание выбранного варианта под ним."""

    rowAccepted = pyqtSignal(int)

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setObjectName("codeEditorCompletionPopup")
        self.setProperty("noDrag", True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._details: list[str] = []

        self.list_widget = ListWidget(self)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.currentRowChanged.connect(self._on_current_row_changed)

        self.detail_label = CaptionLabel("", self)
        self.detail_label.setWordWrap(True)
        self.detail_label.setContentsMargins(8, 2, 8, 4)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(self.list_widget)
        layout.addWidget(self.detail_label)

        name = "Подсказки редактора"
        set_control_accessibility(
            self,
            name=name,
            description="Стрелки вверх и вниз выбирают вариант, Enter вставляет его, Esc закрывает список.",
        )
        set_state_text(self, name)
        self.hide()

    # ------------------------------------------------------------ содержимое

    def set_items(self, labels, details, *, font=None) -> None:
        self._details = [str(detail or "") for detail in details]
        self.list_widget.clear()
        row_height = 24
        if font is not None:
            self.list_widget.setFont(font)
            try:
                row_height = max(22, self.list_widget.fontMetrics().height() + 10)
            except Exception:
                pass
        for label in labels:
            item = QListWidgetItem(str(label))
            item.setSizeHint(QSize(0, row_height))
            self.list_widget.addItem(item)
        self._row_height = row_height
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self._on_current_row_changed(self.list_widget.currentRow())

    def row_count(self) -> int:
        return self.list_widget.count()

    def current_row(self) -> int:
        return self.list_widget.currentRow()

    def move_selection(self, delta: int) -> None:
        count = self.list_widget.count()
        if not count:
            return
        row = self.list_widget.currentRow()
        if row < 0:
            row = 0
        elif abs(delta) == 1:
            row = (row + delta) % count
        else:
            row = max(0, min(count - 1, row + delta))
        self.list_widget.setCurrentRow(row)

    def preferred_size(self, available_width: int) -> QSize:
        rows = min(MAX_VISIBLE_ROWS, max(1, self.list_widget.count()))
        width = max(POPUP_MIN_WIDTH, min(POPUP_MAX_WIDTH, int(available_width)))
        detail_height = self.detail_label.heightForWidth(width - 16) if self.detail_label.text() else 0
        height = rows * getattr(self, "_row_height", 24) + 8 + max(0, detail_height) + 8
        return QSize(width, height)

    def apply_colors(self, *, background: QColor, border: QColor) -> None:
        self.setStyleSheet(
            "#codeEditorCompletionPopup {"
            f" background-color: {background.name(QColor.NameFormat.HexArgb)};"
            f" border: 1px solid {border.name(QColor.NameFormat.HexArgb)};"
            " border-radius: 8px; }"
        )

    # --------------------------------------------------------------- события

    def _on_item_clicked(self, item) -> None:
        row = self.list_widget.row(item)
        if row >= 0:
            self.rowAccepted.emit(row)

    def _on_current_row_changed(self, row: int) -> None:
        detail = self._details[row] if 0 <= row < len(self._details) else ""
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        item = self.list_widget.item(row) if row >= 0 else None
        state = item.text() if item is not None else "Подсказки редактора"
        if detail:
            state = f"{state}: {detail}"
        set_state_text(self, state)


__all__ = ["CompletionPopup"]
