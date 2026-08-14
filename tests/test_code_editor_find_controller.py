from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget


class FindControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.processEvents()
        self.app.closeAllWindows()
        self.app.processEvents()

    def _build(self, text: str = ""):
        from ui.code_editor.editor import CodeEditor
        from ui.code_editor.find_bar import FindReplaceBar
        from ui.code_editor.find_controller import FindController

        host = QWidget()
        self.addCleanup(host.deleteLater)
        layout = QVBoxLayout(host)
        bar = FindReplaceBar(host)
        editor = CodeEditor(host)
        layout.addWidget(bar)
        layout.addWidget(editor)
        controller = FindController(editor, bar, parent=host, debounce_ms=0)
        self.addCleanup(controller.cleanup)
        if text:
            editor.setPlainText(text)
        return host, bar, editor, controller

    def test_typing_query_selects_first_match_and_highlights_all(self) -> None:
        _host, bar, editor, controller = self._build("alpha\nbeta\nalpha\n")

        bar.search_input.setText("alpha")
        self.app.processEvents()

        self.assertEqual(controller.result.count, 2)
        self.assertEqual(controller.current_index, 0)
        self.assertEqual(editor.textCursor().selectionStart(), 0)
        self.assertEqual(bar.counterLabel.text(), "1 / 2")
        # текущая строка + два совпадения
        self.assertEqual(len(editor.extraSelections()), 3)

    def test_enter_and_shift_enter_walk_matches_with_wrap(self) -> None:
        _host, bar, editor, controller = self._build("alpha\nbeta\nalpha\n")

        bar.search_input.setText("alpha")
        self.app.processEvents()

        QTest.keyClick(bar.search_input, Qt.Key.Key_Return)
        self.assertEqual(controller.current_index, 1)
        self.assertEqual(bar.counterLabel.text(), "2 / 2")

        QTest.keyClick(bar.search_input, Qt.Key.Key_Return)
        self.assertEqual(controller.current_index, 0)

        QTest.keyClick(bar.search_input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(controller.current_index, 1)

    def test_missing_match_reports_empty_counter(self) -> None:
        _host, bar, editor, controller = self._build("alpha\n")

        bar.search_input.setText("zzz")
        self.app.processEvents()

        self.assertEqual(controller.result.count, 0)
        self.assertEqual(bar.counterLabel.text(), "Нет совпадений")
        self.assertEqual(len(editor.extraSelections()), 1)

    def test_invalid_regex_marks_error_without_crash(self) -> None:
        _host, bar, editor, controller = self._build("alpha\n")

        bar.regexButton.setChecked(True)
        bar.search_input.setText("(unclosed")
        self.app.processEvents()

        self.assertFalse(controller.result.ok)
        self.assertEqual(bar.counterLabel.text(), "Ошибка")
        self.assertTrue(bar.search_input.property("codeEditorSearchError"))

        bar.search_input.setText(r"al\w+")
        self.app.processEvents()
        self.assertTrue(controller.result.ok)
        self.assertFalse(bar.search_input.property("codeEditorSearchError"))

    def test_case_option_toggle_refreshes_matches(self) -> None:
        _host, bar, _editor, controller = self._build("Alpha alpha\n")

        bar.search_input.setText("alpha")
        self.app.processEvents()
        self.assertEqual(controller.result.count, 2)

        bar.caseButton.setChecked(True)
        self.app.processEvents()
        self.assertEqual(controller.result.count, 1)

    def test_document_edit_refreshes_match_count(self) -> None:
        _host, bar, editor, controller = self._build("alpha\n")

        bar.search_input.setText("alpha")
        self.app.processEvents()
        self.assertEqual(controller.result.count, 1)

        editor.setPlainText("alpha alpha alpha\n")
        self.app.processEvents()
        QTest.qWait(20)

        self.assertEqual(controller.result.count, 3)

    def test_replace_current_replaces_selection_and_advances(self) -> None:
        _host, bar, editor, controller = self._build("alpha beta alpha\n")

        bar.search_input.setText("alpha")
        bar.replace_input.setText("gamma")
        self.app.processEvents()

        self.assertTrue(controller.replace_current())
        self.assertEqual(editor.toPlainText(), "gamma beta alpha\n")
        self.assertEqual(controller.result.count, 1)

    def test_replace_all_is_single_undo_step(self) -> None:
        _host, bar, editor, controller = self._build("alpha beta alpha\n")

        bar.search_input.setText("alpha")
        bar.replace_input.setText("gamma")
        self.app.processEvents()

        messages: list[str] = []
        controller.statusMessage.connect(messages.append)

        self.assertEqual(controller.replace_all(), 2)
        self.assertEqual(editor.toPlainText(), "gamma beta gamma\n")
        self.assertIn("Заменено совпадений: 2", messages)

        editor.undo()
        self.assertEqual(editor.toPlainText(), "alpha beta alpha\n")

    def test_replace_all_with_regex_groups(self) -> None:
        _host, bar, editor, controller = self._build("--filter-tcp=80\n")

        bar.regexButton.setChecked(True)
        bar.search_input.setText(r"--filter-(\w+)=(\d+)")
        bar.replace_input.setText(r"--filter-\1=\2\2")
        self.app.processEvents()

        self.assertEqual(controller.replace_all(), 1)
        self.assertEqual(editor.toPlainText(), "--filter-tcp=8080\n")

    def test_replace_all_on_read_only_editor_does_nothing(self) -> None:
        _host, bar, editor, controller = self._build("alpha\n")

        bar.search_input.setText("alpha")
        bar.replace_input.setText("beta")
        self.app.processEvents()
        editor.setReadOnly(True)

        self.assertEqual(controller.replace_all(), 0)
        self.assertEqual(editor.toPlainText(), "alpha\n")

    def test_ctrl_f_opens_panel_with_selected_word(self) -> None:
        _host, bar, editor, controller = self._build("alpha beta\n")
        bar.setVisible(False)

        cursor = editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(5, cursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)

        QTest.keyClick(editor, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()

        self.assertFalse(bar.isHidden())
        self.assertFalse(bar.is_replace_visible())
        self.assertEqual(bar.query(), "alpha")
        self.assertEqual(controller.result.count, 1)

    def test_ctrl_h_opens_replace_row(self) -> None:
        _host, bar, editor, _controller = self._build("alpha\n")
        bar.setVisible(False)

        QTest.keyClick(editor, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()

        self.assertFalse(bar.isHidden())
        self.assertTrue(bar.is_replace_visible())

    def test_escape_closes_panel_and_clears_highlights(self) -> None:
        _host, bar, editor, controller = self._build("alpha alpha\n")

        bar.search_input.setText("alpha")
        self.app.processEvents()
        self.assertEqual(len(editor.extraSelections()), 3)

        QTest.keyClick(editor, Qt.Key.Key_Escape)
        self.app.processEvents()

        self.assertTrue(bar.isHidden())
        self.assertEqual(controller.result.count, 0)
        self.assertEqual(len(editor.extraSelections()), 1)
        self.assertEqual(bar.counterLabel.text(), "")

    def test_goto_line_prompt_moves_cursor(self) -> None:
        _host, _bar, editor, controller = self._build("a\nb\nc\nd\n")
        calls: list[dict] = []

        def fake_prompt(_parent, *, total_lines, current_line):
            calls.append({"total": total_lines, "current": current_line})
            return 3

        controller.set_goto_prompt(fake_prompt)
        QTest.keyClick(editor, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()

        self.assertEqual(calls, [{"total": 5, "current": 1}])
        self.assertEqual(editor.textCursor().blockNumber(), 2)

    def test_goto_line_cancel_keeps_cursor(self) -> None:
        _host, _bar, editor, controller = self._build("a\nb\nc\n")
        controller.set_goto_prompt(lambda *_args, **_kwargs: None)

        self.assertFalse(controller.prompt_goto_line())
        self.assertEqual(editor.textCursor().blockNumber(), 0)

    def test_down_arrow_from_search_moves_focus_to_editor(self) -> None:
        host, bar, editor, _controller = self._build("alpha\n")
        host.show()
        bar.search_input.setFocus()
        self.app.processEvents()

        QTest.keyClick(bar.search_input, Qt.Key.Key_Down)
        self.app.processEvents()

        self.assertTrue(editor.hasFocus())


if __name__ == "__main__":
    unittest.main()
