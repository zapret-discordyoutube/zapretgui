from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication


class CodeEditorWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.processEvents()
        self.app.closeAllWindows()
        self.app.processEvents()

    def _editor(self, text: str = ""):
        from ui.code_editor.editor import CodeEditor

        editor = CodeEditor()
        self.addCleanup(editor.deleteLater)
        if text:
            editor.setPlainText(text)
        return editor

    def _place_cursor(self, editor, line: int, column: int = 0) -> None:
        block = editor.document().findBlockByNumber(line)
        cursor = editor.textCursor()
        cursor.setPosition(block.position() + column)
        editor.setTextCursor(cursor)

    def test_duplicate_line_shortcut_is_one_undo_step(self) -> None:
        editor = self._editor("a\nb\nc\n")
        self._place_cursor(editor, 1)

        QTest.keyClick(editor, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.toPlainText(), "a\nb\nb\nc\n")
        self.assertEqual(editor.textCursor().blockNumber(), 2)

        editor.undo()
        self.assertEqual(editor.toPlainText(), "a\nb\nc\n")

    def test_delete_line_shortcuts_remove_current_line(self) -> None:
        editor = self._editor("a\nb\nc\n")
        self._place_cursor(editor, 1)

        QTest.keyClick(editor, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.toPlainText(), "a\nc\n")
        # Курсор остаётся на той же строке: следующая строка поднимается вверх.
        self.assertEqual(editor.textCursor().blockNumber(), 1)

        QTest.keyClick(
            editor,
            Qt.Key.Key_K,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        self.assertEqual(editor.toPlainText(), "a\n")

    def test_alt_arrows_move_line_and_keep_cursor_on_it(self) -> None:
        editor = self._editor("a\nb\nc\n")
        self._place_cursor(editor, 0)

        QTest.keyClick(editor, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
        self.assertEqual(editor.toPlainText(), "b\na\nc\n")
        self.assertEqual(editor.textCursor().blockNumber(), 1)

        QTest.keyClick(editor, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
        self.assertEqual(editor.toPlainText(), "a\nb\nc\n")
        self.assertEqual(editor.textCursor().blockNumber(), 0)

    def test_toggle_comment_shortcut_round_trips(self) -> None:
        editor = self._editor("--new\n--filter-tcp=443\n")
        self._place_cursor(editor, 0)

        QTest.keyClick(editor, Qt.Key.Key_Slash, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.toPlainText(), "# --new\n--filter-tcp=443\n")

        QTest.keyClick(editor, Qt.Key.Key_Slash, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.toPlainText(), "--new\n--filter-tcp=443\n")

    def test_tab_indents_only_multiline_selection(self) -> None:
        editor = self._editor("a\nb\nc\n")
        self._place_cursor(editor, 0)

        # Одна строка: Tab обязан уйти в навигацию фокуса, а не отступать текст.
        QTest.keyClick(editor, Qt.Key.Key_Tab)
        self.assertEqual(editor.toPlainText(), "a\nb\nc\n")

        cursor = editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(3, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)

        QTest.keyClick(editor, Qt.Key.Key_Tab)
        self.assertEqual(editor.toPlainText(), "    a\n    b\nc\n")

        QTest.keyClick(editor, Qt.Key.Key_Backtab)
        self.assertEqual(editor.toPlainText(), "a\nb\nc\n")

    def test_zoom_shortcuts_change_font_size_and_reset(self) -> None:
        editor = self._editor("a\n")
        base = editor.current_point_size()

        QTest.keyClick(editor, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.current_point_size(), base + 1)

        QTest.keyClick(editor, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(editor, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.current_point_size(), base - 1)

        QTest.keyClick(editor, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.current_point_size(), base)

    def test_zoom_is_bounded(self) -> None:
        from ui.code_editor.editor import MAX_POINT_SIZE, MIN_POINT_SIZE

        editor = self._editor("a\n")
        editor.set_point_size(MAX_POINT_SIZE + 40)
        self.assertEqual(editor.current_point_size(), MAX_POINT_SIZE)
        editor.set_point_size(MIN_POINT_SIZE - 40)
        self.assertEqual(editor.current_point_size(), MIN_POINT_SIZE)

    def test_line_number_width_grows_with_line_count(self) -> None:
        editor = self._editor("a\n")
        narrow = editor.line_number_area_width()

        editor.setPlainText("\n".join(str(index) for index in range(1200)))
        self.app.processEvents()

        self.assertGreater(editor.line_number_area_width(), narrow)

    def test_goto_line_moves_cursor_and_clamps(self) -> None:
        editor = self._editor("a\nb\nc\nd\n")

        self.assertTrue(editor.goto_line(3))
        self.assertEqual(editor.textCursor().blockNumber(), 2)

        self.assertTrue(editor.goto_line(9999))
        self.assertEqual(editor.textCursor().blockNumber(), editor.blockCount() - 1)

    def test_cursor_status_reports_position_and_selection(self) -> None:
        from ui.code_editor.editor import build_cursor_status_text

        editor = self._editor("alpha\nbeta\ngamma\n")
        cursor = editor.textCursor()
        cursor.setPosition(2)
        cursor.setPosition(8, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)

        status = editor.cursor_status()
        self.assertEqual(status.line, 2)
        self.assertEqual(status.column, 3)
        self.assertEqual(status.selected_characters, 6)
        self.assertEqual(status.selected_lines, 2)
        self.assertEqual(status.total_lines, 4)

        text = build_cursor_status_text(status)
        self.assertIn("Стр 2, кол 3", text)
        self.assertIn("выделено 6", text)
        self.assertIn("всего строк: 4", text)

    def test_cursor_status_signal_is_emitted_on_move(self) -> None:
        editor = self._editor("a\nb\n")
        received: list = []
        editor.cursorStatusChanged.connect(received.append)

        editor.goto_line(2)
        self.app.processEvents()

        self.assertTrue(received)
        self.assertEqual(received[-1].line, 2)

    def test_search_highlights_do_not_modify_document(self) -> None:
        from ui.code_editor.find_engine import find_matches

        editor = self._editor("alpha beta alpha\n")
        editor.document().setModified(False)
        result = find_matches(editor.toPlainText(), "alpha")

        editor.set_search_highlights(result.matches, 0)

        self.assertEqual(editor.toPlainText(), "alpha beta alpha\n")
        self.assertFalse(editor.document().isModified())
        # текущая строка + два совпадения
        self.assertEqual(len(editor.extraSelections()), 3)

        editor.clear_search_highlights()
        self.assertEqual(len(editor.extraSelections()), 1)

    def test_stale_highlight_positions_are_clamped_to_document(self) -> None:
        from ui.code_editor.find_engine import SearchMatch

        editor = self._editor("short\n")

        editor.set_search_highlights((SearchMatch(100, 120),), 0)

        self.assertEqual(len(editor.extraSelections()), 1)

    def test_escape_and_find_shortcuts_emit_signals(self) -> None:
        editor = self._editor("a\n")
        escapes: list = []
        finds: list = []
        next_requests: list = []
        goto_requests: list = []
        editor.escapePressed.connect(lambda: escapes.append(True))
        editor.findRequested.connect(finds.append)
        editor.findNextRequested.connect(next_requests.append)
        editor.gotoLineRequested.connect(lambda: goto_requests.append(True))

        QTest.keyClick(editor, Qt.Key.Key_Escape)
        QTest.keyClick(editor, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(editor, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(editor, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(editor, Qt.Key.Key_F3)
        QTest.keyClick(editor, Qt.Key.Key_F3, Qt.KeyboardModifier.ShiftModifier)

        self.assertEqual(escapes, [True])
        self.assertEqual(finds, [False, True])
        self.assertEqual(next_requests, [False, True])
        self.assertEqual(goto_requests, [True])

    def test_read_only_editor_ignores_editing_shortcuts(self) -> None:
        editor = self._editor("a\nb\n")
        editor.setReadOnly(True)
        self._place_cursor(editor, 0)

        QTest.keyClick(editor, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(editor, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)

        self.assertEqual(editor.toPlainText(), "a\nb\n")

    def test_word_wrap_toggle_switches_mode(self) -> None:
        from qfluentwidgets import PlainTextEdit

        editor = self._editor("a\n")
        self.assertEqual(editor.lineWrapMode(), PlainTextEdit.LineWrapMode.NoWrap)

        self.assertTrue(editor.toggle_word_wrap())
        self.assertEqual(editor.lineWrapMode(), PlainTextEdit.LineWrapMode.WidgetWidth)

        self.assertFalse(editor.toggle_word_wrap())
        self.assertEqual(editor.lineWrapMode(), PlainTextEdit.LineWrapMode.NoWrap)


class PresetSyntaxHighlighterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_highlighter_does_not_change_text_or_modified_flag(self) -> None:
        from ui.code_editor.editor import CodeEditor
        from ui.code_editor.syntax import PresetSyntaxHighlighter

        editor = CodeEditor(highlighter_factory=lambda document: PresetSyntaxHighlighter(document))
        self.addCleanup(editor.deleteLater)
        source = "# comment\n--new\n--filter-tcp=443\n--hostlist=lists/a.txt\n"
        editor.setPlainText(source)
        editor.document().setModified(False)
        self.app.processEvents()

        self.assertEqual(editor.toPlainText(), source)
        self.assertFalse(editor.document().isModified())

    def test_palette_follows_theme_accent_and_text_color(self) -> None:
        from PyQt6.QtGui import QColor, QTextDocument

        from ui.code_editor.syntax import PresetSyntaxHighlighter, SyntaxTheme

        document = QTextDocument()
        dark = SyntaxTheme(accent=QColor("#0078d4"), text=QColor(255, 255, 255))
        highlighter = PresetSyntaxHighlighter(document, theme=dark)
        self.assertEqual(highlighter.theme, dark)

        light = SyntaxTheme(accent=QColor("#ff8c00"), text=QColor(0, 0, 0))
        self.assertTrue(highlighter.apply_theme(light))
        self.assertEqual(highlighter.theme, light)
        self.assertFalse(highlighter.apply_theme(light))

    def test_syntax_colors_are_taken_from_theme_only(self) -> None:
        from PyQt6.QtGui import QColor, QTextDocument

        from ui.code_editor.syntax import PresetSyntaxHighlighter, SyntaxTheme

        accent = QColor("#c50f1f")
        text = QColor(255, 255, 255)
        highlighter = PresetSyntaxHighlighter(
            QTextDocument(), theme=SyntaxTheme(accent=accent, text=text)
        )

        for role, text_format in highlighter._formats.items():
            color = text_format.foreground().color()
            source = (accent, text)[role in {"comment", "operator", "path"}]
            self.assertEqual(
                (color.red(), color.green(), color.blue()),
                (source.red(), source.green(), source.blue()),
                role,
            )

    def test_editor_theme_colors_use_qfluent_accent(self) -> None:
        from qfluentwidgets import themeColor

        from ui.code_editor.editor import CodeEditor

        editor = CodeEditor()
        self.addCleanup(editor.deleteLater)
        theme = editor.theme_colors()

        self.assertEqual(theme.accent.name(), themeColor().name())
        self.assertTrue(theme.text.isValid())


if __name__ == "__main__":
    unittest.main()
