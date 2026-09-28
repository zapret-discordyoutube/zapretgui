"""Список подсказок при наборе (автодополнение) внутри редактора.

Компактный список как в редакторах кода: низкие строки, шрифт редактора,
описание варианта бледным текстом справа. Окошко — обычный дочерний виджет
области текста, а не отдельное окно: фокус остаётся в редакторе (печатать
можно дальше), и на Windows не появляется лишних окон. Клавиши (стрелки,
Enter, Esc) перехватывает сам редактор и вызывает методы этого виджета.
Колесо мыши над списком не крутит список, а закрывает его и прокручивает
текст (сигнал ``wheelScrolled``).
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
)

from ui.accessibility import set_control_accessibility, set_state_text

MAX_VISIBLE_ROWS = 7
MIN_VISIBLE_ROWS = 3
POPUP_MIN_WIDTH = 220
POPUP_MAX_WIDTH = 520
DETAIL_MAX_WIDTH = 280
_DETAIL_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_H_PADDING = 8
_GAP = 16


class _CompletionItemDelegate(QStyledItemDelegate):
    """Строка списка: вариант слева, описание бледным справа."""

    def __init__(self, popup) -> None:
        super().__init__(popup)
        self._popup = popup

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(0, self._popup.row_height())

    def paint(self, painter: QPainter, option, index) -> None:
        popup = self._popup
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(option.rect).adjusted(2, 1, -2, -1)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(popup.selection_color)
            painter.drawRoundedRect(rect, 4, 4)

        label = str(index.data(int(Qt.ItemDataRole.DisplayRole)) or "")
        detail = str(index.data(_DETAIL_ROLE) or "")
        text_rect = rect.adjusted(_H_PADDING, 0, -_H_PADDING, 0)

        painter.setFont(popup.label_font)
        label_metrics = QFontMetrics(popup.label_font)
        label_width = min(int(text_rect.width()), label_metrics.horizontalAdvance(label))
        painter.setPen(popup.text_color)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            label_metrics.elidedText(label, Qt.TextElideMode.ElideRight, int(text_rect.width())),
        )

        detail_space = int(text_rect.width()) - label_width - _GAP
        if detail and detail_space > 40:
            painter.setFont(popup.detail_font)
            detail_metrics = QFontMetrics(popup.detail_font)
            painter.setPen(popup.muted_color)
            painter.drawText(
                text_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                detail_metrics.elidedText(detail, Qt.TextElideMode.ElideRight, detail_space),
            )
        painter.restore()


class CompletionPopup(QFrame):
    """Компактный список вариантов подсказки."""

    rowAccepted = pyqtSignal(int)
    wheelScrolled = pyqtSignal(object)

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setObjectName("codeEditorCompletionPopup")
        self.setProperty("noDrag", True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.label_font = QFont(self.font())
        self.detail_font = QFont(self.font())
        self.text_color = QColor(0, 0, 0)
        self.muted_color = QColor(0, 0, 0, 130)
        self.selection_color = QColor(0, 120, 212, 45)

        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("codeEditorCompletionList")
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setMouseTracking(False)
        self.list_widget.setItemDelegate(_CompletionItemDelegate(self))
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.currentRowChanged.connect(self._on_current_row_changed)
        self.list_widget.viewport().installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(0)
        layout.addWidget(self.list_widget)

        name = "Подсказки редактора"
        set_control_accessibility(
            self,
            name=name,
            description="Стрелки вверх и вниз выбирают вариант, Enter вставляет его, Esc закрывает список.",
        )
        set_state_text(self, name)
        self.hide()

    # ------------------------------------------------------------ содержимое

    def row_height(self) -> int:
        return QFontMetrics(self.label_font).height() + 6

    def set_fonts(self, editor_font: QFont) -> None:
        self.label_font = QFont(editor_font)
        detail = QFont(self.font())
        size = editor_font.pointSizeF()
        if size > 0:
            detail.setPointSizeF(max(7.0, size - 1))
        self.detail_font = detail

    def set_items(self, labels, details, *, font=None) -> None:
        if font is not None:
            self.set_fonts(font)
        self.list_widget.setUpdatesEnabled(False)
        try:
            self.list_widget.clear()
            for label, detail in zip(labels, details):
                item = QListWidgetItem(str(label))
                item.setData(_DETAIL_ROLE, str(detail or ""))
                item.setToolTip(str(detail or ""))
                self.list_widget.addItem(item)
            if self.list_widget.count():
                self.list_widget.setCurrentRow(0)
        finally:
            self.list_widget.setUpdatesEnabled(True)
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

    def preferred_size(self, max_width: int, max_rows: int = MAX_VISIBLE_ROWS) -> QSize:
        """Размер по содержимому: ширина по самому длинному варианту."""
        label_metrics = QFontMetrics(self.label_font)
        detail_metrics = QFontMetrics(self.detail_font)
        label_width = 0
        detail_width = 0
        for row in range(min(self.list_widget.count(), 200)):
            item = self.list_widget.item(row)
            label_width = max(label_width, label_metrics.horizontalAdvance(item.text()))
            detail = str(item.data(_DETAIL_ROLE) or "")
            if detail:
                detail_width = max(detail_width, min(DETAIL_MAX_WIDTH, detail_metrics.horizontalAdvance(detail)))
        width = label_width + (detail_width + _GAP if detail_width else 0) + 2 * _H_PADDING + 4 + 6
        width = max(POPUP_MIN_WIDTH, min(POPUP_MAX_WIDTH, int(max_width), width))
        rows = min(max(1, int(max_rows)), max(1, self.list_widget.count()))
        return QSize(width, rows * self.row_height() + 6)

    def apply_colors(self, *, background: QColor, border: QColor, text: QColor, muted: QColor,
                     selection: QColor) -> None:
        self.text_color = QColor(text)
        self.muted_color = QColor(muted)
        self.selection_color = QColor(selection)
        self.setStyleSheet(
            "#codeEditorCompletionPopup {"
            f" background-color: {background.name(QColor.NameFormat.HexArgb)};"
            f" border: 1px solid {border.name(QColor.NameFormat.HexArgb)};"
            " border-radius: 6px; }"
            "#codeEditorCompletionList { background: transparent; border: none; outline: none; }"
        )
        self.list_widget.viewport().update()

    # --------------------------------------------------------------- события

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Type.Wheel:
            # Колесо крутит текст, а не список: список закрывается.
            self.wheelScrolled.emit(event)
            return True
        return super().eventFilter(obj, event)

    def _on_item_clicked(self, item) -> None:
        row = self.list_widget.row(item)
        if row >= 0:
            self.rowAccepted.emit(row)

    def _on_current_row_changed(self, row: int) -> None:
        item = self.list_widget.item(row) if row >= 0 else None
        if item is None:
            set_state_text(self, "Подсказки редактора")
            return
        detail = str(item.data(_DETAIL_ROLE) or "")
        set_state_text(self, f"{item.text()}: {detail}" if detail else item.text())


__all__ = ["CompletionPopup"]
