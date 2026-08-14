"""Текстовый редактор с номерами строк, подсветкой и построчными операциями.

Переиспользуемый виджет: ничего не знает о пресетах, профилях и файлах —
только текст. Конкретную подсветку синтаксиса задаёт вызывающая сторона
через `highlighter_factory`.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QTextCursor, QTextFormat
from PyQt6.QtWidgets import QTextEdit
from qfluentwidgets import PlainTextEdit, isDarkTheme, themeColor

from ui.code_editor.line_number_area import LineNumberArea
from ui.code_editor.line_ops import (
    delete_lines,
    duplicate_lines,
    indent_lines,
    move_lines,
    toggle_comment,
    unindent_lines,
)
from ui.smooth_scroll import apply_editor_smooth_scroll_preference
from ui.theme_refresh import ThemeRefreshBinding

MIN_POINT_SIZE = 6
MAX_POINT_SIZE = 32
DEFAULT_POINT_SIZE = 10
MONOSPACE_FAMILIES = (
    "Consolas",
    "Cascadia Mono",
    "JetBrains Mono",
    "DejaVu Sans Mono",
    "Courier New",
    "monospace",
)


@dataclass(frozen=True, slots=True)
class CursorStatus:
    line: int
    column: int
    selected_characters: int
    selected_lines: int
    total_lines: int


def build_cursor_status_text(status: CursorStatus) -> str:
    """Текст индикатора позиции курсора для статус-бара."""
    parts = [f"Стр {status.line}, кол {status.column}"]
    if status.selected_characters > 0:
        selection = f"выделено {status.selected_characters}"
        if status.selected_lines > 1:
            selection += f" в {status.selected_lines} стр."
        parts.append(selection)
    parts.append(f"всего строк: {status.total_lines}")
    return " · ".join(parts)


class CodeEditor(PlainTextEdit):
    """PlainTextEdit с колонкой номеров строк и хоткеями Notepad++-стиля."""

    contentEdited = pyqtSignal()
    cursorStatusChanged = pyqtSignal(object)
    findRequested = pyqtSignal(bool)
    findNextRequested = pyqtSignal(bool)
    gotoLineRequested = pyqtSignal()
    escapePressed = pyqtSignal()

    def __init__(self, parent=None, *, highlighter_factory=None) -> None:
        super().__init__(parent)
        self.setProperty("noDrag", True)
        self._line_number_area = LineNumberArea(self)
        self._search_selections: tuple = ()
        self._current_match_range: tuple[int, int] | None = None
        self._base_point_size = DEFAULT_POINT_SIZE
        self._line_number_color = QColor("#8b949e")
        self._line_number_current_color = QColor("#c9d1d9")
        self._line_number_bg = QColor(0, 0, 0, 0)
        self._current_line_color = QColor(255, 255, 255, 10)
        self._match_color = QColor(255, 214, 0, 70)
        self._current_match_color = QColor(255, 150, 50, 120)
        self._highlighter = None
        self._suppress_content_signals = False

        self.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        # Одиночный Tab отдаётся навигации по фокусу (accessibility); отступ
        # блока перехватывается раньше — только при выделении нескольких строк.
        self.setTabChangesFocus(True)
        self._apply_monospace_font(DEFAULT_POINT_SIZE)
        apply_editor_smooth_scroll_preference(self)

        self.blockCountChanged.connect(self._on_block_count_changed)
        self.textChanged.connect(self._on_text_changed)
        self.updateRequest.connect(self._on_update_request)
        self.cursorPositionChanged.connect(self._on_cursor_position_changed)
        self.selectionChanged.connect(self._emit_cursor_status)

        if callable(highlighter_factory):
            try:
                self._highlighter = highlighter_factory(self.document())
            except Exception:
                self._highlighter = None

        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme)
        self._apply_theme()
        self._update_line_number_area_width()
        self._refresh_extra_selections()

    # ---------------------------------------------------------------- шрифт

    def _apply_monospace_font(self, point_size: int) -> None:
        font = QFont(self.font())
        font.setFamilies(list(MONOSPACE_FAMILIES))
        font.setFamily(MONOSPACE_FAMILIES[0])
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        font.setPointSize(int(point_size))
        self.setFont(font)
        self.document().setDefaultFont(font)
        try:
            self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        except Exception:
            pass

    def current_point_size(self) -> int:
        size = int(self.font().pointSize())
        return size if size > 0 else DEFAULT_POINT_SIZE

    def set_point_size(self, point_size: int) -> bool:
        target = max(MIN_POINT_SIZE, min(MAX_POINT_SIZE, int(point_size)))
        if target == self.current_point_size():
            return False
        self._apply_monospace_font(target)
        self._update_line_number_area_width()
        self._line_number_area.update()
        return True

    def zoom_by(self, steps: int) -> bool:
        return self.set_point_size(self.current_point_size() + int(steps))

    def reset_zoom(self) -> bool:
        return self.set_point_size(self._base_point_size)

    # -------------------------------------------------------------- номера строк

    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        try:
            advance = self.fontMetrics().horizontalAdvance("9")
        except Exception:
            advance = 8
        return 14 + advance * digits

    def _update_line_number_area_width(self) -> None:
        width = self.line_number_area_width()
        self.setViewportMargins(width, 0, 0, 0)
        self._line_number_area.setFixedWidth(width)

    def _on_block_count_changed(self, _count: int) -> None:
        self._update_line_number_area_width()
        self._emit_cursor_status()

    def _on_update_request(self, rect: QRect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(0, rect.y(), self._line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        contents = self.contentsRect()
        self._line_number_area.setGeometry(
            QRect(contents.left(), contents.top(), self.line_number_area_width(), contents.height())
        )

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self._line_number_area)
        if self._line_number_bg.alpha():
            painter.fillRect(event.rect(), self._line_number_bg)

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        offset = self.contentOffset()
        top = self.blockBoundingGeometry(block).translated(offset).top()
        bottom = top + self.blockBoundingRect(block).height()
        current_block = self.textCursor().blockNumber()
        width = self._line_number_area.width() - 8
        height = self.fontMetrics().height()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                is_current = block_number == current_block
                painter.setPen(
                    self._line_number_current_color if is_current else self._line_number_color
                )
                painter.drawText(
                    0,
                    int(top),
                    width,
                    height,
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    str(block_number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1
        painter.end()

    # ------------------------------------------------------------------ тема

    def _apply_theme(self, *, tokens=None) -> None:
        is_light = bool(getattr(tokens, "is_light", None)) if tokens is not None else not isDarkTheme()
        try:
            accent = QColor(themeColor())
        except Exception:
            accent = QColor("#0078d4")

        if is_light:
            self._line_number_color = QColor(0, 0, 0, 90)
            self._line_number_current_color = QColor(0, 0, 0, 190)
            self._current_line_color = QColor(0, 0, 0, 10)
            self._match_color = QColor(255, 200, 0, 110)
        else:
            self._line_number_color = QColor(255, 255, 255, 90)
            self._line_number_current_color = QColor(255, 255, 255, 210)
            self._current_line_color = QColor(255, 255, 255, 14)
            self._match_color = QColor(255, 214, 0, 80)

        current = QColor(accent)
        current.setAlpha(150)
        self._current_match_color = current

        self._apply_highlighter_theme(is_light)

        self._refresh_extra_selections()
        self._line_number_area.update()

    def _apply_highlighter_theme(self, is_light: bool) -> None:
        """Перекрашивает синтаксис, не выдавая это за правку документа.

        QSyntaxHighlighter.rehighlight() открывает edit-block на документе и
        по его закрытию Qt эмитит textChanged, хотя текст не менялся. Без
        подавления смена темы помечала пресет изменённым и запускала
        автосохранение — поэтому наружу отдаётся contentEdited, а не
        сырой textChanged.
        """
        setter = getattr(self._highlighter, "set_light_theme", None)
        if not callable(setter):
            return
        self._suppress_content_signals = True
        try:
            setter(bool(is_light))
        except Exception:
            pass
        finally:
            self._suppress_content_signals = False

    def _on_text_changed(self) -> None:
        if self._suppress_content_signals:
            return
        self.contentEdited.emit()

    # -------------------------------------------------------------- подсветка

    def set_search_highlights(self, matches, current_index: int | None = None) -> None:
        """Подсвечивает все совпадения; текущее — отдельным цветом."""
        items = tuple(matches or ())
        self._search_selections = items
        if current_index is None or not (0 <= int(current_index) < len(items)):
            self._current_match_range = None
        else:
            match = items[int(current_index)]
            self._current_match_range = (int(match.start), int(match.end))
        self._refresh_extra_selections()

    def clear_search_highlights(self) -> None:
        self._search_selections = ()
        self._current_match_range = None
        self._refresh_extra_selections()

    def _refresh_extra_selections(self) -> None:
        selections = []

        current_line = QTextEdit.ExtraSelection()
        current_line.format.setBackground(self._current_line_color)
        current_line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        cursor = self.textCursor()
        cursor.clearSelection()
        current_line.cursor = cursor
        selections.append(current_line)

        document = self.document()
        # Позиции совпадений могли устареть: правка документа приходит раньше,
        # чем контроллер успевает пересчитать поиск.
        limit = max(0, document.characterCount() - 1)
        for match in self._search_selections:
            start = max(0, min(int(match.start), limit))
            end = max(start, min(int(match.end), limit))
            if end <= start:
                continue
            selection = QTextEdit.ExtraSelection()
            is_current = (
                self._current_match_range is not None
                and (int(match.start), int(match.end)) == self._current_match_range
            )
            selection.format.setBackground(
                self._current_match_color if is_current else self._match_color
            )
            match_cursor = QTextCursor(document)
            match_cursor.setPosition(start)
            match_cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selection.cursor = match_cursor
            selections.append(selection)

        self.setExtraSelections(selections)

    # -------------------------------------------------------- позиция курсора

    def cursor_status(self) -> CursorStatus:
        cursor = self.textCursor()
        selected = cursor.selectedText()
        selected_lines = 0
        if cursor.hasSelection():
            start_block = self.document().findBlock(cursor.selectionStart()).blockNumber()
            end_block = self.document().findBlock(cursor.selectionEnd()).blockNumber()
            selected_lines = abs(end_block - start_block) + 1
        return CursorStatus(
            line=cursor.blockNumber() + 1,
            column=cursor.positionInBlock() + 1,
            selected_characters=len(selected),
            selected_lines=selected_lines,
            total_lines=max(1, self.blockCount()),
        )

    def _on_cursor_position_changed(self) -> None:
        self._refresh_extra_selections()
        self._emit_cursor_status()

    def _emit_cursor_status(self) -> None:
        try:
            self.cursorStatusChanged.emit(self.cursor_status())
        except Exception:
            pass

    def goto_line(self, line_number: int) -> bool:
        """Ставит курсор в начало указанной строки (1-based)."""
        total = max(1, self.blockCount())
        target = max(1, min(int(line_number), total))
        block = self.document().findBlockByNumber(target - 1)
        if not block.isValid():
            return False
        cursor = self.textCursor()
        cursor.setPosition(block.position())
        self.setTextCursor(cursor)
        self.centerCursor()
        return True

    def select_range(self, start: int, end: int) -> None:
        cursor = self.textCursor()
        cursor.setPosition(int(start))
        cursor.setPosition(int(end), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    # ------------------------------------------------------ построчные операции

    def _document_lines(self) -> list[str]:
        return self.toPlainText().split("\n")

    def _selected_line_range(self) -> tuple[int, int]:
        cursor = self.textCursor()
        document = self.document()
        first = document.findBlock(cursor.selectionStart()).blockNumber()
        last = document.findBlock(cursor.selectionEnd()).blockNumber()
        if last > first and cursor.selectionEnd() == document.findBlockByNumber(last).position():
            # выделение заканчивается ровно на начале строки — её не трогаем
            last -= 1
        return first, last

    def _apply_line_plan(self, plan) -> bool:
        if plan is None:
            return False
        document = self.document()
        source_cursor = self.textCursor()
        had_selection = source_cursor.hasSelection()
        selection_start = source_cursor.selectionStart()
        selection_end = source_cursor.selectionEnd()
        anchor_column = selection_start - document.findBlock(selection_start).position()
        cursor_column = selection_end - document.findBlock(selection_end).position()

        start_block = document.findBlockByNumber(plan.start_line)
        end_block = document.findBlockByNumber(plan.end_line)
        if not start_block.isValid() or not end_block.isValid():
            return False

        edit = QTextCursor(document)
        edit.beginEditBlock()
        try:
            if plan.lines:
                edit.setPosition(start_block.position())
                edit.setPosition(
                    end_block.position() + end_block.length() - 1,
                    QTextCursor.MoveMode.KeepAnchor,
                )
                edit.insertText("\n".join(plan.lines))
            else:
                next_block = end_block.next()
                previous_block = start_block.previous()
                if next_block.isValid():
                    edit.setPosition(start_block.position())
                    edit.setPosition(next_block.position(), QTextCursor.MoveMode.KeepAnchor)
                elif previous_block.isValid():
                    edit.setPosition(
                        previous_block.position() + previous_block.length() - 1
                    )
                    edit.setPosition(
                        end_block.position() + end_block.length() - 1,
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                else:
                    edit.setPosition(start_block.position())
                    edit.setPosition(
                        end_block.position() + end_block.length() - 1,
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                edit.removeSelectedText()
        finally:
            edit.endEditBlock()

        self._restore_cursor_after_plan(
            plan,
            anchor_column=anchor_column,
            cursor_column=cursor_column,
            keep_selection=had_selection,
        )
        return True

    def _restore_cursor_after_plan(
        self,
        plan,
        *,
        anchor_column: int,
        cursor_column: int,
        keep_selection: bool,
    ) -> None:
        document = self.document()
        total = max(1, document.blockCount())
        anchor_line = max(0, min(int(plan.anchor_line), total - 1))
        cursor_line = max(0, min(int(plan.cursor_line), total - 1))
        anchor_block = document.findBlockByNumber(anchor_line)
        cursor_block = document.findBlockByNumber(cursor_line)
        if not anchor_block.isValid() or not cursor_block.isValid():
            return

        anchor_pos = anchor_block.position() + max(
            0, min(anchor_column + plan.anchor_column_delta, anchor_block.length() - 1)
        )
        cursor_pos = cursor_block.position() + max(
            0, min(cursor_column + plan.cursor_column_delta, cursor_block.length() - 1)
        )

        cursor = self.textCursor()
        if keep_selection or anchor_line != cursor_line:
            cursor.setPosition(anchor_pos)
            cursor.setPosition(cursor_pos, QTextCursor.MoveMode.KeepAnchor)
        else:
            cursor.setPosition(cursor_pos)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def duplicate_selected_lines(self) -> bool:
        first, last = self._selected_line_range()
        return self._apply_line_plan(duplicate_lines(self._document_lines(), first, last))

    def delete_selected_lines(self) -> bool:
        first, last = self._selected_line_range()
        return self._apply_line_plan(delete_lines(self._document_lines(), first, last))

    def move_selected_lines(self, *, delta: int) -> bool:
        first, last = self._selected_line_range()
        return self._apply_line_plan(
            move_lines(self._document_lines(), first, last, delta=delta)
        )

    def toggle_selected_comment(self) -> bool:
        first, last = self._selected_line_range()
        return self._apply_line_plan(toggle_comment(self._document_lines(), first, last))

    def indent_selected_lines(self) -> bool:
        first, last = self._selected_line_range()
        return self._apply_line_plan(indent_lines(self._document_lines(), first, last))

    def unindent_selected_lines(self) -> bool:
        first, last = self._selected_line_range()
        return self._apply_line_plan(unindent_lines(self._document_lines(), first, last))

    def set_word_wrap(self, enabled: bool) -> None:
        self.setLineWrapMode(
            PlainTextEdit.LineWrapMode.WidgetWidth if enabled else PlainTextEdit.LineWrapMode.NoWrap
        )

    def toggle_word_wrap(self) -> bool:
        enabled = self.lineWrapMode() == PlainTextEdit.LineWrapMode.NoWrap
        self.set_word_wrap(enabled)
        return enabled

    # ------------------------------------------------------------------ ввод

    def wheelEvent(self, event):  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.zoom_by(1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def focusNextPrevChild(self, next_child):  # noqa: N802
        # QWidget::event() отдаёт Tab навигации по фокусу ДО keyPressEvent,
        # поэтому отступ блока разрешаем, только отказавшись от смены фокуса.
        if not self.isReadOnly():
            first, last = self._selected_line_range()
            if last > first:
                return False
        return super().focusNextPrevChild(next_child)

    def keyPressEvent(self, event):  # noqa: N802
        if self._handle_shortcut(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def _handle_shortcut(self, event) -> bool:
        key = event.key()
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)

        if key == Qt.Key.Key_Escape and not (ctrl or alt):
            self.escapePressed.emit()
            return True

        if key == Qt.Key.Key_F3 and not ctrl and not alt:
            self.findNextRequested.emit(shift)
            return True

        if ctrl and not alt:
            if key == Qt.Key.Key_F:
                self.findRequested.emit(False)
                return True
            if key == Qt.Key.Key_H:
                self.findRequested.emit(True)
                return True
            if key == Qt.Key.Key_G:
                self.gotoLineRequested.emit()
                return True
            if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self.zoom_by(1)
                return True
            if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                self.zoom_by(-1)
                return True
            if key == Qt.Key.Key_0:
                self.reset_zoom()
                return True
            if self.isReadOnly():
                return False
            if key == Qt.Key.Key_D and not shift:
                return self.duplicate_selected_lines()
            if key == Qt.Key.Key_L and not shift:
                return self.delete_selected_lines()
            if key == Qt.Key.Key_K and shift:
                return self.delete_selected_lines()
            if key == Qt.Key.Key_Slash:
                return self.toggle_selected_comment()
            if key == Qt.Key.Key_W and shift:
                self.toggle_word_wrap()
                return True
            return False

        if alt and not ctrl and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            if self.isReadOnly():
                return False
            return self.move_selected_lines(delta=-1 if key == Qt.Key.Key_Up else 1)

        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) and not ctrl and not alt:
            if self.isReadOnly():
                return False
            # Tab отступает блок только при выделении нескольких строк —
            # иначе Tab обязан уходить в навигацию фокуса (accessibility).
            first, last = self._selected_line_range()
            if last <= first:
                return False
            if key == Qt.Key.Key_Backtab or shift:
                return self.unindent_selected_lines()
            return self.indent_selected_lines()

        return False
