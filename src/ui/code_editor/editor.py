"""Текстовый редактор с номерами строк, подсветкой и построчными операциями.

Переиспользуемый виджет: ничего не знает о пресетах, профилях и файлах —
только текст. Конкретную подсветку синтаксиса задаёт вызывающая сторона
через `highlighter_factory`, а проверку текста, подсказки при наборе и
быстрые исправления — через `set_language_support` (см. `language.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6 import QtWidgets
from PyQt6.QtCore import QEvent, QPointF, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QPainter, QPalette, QTextCharFormat, QTextCursor, QTextFormat
from qfluentwidgets import PlainTextEdit, RoundMenu, isDarkTheme, themeColor
from qfluentwidgets.components.widgets.menu import TextEditMenu

from ui.code_editor.completion_popup import MAX_VISIBLE_ROWS, MIN_VISIBLE_ROWS, POPUP_MAX_WIDTH, CompletionPopup
from ui.code_editor.language import (
    SEVERITY_ERROR,
    SEVERITY_HINT,
    SEVERITY_ORDER,
    SEVERITY_WARNING,
    worst_severity,
)
from ui.code_editor.line_number_area import LineNumberArea
from ui.code_editor.match_ruler import RULER_WIDTH, MatchRuler
from ui.code_editor.syntax import SyntaxTheme
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
from ui.widgets.fluent_item_tooltip import FluentItemToolTipController

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
DIAGNOSTICS_DELAY_MS = 300
PROBLEM_GUTTER_WIDTH = 10


@dataclass(frozen=True, slots=True)
class CursorStatus:
    line: int
    column: int
    selected_characters: int
    selected_lines: int
    total_lines: int


@dataclass(frozen=True, slots=True)
class ProblemsSummary:
    """Сколько проблем нашла проверка и что не так в строке курсора."""

    errors: int
    warnings: int
    hints: int
    current_message: str = ""
    current_severity: str = ""

    @property
    def total(self) -> int:
        return self.errors + self.warnings


class _CodeEditorContextMenu(TextEditMenu):
    """Обычное меню правки, перед которым стоят исправления для этого места."""

    def __init__(self, editor, fixes) -> None:
        super().__init__(editor)
        self._fixes = tuple(fixes)
        self._fix_actions: list[QAction] = []
        self._fixes_added = False

    def createActions(self):  # noqa: N802
        super().createActions()
        editor = self.parent()
        self._fixes_added = False
        self._fix_actions = [
            QAction(str(fix.title), self, triggered=lambda _checked=False, f=fix: editor.apply_quick_fix(f))
            for fix in self._fixes
        ]

    def _ensure_fixes(self) -> None:
        if self._fixes_added or not self._fix_actions:
            return
        self._fixes_added = True
        RoundMenu.addActions(self, self._fix_actions)
        self.addSeparator()

    def addAction(self, action):  # noqa: N802
        self._ensure_fixes()
        super().addAction(action)


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
    problemsChanged = pyqtSignal(object)

    def __init__(self, parent=None, *, highlighter_factory=None) -> None:
        super().__init__(parent)
        self.setProperty("noDrag", True)
        self._line_number_area = LineNumberArea(self)
        self._match_ruler = MatchRuler(self)
        self._match_ruler.hide()
        self._match_ruler.lineRequested.connect(self._on_ruler_line_requested)
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
        self._language = None
        self._diagnostics: tuple = ()
        # Курсоры с выделением сами сдвигаются при правках, поэтому подчёркивание
        # остаётся на месте, пока проверка не пересчитана.
        self._diagnostic_cursors: list[tuple[QTextCursor, str, object]] = []
        self._problem_colors = {
            SEVERITY_ERROR: QColor("#e0443e"),
            SEVERITY_WARNING: QColor("#d9a400"),
            SEVERITY_HINT: QColor(128, 128, 128, 160),
        }
        self._diagnostics_timer = QTimer(self)
        self._diagnostics_timer.setSingleShot(True)
        self._diagnostics_timer.timeout.connect(self._on_diagnostics_timer)
        self._diagnostics_deferred = False
        self._completion_popup: CompletionPopup | None = None
        self._completion = None
        self._tooltip: FluentItemToolTipController | None = None
        self._last_problems_summary: ProblemsSummary | None = None

        self.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        # Одиночный Tab отдаётся навигации по фокусу (accessibility); отступ
        # блока перехватывается раньше — только при выделении нескольких строк.
        self.setTabChangesFocus(True)
        self._apply_monospace_font(DEFAULT_POINT_SIZE)
        apply_editor_smooth_scroll_preference(self)

        self.blockCountChanged.connect(self._on_block_count_changed)
        self.textChanged.connect(self._on_text_changed)
        self.updateRequest.connect(self._on_update_request)
        self.verticalScrollBar().valueChanged.connect(self._sync_ruler_visible_range)
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
        gutter = PROBLEM_GUTTER_WIDTH if getattr(self, "_language", None) is not None else 0
        return 14 + advance * digits + gutter

    def _update_line_number_area_width(self) -> None:
        width = self.line_number_area_width()
        self.setViewportMargins(width, 0, self.match_ruler_width(), 0)
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
        self._sync_ruler_visible_range()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        contents = self.contentsRect()
        self._line_number_area.setGeometry(
            QRect(contents.left(), contents.top(), self.line_number_area_width(), contents.height())
        )
        self._update_match_ruler_geometry()

    # ---------------------------------------------------- полоса совпадений

    def match_ruler_width(self) -> int:
        """Ширина полосы маркеров: 0, пока искать нечего."""
        return RULER_WIDTH if self._match_ruler.has_markers() else 0

    def _update_match_ruler_geometry(self) -> None:
        contents = self.contentsRect()
        width = self.match_ruler_width()
        if not width:
            self._match_ruler.hide()
            return
        self._match_ruler.setGeometry(
            QRect(contents.right() - width + 1, contents.top(), width, contents.height())
        )
        self._match_ruler.show()
        self._match_ruler.raise_()

    def _visible_line_range(self) -> tuple[int, int]:
        first_block = self.firstVisibleBlock()
        if not first_block.isValid():
            return (0, 0)
        first = first_block.blockNumber()
        offset = self.contentOffset()
        bottom = self.viewport().rect().bottom()
        block = first_block
        last = first
        top = self.blockBoundingGeometry(block).translated(offset).top()
        while block.isValid() and top <= bottom:
            last = block.blockNumber()
            top += self.blockBoundingRect(block).height()
            block = block.next()
        return (first, last)

    def _sync_ruler_visible_range(self) -> None:
        if not self._match_ruler.has_markers():
            return
        first, last = self._visible_line_range()
        self._match_ruler.set_visible_range(first, last)

    def _on_ruler_line_requested(self, line: int) -> None:
        self.goto_line(int(line) + 1)

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
        problem_lines = self._problem_line_severities() if self._language is not None else {}
        if problem_lines:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

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
                severity = problem_lines.get(block_number)
                if severity:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(self._problem_colors.get(severity, self._problem_colors[SEVERITY_HINT]))
                    radius = 3.5 if severity != SEVERITY_HINT else 2.5
                    center_y = top + height / 2
                    painter.drawEllipse(QPointF(6.0, center_y), radius, radius)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1
        painter.end()

    # ------------------------------------------------------------------ тема

    def theme_colors(self) -> SyntaxTheme:
        """Акцент и цвет текста текущей темы qfluentwidgets."""
        try:
            accent = QColor(themeColor())
        except Exception:
            accent = QColor("#0078d4")
        text = QColor(self.palette().color(QPalette.ColorRole.Text))
        if not text.isValid() or text.alpha() == 0:
            text = QColor(255, 255, 255) if isDarkTheme() else QColor(0, 0, 0)
        return SyntaxTheme(accent=accent, text=text)

    def _apply_theme(self, *, tokens=None) -> None:
        theme = self.theme_colors()
        accent = theme.accent
        text = theme.text

        # Служебные подсветки — тем же акцентом и цветом текста темы,
        # различаются только прозрачностью.
        self._line_number_color = QColor(text)
        self._line_number_color.setAlpha(95)
        self._line_number_current_color = QColor(accent)
        self._current_line_color = QColor(text)
        self._current_line_color.setAlpha(14)
        self._match_color = QColor(accent)
        self._match_color.setAlpha(70)
        self._current_match_color = QColor(accent)
        self._current_match_color.setAlpha(160)

        ruler_marker = QColor(accent)
        ruler_marker.setAlpha(150)
        ruler_viewport = QColor(text)
        ruler_viewport.setAlpha(30)
        self._match_ruler.set_colors(
            marker=ruler_marker,
            current=QColor(accent),
            viewport=ruler_viewport,
        )

        dark = isDarkTheme()
        self._problem_colors = {
            SEVERITY_ERROR: QColor("#ff6b61") if dark else QColor("#c42b1c"),
            SEVERITY_WARNING: QColor("#f2c14e") if dark else QColor("#9d5d00"),
            SEVERITY_HINT: QColor(text.red(), text.green(), text.blue(), 150),
        }
        if self._completion_popup is not None:
            self._apply_completion_popup_colors()

        self._apply_highlighter_theme(theme)

        self._refresh_extra_selections()
        self._refresh_problem_ruler()
        self._line_number_area.update()

    def _apply_highlighter_theme(self, theme=None) -> None:
        """Перекрашивает синтаксис, не выдавая это за правку документа.

        QSyntaxHighlighter.rehighlight() открывает edit-block на документе и
        по его закрытию Qt эмитит textChanged, хотя текст не менялся. Без
        подавления смена темы помечала пресет изменённым и запускала
        автосохранение — поэтому наружу отдаётся contentEdited, а не
        сырой textChanged.
        """
        setter = getattr(self._highlighter, "apply_theme", None)
        if not callable(setter):
            return
        self._suppress_content_signals = True
        try:
            setter(theme if theme is not None else self.theme_colors())
        except Exception:
            pass
        finally:
            self._suppress_content_signals = False

    def _on_text_changed(self) -> None:
        if self._suppress_content_signals:
            return
        if self._language is not None:
            self._diagnostics_timer.start(DIAGNOSTICS_DELAY_MS)
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
        self._refresh_match_ruler(current_index)

    def clear_search_highlights(self) -> None:
        self._search_selections = ()
        self._current_match_range = None
        self._refresh_extra_selections()
        self._refresh_match_ruler(None)

    def _refresh_match_ruler(self, current_index: int | None) -> None:
        """Пересчитывает метки полосы: номера строк считаются один раз здесь."""
        document = self.document()
        limit = max(0, document.characterCount() - 1)
        lines: list[int] = []
        current_line: int | None = None
        for index, match in enumerate(self._search_selections):
            position = max(0, min(int(match.start), limit))
            block = document.findBlock(position)
            if not block.isValid():
                continue
            line = block.blockNumber()
            lines.append(line)
            if current_index is not None and index == int(current_index):
                current_line = line

        self._match_ruler.set_matches(
            lines,
            total_lines=max(1, self.blockCount()),
            current_line=current_line,
        )
        self._update_line_number_area_width()
        self._update_match_ruler_geometry()
        self._sync_ruler_visible_range()

    def _refresh_extra_selections(self) -> None:
        selections = []

        current_line = QtWidgets.QTextEdit.ExtraSelection()
        current_line.format.setBackground(self._current_line_color)
        current_line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        cursor = self.textCursor()
        cursor.clearSelection()
        current_line.cursor = cursor
        selections.append(current_line)

        selections.extend(self._diagnostic_selections())

        document = self.document()
        # Позиции совпадений могли устареть: правка документа приходит раньше,
        # чем контроллер успевает пересчитать поиск.
        limit = max(0, document.characterCount() - 1)
        for match in self._search_selections:
            start = max(0, min(int(match.start), limit))
            end = max(start, min(int(match.end), limit))
            if end <= start:
                continue
            selection = QtWidgets.QTextEdit.ExtraSelection()
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
        if self._language is not None:
            self._emit_problems_summary()
            if self._completion is not None and not self._cursor_inside_completion():
                self.hide_completion()

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
        if self._handle_completion_key(event):
            event.accept()
            return
        if self._handle_shortcut(event):
            event.accept()
            return
        super().keyPressEvent(event)
        self._after_key_typed(event)

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

        if key == Qt.Key.Key_F8 and not ctrl and not alt and self._language is not None:
            self.goto_next_problem(forward=not shift)
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
            if key == Qt.Key.Key_Space and self._language is not None:
                self.request_completion(explicit=True)
                return True
            if key == Qt.Key.Key_Period and not shift and self._language is not None:
                self.show_quick_fix_menu()
                return True
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

    # ------------------------------------------------------ поддержка языка

    def set_language_support(self, support) -> None:
        """Подключает проверку текста, подсказки и исправления (или None)."""
        self._language = support
        self.hide_completion()
        self._update_line_number_area_width()
        if support is None:
            self._diagnostics_timer.stop()
            self._diagnostics = ()
            self._diagnostic_cursors = []
            self._refresh_extra_selections()
            self._refresh_problem_ruler()
            self._emit_problems_summary(force=True)
        else:
            self._diagnostics_timer.start(0)
        self._line_number_area.update()

    def language_support(self):
        return self._language

    def diagnostics(self) -> tuple:
        return self._diagnostics

    def _on_diagnostics_timer(self) -> None:
        # Пока открыт список подсказок, человек дописывает слово: не мигать
        # ошибками на недописанном тексте, проверить после закрытия списка.
        if self.is_completion_visible():
            self._diagnostics_deferred = True
            return
        self.refresh_diagnostics()

    def refresh_diagnostics(self) -> None:
        """Проверяет текст сейчас (обычно проверка идёт с задержкой после правки)."""
        self._diagnostics_timer.stop()
        self._diagnostics_deferred = False
        support = self._language
        diagnostics: tuple = ()
        if support is not None:
            try:
                diagnostics = tuple(support.diagnose(self.toPlainText()) or ())
            except Exception:
                diagnostics = ()
        self._diagnostics = diagnostics
        document = self.document()
        cursors: list[tuple[QTextCursor, str, object]] = []
        for diagnostic in diagnostics:
            block = document.findBlockByNumber(int(diagnostic.line))
            if not block.isValid():
                continue
            length = max(0, block.length() - 1)
            start = max(0, min(int(diagnostic.start), length))
            end = max(start, min(int(diagnostic.end), length))
            if end == start:
                if start > 0:
                    start -= 1
                elif length:
                    end = 1
            cursor = QTextCursor(document)
            cursor.setPosition(block.position() + start)
            cursor.setPosition(block.position() + end, QTextCursor.MoveMode.KeepAnchor)
            cursors.append((cursor, str(diagnostic.severity), diagnostic))
        self._diagnostic_cursors = cursors
        self._refresh_extra_selections()
        self._refresh_problem_ruler()
        self._line_number_area.update()
        self._emit_problems_summary(force=True)

    def _diagnostic_line(self, cursor: QTextCursor) -> int:
        return self.document().findBlock(cursor.selectionStart()).blockNumber()

    def _problem_line_severities(self) -> dict[int, str]:
        lines: dict[int, str] = {}
        for cursor, severity, _diagnostic in self._diagnostic_cursors:
            line = self._diagnostic_line(cursor)
            lines[line] = worst_severity((lines.get(line, ""), severity))
        return lines

    def _diagnostic_selections(self) -> list:
        selections = []
        for cursor, severity, _diagnostic in self._diagnostic_cursors:
            if not cursor.hasSelection():
                continue
            fmt = QTextCharFormat()
            fmt.setUnderlineStyle(
                QTextCharFormat.UnderlineStyle.DotLine
                if severity == SEVERITY_HINT
                else QTextCharFormat.UnderlineStyle.WaveUnderline
            )
            fmt.setUnderlineColor(self._problem_colors.get(severity, self._problem_colors[SEVERITY_HINT]))
            selection = QtWidgets.QTextEdit.ExtraSelection()
            selection.format = fmt
            selection.cursor = cursor
            selections.append(selection)
        return selections

    def _refresh_problem_ruler(self) -> None:
        problems = []
        for line, severity in sorted(self._problem_line_severities().items()):
            if severity == SEVERITY_HINT:
                continue
            problems.append((line, self._problem_colors.get(severity, self._problem_colors[SEVERITY_WARNING])))
        self._match_ruler.set_problems(problems, total_lines=max(1, self.blockCount()))
        self._update_line_number_area_width()
        self._update_match_ruler_geometry()
        self._sync_ruler_visible_range()

    def problems_summary(self) -> ProblemsSummary:
        counts = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 0, SEVERITY_HINT: 0}
        for _cursor, severity, _diagnostic in self._diagnostic_cursors:
            counts[severity] = counts.get(severity, 0) + 1
        line = self.textCursor().blockNumber()
        current = [
            (SEVERITY_ORDER.get(severity, 9), severity, diagnostic)
            for cursor, severity, diagnostic in self._diagnostic_cursors
            if self._diagnostic_line(cursor) == line
        ]
        current.sort(key=lambda item: item[0])
        message = str(current[0][2].message) if current else ""
        severity = current[0][1] if current else ""
        return ProblemsSummary(
            errors=counts[SEVERITY_ERROR],
            warnings=counts[SEVERITY_WARNING],
            hints=counts[SEVERITY_HINT],
            current_message=message,
            current_severity=severity,
        )

    def _emit_problems_summary(self, *, force: bool = False) -> None:
        summary = self.problems_summary()
        if not force and summary == self._last_problems_summary:
            return
        self._last_problems_summary = summary
        try:
            self.problemsChanged.emit(summary)
        except Exception:
            pass

    def goto_next_problem(self, *, forward: bool = True) -> bool:
        """Переводит курсор к следующей (или предыдущей) проблеме по кругу."""
        if self._diagnostics_timer.isActive():
            self.refresh_diagnostics()
        entries = [(cursor.selectionStart(), severity) for cursor, severity, _d in self._diagnostic_cursors]
        serious = [position for position, severity in entries if severity != SEVERITY_HINT]
        positions = sorted(set(serious or [position for position, _severity in entries]))
        if not positions:
            return False
        here = self.textCursor().position()
        if forward:
            target = next((position for position in positions if position > here), positions[0])
        else:
            target = next((position for position in reversed(positions) if position < here), positions[-1])
        cursor = self.textCursor()
        cursor.setPosition(target)
        self.setTextCursor(cursor)
        self.centerCursor()
        return True

    # ------------------------------------------------------------- подсказки

    def viewportEvent(self, event):  # noqa: N802
        # Qt зовёт viewportEvent ещё из конструктора базового класса.
        if getattr(self, "_language", None) is not None and event.type() == QEvent.Type.ToolTip:
            self._show_tooltip_at(event.pos(), event.globalPos())
            return True
        return super().viewportEvent(event)

    def _show_tooltip_at(self, pos, global_pos) -> None:
        cursor = self.cursorForPosition(pos)
        position = cursor.position()
        line = cursor.blockNumber()
        column = cursor.positionInBlock()
        titles = {SEVERITY_ERROR: "Ошибка", SEVERITY_WARNING: "Предупреждение", SEVERITY_HINT: "Подсказка"}
        parts = [
            f"{titles.get(severity, 'Проблема')}: {diagnostic.message}"
            for problem_cursor, severity, diagnostic in self._diagnostic_cursors
            if problem_cursor.selectionStart() <= position <= problem_cursor.selectionEnd()
            and self._diagnostic_line(problem_cursor) == line
        ]
        try:
            description = str(self._language.describe(self.toPlainText(), line, column) or "")
        except Exception:
            description = ""
        if description:
            parts.append(description)
        if self._tooltip is None:
            self._tooltip = FluentItemToolTipController(self.viewport(), duration=12000)
        if parts:
            self._tooltip.show_text("\n\n".join(parts), global_pos)
        else:
            self._tooltip.hide()

    def _ensure_completion_popup(self) -> CompletionPopup:
        if self._completion_popup is None:
            popup = CompletionPopup(self.viewport())
            popup.rowAccepted.connect(self.accept_completion)
            popup.wheelScrolled.connect(self._on_completion_wheel)
            self._completion_popup = popup
            self._apply_completion_popup_colors()
            self.verticalScrollBar().valueChanged.connect(self._hide_completion_on_scroll)
            self.horizontalScrollBar().valueChanged.connect(self._hide_completion_on_scroll)
        return self._completion_popup

    def _hide_completion_on_scroll(self, _value=None) -> None:
        self.hide_completion()

    def _on_completion_wheel(self, event) -> None:
        """Колесо над списком подсказок прокручивает текст, список закрывается."""
        self.hide_completion()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return
        delta = event.angleDelta().y()
        if not delta:
            return
        bar = self.verticalScrollBar()
        step = max(1, QtWidgets.QApplication.wheelScrollLines()) * max(1, bar.singleStep())
        bar.setValue(bar.value() - int(round(delta / 120 * step)))

    def _apply_completion_popup_colors(self) -> None:
        popup = self._completion_popup
        if popup is None:
            return
        dark = isDarkTheme()
        theme = self.theme_colors()
        background = QColor(40, 40, 40) if dark else QColor(252, 252, 252)
        border = QColor(255, 255, 255, 34) if dark else QColor(0, 0, 0, 34)
        muted = QColor(theme.text)
        muted.setAlpha(120)
        selection = QColor(theme.accent)
        selection.setAlpha(60 if dark else 40)
        popup.apply_colors(background=background, border=border, text=theme.text, muted=muted,
                           selection=selection)

    def completion_popup(self) -> CompletionPopup | None:
        return self._completion_popup

    def is_completion_visible(self) -> bool:
        return self._completion_popup is not None and self._completion_popup.isVisible()

    def hide_completion(self) -> None:
        self._completion = None
        popup = getattr(self, "_completion_popup", None)
        if popup is not None and popup.isVisible():
            popup.hide()
            if getattr(self, "_diagnostics_deferred", False):
                self._diagnostics_timer.start(DIAGNOSTICS_DELAY_MS)

    def _cursor_inside_completion(self) -> bool:
        result = self._completion
        if result is None:
            return False
        cursor = self.textCursor()
        return cursor.blockNumber() == int(result.line) and cursor.positionInBlock() >= int(result.start)

    def request_completion(self, *, explicit: bool = False) -> bool:
        """Спрашивает подсказки для места курсора и показывает список."""
        support = self._language
        if support is None or self.isReadOnly():
            return False
        cursor = self.textCursor()
        if cursor.hasSelection():
            self.hide_completion()
            return False
        try:
            result = support.complete(
                self.toPlainText(),
                cursor.blockNumber(),
                cursor.positionInBlock(),
                explicit=bool(explicit),
            )
        except Exception:
            result = None
        items = tuple(getattr(result, "items", ()) or ())
        if not items:
            self.hide_completion()
            return False
        self._completion = result
        popup = self._ensure_completion_popup()
        popup.set_items([item.label for item in items], [item.detail for item in items], font=self.font())
        if not self._position_completion_popup():
            self.hide_completion()
            return False
        popup.show()
        popup.raise_()
        return True

    def _position_completion_popup(self) -> bool:
        """Ставит список под строкой курсора, а если места нет — над ней.

        Строку, в которой печатают, список не закрывает никогда: он
        сжимается под свободное место (не меньше трёх вариантов, если влезает).
        """
        popup = self._completion_popup
        result = self._completion
        if popup is None or result is None:
            return False
        block = self.document().findBlockByNumber(int(result.line))
        anchor = QTextCursor(self.document())
        if block.isValid():
            anchor.setPosition(block.position() + max(0, min(int(result.start), block.length() - 1)))
        rect = self.cursorRect(anchor)
        viewport = self.viewport().rect()
        row_height = popup.row_height()
        wanted = min(MAX_VISIBLE_ROWS, max(1, popup.row_count()))
        below_rows = (viewport.height() - rect.bottom() - 12) // row_height
        above_rows = (rect.top() - 12) // row_height
        place_below = below_rows >= min(wanted, MIN_VISIBLE_ROWS) or below_rows >= above_rows
        rows = min(wanted, below_rows if place_below else above_rows)
        if rows < 1:
            return False
        size = popup.preferred_size(min(POPUP_MAX_WIDTH, viewport.width() - 8), rows)
        width = min(size.width(), max(120, viewport.width() - 4))
        x = max(0, min(rect.left() - 11, viewport.width() - width))
        y = rect.bottom() + 3 if place_below else rect.top() - size.height() - 3
        popup.setGeometry(x, y, width, size.height())
        return True

    def accept_completion(self, row: int | None = None) -> bool:
        result = self._completion
        popup = self._completion_popup
        if result is None or popup is None:
            return False
        index = popup.current_row() if row is None else int(row)
        items = tuple(result.items)
        if not 0 <= index < len(items):
            return False
        item = items[index]
        block = self.document().findBlockByNumber(int(result.line))
        if not block.isValid():
            self.hide_completion()
            return False
        limit = block.length() - 1
        start = block.position() + max(0, min(int(result.start), limit))
        end = block.position() + max(0, min(int(result.end), limit))
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        cursor.setPosition(start)
        cursor.setPosition(max(start, end), QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(str(item.insert_text))
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.hide_completion()
        if bool(getattr(item, "reopen", False)):
            QTimer.singleShot(0, lambda: self.request_completion(explicit=False))
        return True

    def _handle_completion_key(self, event) -> bool:
        if not self.is_completion_visible():
            return False
        key = event.key()
        modifiers = event.modifiers()
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            return False
        popup = self._completion_popup
        if key == Qt.Key.Key_Up:
            popup.move_selection(-1)
            return True
        if key == Qt.Key.Key_Down:
            popup.move_selection(1)
            return True
        if key == Qt.Key.Key_PageUp:
            popup.move_selection(-8)
            return True
        if key == Qt.Key.Key_PageDown:
            popup.move_selection(8)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not modifiers & Qt.KeyboardModifier.ShiftModifier:
            return self.accept_completion()
        if key == Qt.Key.Key_Escape:
            self.hide_completion()
            return True
        return False

    _AUTO_COMPLETE_CHARS = frozenset("-=:,@/~")

    def _after_key_typed(self, event) -> None:
        if self._language is None or self.isReadOnly():
            return
        key = event.key()
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            return
        text = event.text()
        if self.is_completion_visible():
            if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete, Qt.Key.Key_Left, Qt.Key.Key_Right) or (
                text and text.isprintable()
            ):
                self.request_completion(explicit=False)
            return
        if text and (text in self._AUTO_COMPLETE_CHARS or text.isalnum() or text == "_"):
            self.request_completion(explicit=False)

    def focusOutEvent(self, event):  # noqa: N802
        self.hide_completion()
        super().focusOutEvent(event)

    # ------------------------------------------------------ быстрые исправления

    def quick_fixes_at_cursor(self) -> list:
        """Исправления для проблем в строке курсора и действия по месту."""
        if self._language is None:
            return []
        if self._diagnostics_timer.isActive():
            self.refresh_diagnostics()
        cursor = self.textCursor()
        line = cursor.blockNumber()
        fixes: list = []
        titles: set[str] = set()
        ordered = sorted(
            (
                (SEVERITY_ORDER.get(severity, 9), diagnostic)
                for problem_cursor, severity, diagnostic in self._diagnostic_cursors
                if self._diagnostic_line(problem_cursor) == line
            ),
            key=lambda item: item[0],
        )
        for _order, diagnostic in ordered:
            for fix in tuple(getattr(diagnostic, "fixes", ()) or ()):
                if fix.title not in titles:
                    titles.add(fix.title)
                    fixes.append(fix)
        try:
            actions = tuple(self._language.quick_actions(self.toPlainText(), line, cursor.positionInBlock()) or ())
        except Exception:
            actions = ()
        for action in actions:
            if action.title not in titles:
                titles.add(action.title)
                fixes.append(action)
        return fixes

    def show_quick_fix_menu(self) -> bool:
        if self.isReadOnly():
            return False
        fixes = self.quick_fixes_at_cursor()
        if not fixes:
            return False
        menu = RoundMenu(parent=self)
        for fix in fixes:
            menu.addAction(
                QAction(str(fix.title), menu, triggered=lambda _checked=False, f=fix: self.apply_quick_fix(f))
            )
        position = self.viewport().mapToGlobal(self.cursorRect().bottomLeft())
        menu.exec(position)
        return True

    def contextMenuEvent(self, event):  # noqa: N802
        if self._language is None or self.isReadOnly():
            super().contextMenuEvent(event)
            return
        cursor = self.cursorForPosition(event.pos())
        if not self.textCursor().hasSelection():
            self.setTextCursor(cursor)
        menu = _CodeEditorContextMenu(self, self.quick_fixes_at_cursor())
        menu.exec(event.globalPos())

    def apply_quick_fix(self, fix) -> bool:
        return self.apply_text_edits(tuple(getattr(fix, "edits", ()) or ()))

    def _position_of(self, line: int, column: int) -> int:
        document = self.document()
        block = document.findBlockByNumber(int(line))
        if not block.isValid():
            return max(0, document.characterCount() - 1)
        return block.position() + max(0, min(int(column), block.length() - 1))

    def apply_text_edits(self, edits) -> bool:
        """Применяет правки одним шагом отмены (Ctrl+Z вернёт всё сразу)."""
        if self.isReadOnly() or not edits:
            return False
        planned = []
        for edit in edits:
            start = self._position_of(edit.start_line, edit.start_column)
            end = self._position_of(edit.end_line, edit.end_column)
            planned.append((min(start, end), max(start, end), str(edit.text)))
        planned.sort(key=lambda item: item[0], reverse=True)
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        try:
            for start, end, text in planned:
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                cursor.insertText(text)
        finally:
            cursor.endEditBlock()
        first_start, _end, first_text = planned[-1]
        caret = self.textCursor()
        caret.setPosition(min(first_start + len(first_text), max(0, self.document().characterCount() - 1)))
        self.setTextCursor(caret)
        self.refresh_diagnostics()
        return True
