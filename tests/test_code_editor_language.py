"""Поддержка языка в CodeEditor: подчёркивание, подсказки, исправления, F8.

Редактор проверяется с простым поставщиком языка (без winws2), а связка с
winws2 — отдельным тестом через ``Winws2EditorLanguageController``.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCharFormat
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication


@dataclass(frozen=True)
class _Edit:
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    text: str


@dataclass(frozen=True)
class _Fix:
    title: str
    edits: tuple


@dataclass(frozen=True)
class _Diagnostic:
    line: int
    start: int
    end: int
    severity: str
    message: str
    fixes: tuple = ()


@dataclass(frozen=True)
class _Item:
    label: str
    insert_text: str
    detail: str = ""
    kind: str = "value"
    reopen: bool = False


@dataclass(frozen=True)
class _Completion:
    line: int
    start: int
    end: int
    items: tuple


class _Language:
    """Помечает ошибкой каждое слово BAD, подсказывает слова на «--»."""

    def __init__(self) -> None:
        self.diagnose_calls = 0

    def diagnose(self, text):
        self.diagnose_calls += 1
        found = []
        for line_index, line in enumerate(text.split("\n")):
            column = line.find("BAD")
            if column >= 0:
                fix = _Fix("Заменить на GOOD", (_Edit(line_index, column, line_index, column + 3, "GOOD"),))
                found.append(_Diagnostic(line_index, column, column + 3, "error", "Плохое слово", (fix,)))
        return tuple(found)

    def complete(self, text, line, column, *, explicit):
        current = text.split("\n")[line][:column]
        word = current.split(" ")[-1]
        if not word.startswith("--") and not explicit:
            return None
        items = tuple(
            _Item(label, label + "=", f"описание {label}")
            for label in ("--payload", "--port", "--name")
            if label.startswith(word)
        )
        return _Completion(line, column - len(word), column, items)

    def describe(self, text, line, column):
        return "Описание слова"

    def quick_actions(self, text, line, column):
        return ()


class CodeEditorLanguageTests(unittest.TestCase):
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
        editor.resize(700, 400)
        editor.show()
        editor.set_language_support(_Language())
        if text:
            editor.setPlainText(text)
        editor.refresh_diagnostics()
        return editor

    def _place(self, editor, line: int, column: int = 0) -> None:
        block = editor.document().findBlockByNumber(line)
        cursor = editor.textCursor()
        cursor.setPosition(block.position() + column)
        editor.setTextCursor(cursor)

    def test_problems_are_underlined_without_touching_text(self) -> None:
        editor = self._editor("ok\nsome BAD word\n")
        editor.document().setModified(False)
        edits: list[bool] = []
        editor.contentEdited.connect(lambda: edits.append(True))

        editor.refresh_diagnostics()

        self.assertEqual(editor.toPlainText(), "ok\nsome BAD word\n")
        self.assertFalse(editor.document().isModified())
        self.assertEqual(edits, [])
        underlines = [
            selection for selection in editor.extraSelections()
            if selection.format.underlineStyle() == QTextCharFormat.UnderlineStyle.WaveUnderline
        ]
        self.assertEqual(len(underlines), 1)
        self.assertEqual(underlines[0].cursor.selectedText(), "BAD")
        self.assertEqual(editor._match_ruler.problem_lines, (1,))

    def test_problems_summary_and_current_line_message(self) -> None:
        editor = self._editor("BAD\nok\n")
        summaries = []
        editor.problemsChanged.connect(summaries.append)
        self._place(editor, 0, 1)
        editor.refresh_diagnostics()
        self.assertEqual(summaries[-1].errors, 1)
        self.assertEqual(summaries[-1].current_message, "Плохое слово")
        self._place(editor, 1)
        self.assertEqual(summaries[-1].current_message, "")

    def test_f8_moves_to_next_problem_and_wraps(self) -> None:
        editor = self._editor("a BAD\nb\nc BAD\n")
        self._place(editor, 0)
        QTest.keyClick(editor, Qt.Key.Key_F8)
        self.assertEqual((editor.textCursor().blockNumber(), editor.textCursor().positionInBlock()), (0, 2))
        QTest.keyClick(editor, Qt.Key.Key_F8)
        self.assertEqual(editor.textCursor().blockNumber(), 2)
        QTest.keyClick(editor, Qt.Key.Key_F8)
        self.assertEqual(editor.textCursor().blockNumber(), 0)
        QTest.keyClick(editor, Qt.Key.Key_F8, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(editor.textCursor().blockNumber(), 2)

    def test_quick_fix_is_one_undo_step(self) -> None:
        editor = self._editor("x BAD y\n")
        self._place(editor, 0, 3)
        fixes = editor.quick_fixes_at_cursor()
        self.assertEqual([fix.title for fix in fixes], ["Заменить на GOOD"])

        self.assertTrue(editor.apply_quick_fix(fixes[0]))
        self.assertEqual(editor.toPlainText(), "x GOOD y\n")
        self.assertEqual(editor.diagnostics(), ())
        editor.undo()
        self.assertEqual(editor.toPlainText(), "x BAD y\n")

    def test_typing_opens_suggestions_enter_inserts_escape_closes(self) -> None:
        editor = self._editor("")
        escapes = []
        editor.escapePressed.connect(lambda: escapes.append(True))
        editor.setFocus()

        QTest.keyClicks(editor, "--pa")
        self.assertTrue(editor.is_completion_visible())
        self.assertEqual(editor.completion_popup().row_count(), 1)
        self.assertFalse(editor.completion_popup().isWindow())

        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.assertEqual(editor.toPlainText(), "--payload=")
        self.assertFalse(editor.is_completion_visible())

        QTest.keyClicks(editor, " --")
        self.assertTrue(editor.is_completion_visible())
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        self.assertFalse(editor.is_completion_visible())
        self.assertEqual(escapes, [])
        self.assertEqual(editor.toPlainText(), "--payload= --")

    def test_arrow_keys_choose_suggestion_and_ctrl_space_opens_it(self) -> None:
        editor = self._editor("")
        editor.setFocus()
        QTest.keyClick(editor, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(editor.is_completion_visible())
        self.assertEqual(editor.completion_popup().row_count(), 3)
        QTest.keyClick(editor, Qt.Key.Key_Down)
        QTest.keyClick(editor, Qt.Key.Key_Enter)
        self.assertEqual(editor.toPlainText(), "--port=")

    def test_popup_never_covers_the_line_being_typed(self) -> None:
        editor = self._editor("\n".join(f"line {i}" for i in range(80)))
        editor.setFocus()
        for line, scroll in ((70, 70 - 20), (3, 0)):
            self._place(editor, line, len(f"line {line}"))
            editor.verticalScrollBar().setValue(scroll)
            self.app.processEvents()
            editor.ensureCursorVisible()
            QTest.keyClicks(editor, " --")
            with self.subTest(line=line):
                self.assertTrue(editor.is_completion_visible())
                self.assertFalse(editor.completion_popup().geometry().intersects(editor.cursorRect()))
            QTest.keyClick(editor, Qt.Key.Key_Escape)

    def test_wheel_over_popup_scrolls_text_and_closes_list(self) -> None:
        from PyQt6.QtCore import QPoint, QPointF
        from PyQt6.QtGui import QWheelEvent

        editor = self._editor("\n".join(f"line {i}" for i in range(200)))
        editor.setFocus()
        self._place(editor, 5, 6)
        QTest.keyClicks(editor, " --")
        popup = editor.completion_popup()
        self.assertTrue(editor.is_completion_visible())
        before = editor.verticalScrollBar().value()
        event = QWheelEvent(
            QPointF(5, 5), QPointF(popup.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
        )
        QApplication.sendEvent(popup.list_widget.viewport(), event)
        self.assertFalse(editor.is_completion_visible())
        self.assertGreater(editor.verticalScrollBar().value(), before)

    def test_problems_are_not_rechecked_while_list_is_open(self) -> None:
        editor = self._editor("")
        language = editor.language_support()
        editor.setFocus()
        QTest.keyClicks(editor, "BAD --")
        self.assertTrue(editor.is_completion_visible())
        calls = language.diagnose_calls
        editor._on_diagnostics_timer()
        self.assertEqual(language.diagnose_calls, calls)
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        QTest.qWait(400)
        self.assertGreater(language.diagnose_calls, calls)
        self.assertEqual(len(editor.diagnostics()), 1)

    def test_editor_without_language_keeps_old_behaviour(self) -> None:
        from ui.code_editor.editor import CodeEditor

        editor = CodeEditor()
        self.addCleanup(editor.deleteLater)
        editor.setPlainText("BAD\n")
        QTest.keyClicks(editor, "--pa")
        self.assertIsNone(editor.completion_popup())
        self.assertEqual(editor.diagnostics(), ())


class Winws2EditorLanguageControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _wait(self, predicate, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return bool(predicate())

    def test_background_facts_enable_blob_declaration_fix(self) -> None:
        from profile.ui.winws2_editor_language import Winws2EditorLanguageController
        from ui.code_editor.editor import CodeEditor

        editor = CodeEditor()
        self.addCleanup(editor.deleteLater)
        controller = Winws2EditorLanguageController(
            editor,
            current_text=editor.toPlainText,
            application_root=None,
            load_fake_values=lambda: {"tls_google": "@bin/tls_google.bin"},
        )
        self.addCleanup(controller.cleanup)
        editor.setPlainText("--wf-tcp-out=443\n--filter-tcp=443\n--lua-desync=fake:blob=tls_google\n")
        controller.schedule_scan(0)

        self.assertTrue(self._wait(lambda: bool(controller.language.fake_values)))
        problems = [d for d in editor.diagnostics() if "tls_google" in d.message]
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].fixes)
        self.assertIn("Ctrl+.", editor.accessibleDescription())


class PresetStatusBarProblemsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_counter_and_current_message(self) -> None:
        from presets.ui.common.preset_status_bar import PresetStatusBar, build_problems_counter_text

        self.assertEqual(build_problems_counter_text(2, 1), "2 ошибки, 1 предупреждение")
        self.assertEqual(build_problems_counter_text(5, 0), "5 ошибок")
        self.assertEqual(build_problems_counter_text(0, 0), "")

        bar = PresetStatusBar()
        self.addCleanup(bar.deleteLater)
        clicks = []
        bar.problemsClicked.connect(lambda: clicks.append(True))
        bar.set_problems(errors=1, warnings=0, current_message="Нет значения", current_severity="error")
        self.assertEqual(bar.problems_button.text(), "1 ошибка")
        self.assertFalse(bar.problems_button.isHidden())
        self.assertEqual(bar.problem_label.text(), "Нет значения")
        bar.problems_button.click()
        self.assertEqual(clicks, [True])

        bar.set_problems(errors=0, warnings=0)
        self.assertTrue(bar.problems_button.isHidden())
        self.assertTrue(bar.problem_label.isHidden())

    def test_long_messages_wrap_instead_of_being_cut(self) -> None:
        from presets.ui.common.preset_status_bar import PresetStatusBar, build_preset_status_plan

        bar = PresetStatusBar()
        self.addCleanup(bar.deleteLater)
        long_text = "Ошибка переключения пресета: winws2 не запустился. " * 6
        message = "Функции «hostfakespli» нет в подключённых lua-файлах. Возможно, имелось в виду hostfakesplit. " * 2
        bar.set_plan(build_preset_status_plan("error", launch_method="direct_zapret2", text=long_text))
        bar.set_problems(errors=1, warnings=0, current_message=message, current_severity="error")
        bar.resize(900, 24)
        bar.show()
        self.app.processEvents()

        self.assertEqual(bar.text_label.text(), long_text.strip())
        self.assertEqual(bar.problem_label.text(), " ".join(message.split()))
        self.assertTrue(bar.text_label.wordWrap())
        self.assertTrue(bar.problem_label.wordWrap())
        self.assertGreater(bar.sizeHint().height(), 24)


if __name__ == "__main__":
    unittest.main()
