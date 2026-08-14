"""Связка редактора и панели поиска: состояние совпадений, замена, переходы."""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QTextCursor

from ui.code_editor.find_engine import (
    SearchResult,
    build_counter_text,
    compile_query,
    expand_replacement,
    find_matches,
    locate_match,
    match_index_at,
    replace_all,
)

REFRESH_DEBOUNCE_MS = 120


class FindController(QObject):
    """Единственный владелец состояния поиска для пары редактор + панель."""

    statusMessage = pyqtSignal(str)

    def __init__(self, editor, bar, *, parent=None, debounce_ms: int = REFRESH_DEBOUNCE_MS) -> None:
        super().__init__(parent)
        self._editor = editor
        self._bar = bar
        self._result = SearchResult()
        self._index: int | None = None
        self._applying_replacement = False
        self._goto_prompt = None

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(max(0, int(debounce_ms)))
        self._refresh_timer.timeout.connect(self.refresh_matches)

        bar.searchChanged.connect(self.search_text)
        bar.findRequested.connect(self._on_find_requested)
        bar.optionsChanged.connect(self._on_options_changed)
        bar.replaceRequested.connect(self.replace_current)
        bar.replaceAllRequested.connect(self.replace_all)
        bar.closeRequested.connect(self.close_panel)
        bar.focusEditorRequested.connect(self.focus_editor)

        editor.findRequested.connect(self.open_panel)
        editor.findNextRequested.connect(self._on_find_requested)
        editor.escapePressed.connect(self._on_editor_escape)
        editor.gotoLineRequested.connect(self.prompt_goto_line)
        editor.contentEdited.connect(self._schedule_refresh)

    # --------------------------------------------------------------- свойства

    @property
    def result(self) -> SearchResult:
        return self._result

    @property
    def current_index(self) -> int | None:
        return self._index

    def set_goto_prompt(self, prompt) -> None:
        """Подменяет диалог перехода к строке (используется в тестах)."""
        self._goto_prompt = prompt

    # ------------------------------------------------------------------ поиск

    def _schedule_refresh(self) -> None:
        if self._applying_replacement:
            return
        if not self._bar.query():
            return
        self._refresh_timer.start()

    def refresh_matches(self, *, keep_index: bool = True) -> SearchResult:
        """Пересчитывает совпадения по текущему тексту и опциям."""
        query = self._bar.query()
        options = self._bar.options()
        if not query:
            self._result = SearchResult()
            self._index = None
            self._editor.clear_search_highlights()
            self._bar.set_error(False)
            self._bar.set_counter_text("")
            return self._result

        result = find_matches(self._editor.toPlainText(), query, options)
        self._result = result
        self._bar.set_error(bool(result.error))
        if result.error:
            self._index = None
            self._editor.clear_search_highlights()
            self._bar.set_counter_text(build_counter_text(result, None, query=query))
            return result

        if keep_index:
            self._index = match_index_at(result.matches, self._editor.textCursor().selectionStart())
        else:
            self._index = None
        self._apply_highlights()
        return result

    def _apply_highlights(self) -> None:
        self._editor.set_search_highlights(self._result.matches, self._index)
        self._bar.set_counter_text(
            build_counter_text(self._result, self._index, query=self._bar.query())
        )

    def search_text(self, text: str) -> bool:
        """Реакция на ввод в поле поиска: первое совпадение от начала документа."""
        self._refresh_timer.stop()
        query = str(text or "")
        if not query:
            self.refresh_matches()
            return False
        result = self.refresh_matches(keep_index=False)
        if result.error:
            return False
        if not result.count:
            self._apply_highlights()
            return False
        self._select_index(0)
        return True

    def _on_find_requested(self, reverse: bool) -> None:
        self.find_next(reverse=bool(reverse))

    def _on_options_changed(self) -> None:
        self._refresh_timer.stop()
        result = self.refresh_matches(keep_index=False)
        if result.ok and result.count:
            self._select_from_cursor(reverse=False, include_current=True)

    def find_next(self, *, reverse: bool = False) -> bool:
        if not self._bar.query():
            return False
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()
            self.refresh_matches()
        if self._result.error or not self._result.count:
            self._apply_highlights()
            return False
        return self._select_from_cursor(reverse=reverse, include_current=False)

    def _select_from_cursor(self, *, reverse: bool, include_current: bool) -> bool:
        cursor = self._editor.textCursor()
        position = cursor.selectionStart() if reverse else cursor.selectionEnd()
        if include_current:
            position = cursor.selectionStart()
        index = locate_match(
            self._result.matches,
            position,
            reverse=reverse,
            include_position=include_current,
        )
        if index is None:
            return False
        self._select_index(index)
        return True

    def _select_index(self, index: int) -> None:
        matches = self._result.matches
        if not matches:
            return
        bounded = max(0, min(int(index), len(matches) - 1))
        self._index = bounded
        match = matches[bounded]
        self._editor.select_range(match.start, match.end)
        self._apply_highlights()

    # ----------------------------------------------------------------- замена

    def replace_current(self) -> bool:
        """Заменяет текущее совпадение и переходит к следующему."""
        query = self._bar.query()
        if not query or self._editor.isReadOnly():
            return False
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()
            self.refresh_matches()
        if self._result.error or not self._result.count:
            return False

        cursor = self._editor.textCursor()
        index = match_index_at(self._result.matches, cursor.selectionStart())
        if index is None or not cursor.hasSelection():
            return self.find_next()

        match = self._result.matches[index]
        if (cursor.selectionStart(), cursor.selectionEnd()) != (match.start, match.end):
            return self.find_next()

        options = self._bar.options()
        pattern, error = compile_query(query, options)
        if error or pattern is None:
            return False
        try:
            replacement = expand_replacement(
                pattern, self._editor.toPlainText(), match, self._bar.replacement(), options
            )
        except Exception:
            return False

        edit = self._editor.textCursor()
        edit.beginEditBlock()
        try:
            edit.setPosition(match.start)
            edit.setPosition(match.end, QTextCursor.MoveMode.KeepAnchor)
            edit.insertText(replacement)
        finally:
            edit.endEditBlock()
        self._editor.setTextCursor(edit)

        self._refresh_timer.stop()
        self.refresh_matches(keep_index=False)
        if self._result.ok and self._result.count:
            self._select_from_cursor(reverse=False, include_current=True)
        else:
            self._apply_highlights()
        return True

    def replace_all(self) -> int:
        """Заменяет все совпадения одной undo-транзакцией."""
        query = self._bar.query()
        if not query or self._editor.isReadOnly():
            return 0

        options = self._bar.options()
        source = self._editor.toPlainText()
        result = replace_all(source, query, self._bar.replacement(), options)
        if result.error:
            self._bar.set_error(True)
            self._bar.set_counter_text("Ошибка")
            self.statusMessage.emit(f"Замена не выполнена: {result.error}")
            return 0
        if not result.count:
            self.statusMessage.emit("Совпадений не найдено.")
            return 0

        cursor = self._editor.textCursor()
        position = cursor.position()
        self._applying_replacement = True
        try:
            edit = self._editor.textCursor()
            edit.beginEditBlock()
            try:
                edit.select(QTextCursor.SelectionType.Document)
                edit.insertText(result.text)
            finally:
                edit.endEditBlock()
            restored = self._editor.textCursor()
            restored.setPosition(max(0, min(position, len(result.text))))
            self._editor.setTextCursor(restored)
        finally:
            self._applying_replacement = False

        self._refresh_timer.stop()
        self.refresh_matches(keep_index=False)
        self._apply_highlights()
        self.statusMessage.emit(f"Заменено совпадений: {result.count}")
        return int(result.count)

    # ---------------------------------------------------------------- панель

    def open_panel(self, replace_mode: bool = False) -> None:
        """Показывает панель поиска (Ctrl+F) или поиска с заменой (Ctrl+H)."""
        self._bar.setVisible(True)
        self._bar.set_replace_visible(bool(replace_mode))

        cursor = self._editor.textCursor()
        selected = cursor.selectedText()
        if selected and " " not in selected:
            if selected != self._bar.query():
                self._bar.search_input.setText(selected)
            else:
                self.refresh_matches()
        elif self._bar.query():
            self.refresh_matches()

        self._bar.focus_search()

    def close_panel(self) -> None:
        self._refresh_timer.stop()
        self._bar.setVisible(False)
        self._bar.set_replace_visible(False)
        self._bar.set_error(False)
        self._bar.set_counter_text("")
        self._result = SearchResult()
        self._index = None
        self._editor.clear_search_highlights()
        self.focus_editor()

    def _on_editor_escape(self) -> None:
        # isHidden(), а не isVisible(): страница может быть ещё не показана,
        # но панель при этом остаётся «открытой» с точки зрения пользователя.
        if not self._bar.isHidden():
            self.close_panel()

    def focus_editor(self) -> None:
        self._editor.setFocus(Qt.FocusReason.OtherFocusReason)

    # ------------------------------------------------------- переход к строке

    def prompt_goto_line(self) -> bool:
        prompt = self._goto_prompt
        if prompt is None:
            from ui.code_editor.goto_line_dialog import prompt_goto_line as default_prompt

            prompt = default_prompt
        status = self._editor.cursor_status()
        try:
            line = prompt(
                self._editor.window(),
                total_lines=status.total_lines,
                current_line=status.line,
            )
        except Exception:
            return False
        if line is None:
            return False
        return bool(self._editor.goto_line(int(line)))

    def cleanup(self) -> None:
        try:
            self._refresh_timer.stop()
        except Exception:
            pass
