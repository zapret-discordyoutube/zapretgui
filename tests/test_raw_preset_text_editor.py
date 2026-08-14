from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget


class RawPresetTextEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.closeAllWindows()
        self.app.processEvents()

    def test_enter_finds_next_match_and_shift_enter_finds_previous_match(self) -> None:
        from presets.ui.common.raw_preset_text_editor import RawPresetTextEditor

        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        editor = RawPresetTextEditor(
            parent,
            request_save=lambda *, publish_content_changed=False: True,
            set_footer=lambda _text: None,
            cleanup_in_progress=lambda: False,
        )
        self.addCleanup(editor.cleanup)
        editor.apply_loaded_text("alpha\nbeta\nalpha\nbeta\nalpha\n")
        editor.search_input.setText("alpha")
        self.app.processEvents()

        first_cursor = editor.editor.textCursor()
        self.assertEqual(first_cursor.selectedText(), "alpha")
        self.assertEqual(first_cursor.selectionStart(), 0)

        QTest.keyClick(editor.search_input, Qt.Key.Key_Return)
        second_cursor = editor.editor.textCursor()
        self.assertEqual(second_cursor.selectedText(), "alpha")
        self.assertEqual(second_cursor.selectionStart(), len("alpha\nbeta\n"))

        QTest.keyClick(editor.search_input, Qt.Key.Key_Return)
        third_cursor = editor.editor.textCursor()
        self.assertEqual(third_cursor.selectedText(), "alpha")
        self.assertEqual(third_cursor.selectionStart(), len("alpha\nbeta\nalpha\nbeta\n"))

        QTest.keyClick(editor.search_input, Qt.Key.Key_Return)
        wrapped_cursor = editor.editor.textCursor()
        self.assertEqual(wrapped_cursor.selectedText(), "alpha")
        self.assertEqual(wrapped_cursor.selectionStart(), 0)

        QTest.keyClick(editor.search_input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        previous_cursor = editor.editor.textCursor()
        self.assertEqual(previous_cursor.selectedText(), "alpha")
        self.assertEqual(previous_cursor.selectionStart(), len("alpha\nbeta\nalpha\nbeta\n"))

    def test_external_text_update_is_not_applied_over_local_unsaved_changes(self) -> None:
        from presets.ui.common.raw_preset_text_editor import RawPresetTextEditor

        footer_messages: list[str] = []
        save_requests: list[bool] = []
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        editor = RawPresetTextEditor(
            parent,
            request_save=lambda *, publish_content_changed=False: save_requests.append(
                bool(publish_content_changed)
            ) or True,
            set_footer=footer_messages.append,
            cleanup_in_progress=lambda: False,
        )
        self.addCleanup(editor.cleanup)
        editor.apply_loaded_text("--new\nold\n")
        editor.editor.setPlainText("--new\nlocal edit\n")
        self.app.processEvents()

        applied = editor.apply_external_text_update("--new\nexternal edit\n")

        self.assertFalse(applied)
        self.assertEqual(editor.current_text(), "--new\nlocal edit\n")
        self.assertEqual(editor.editor.toPlainText(), "--new\nlocal edit\n")
        self.assertIn("не перезаписаны", footer_messages[-1])
        self.assertEqual(save_requests, [])

    def test_multiline_clipboard_paste_is_not_lost_from_text_cache(self) -> None:
        from presets.ui.common.raw_preset_text_editor import RawPresetTextEditor

        # Регрессия: вставка CRLF-многострочного текста в пустой редактор даёт
        # contentsChange (0, 1, N+1) — Qt учитывает финальный разделитель блока,
        # и инкрементальный патчинг кэша молча терял вставленный текст.
        # Кэш обязан совпадать с документом при сохранении.
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        editor = RawPresetTextEditor(
            parent,
            request_save=lambda *, publish_content_changed=False: True,
            set_footer=lambda _text: None,
            cleanup_in_progress=lambda: False,
        )
        self.addCleanup(editor.cleanup)
        editor.apply_loaded_text("")

        self.app.clipboard().setText("--new\r\n--filter-tcp=443\r\n--hostlist=list.txt")
        editor.editor.paste()
        self.app.processEvents()

        document_text = editor.editor.toPlainText()
        self.assertEqual(document_text, "--new\n--filter-tcp=443\n--hostlist=list.txt")
        self.assertEqual(editor.current_text(), document_text)
        self.assertEqual(editor.resolve_save_text(None), document_text)

    def test_paste_over_selection_keeps_cache_in_sync_with_document(self) -> None:
        from presets.ui.common.raw_preset_text_editor import RawPresetTextEditor

        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        editor = RawPresetTextEditor(
            parent,
            request_save=lambda *, publish_content_changed=False: True,
            set_footer=lambda _text: None,
            cleanup_in_progress=lambda: False,
        )
        self.addCleanup(editor.cleanup)
        editor.apply_loaded_text("--old\n--filter-tcp=80\n")
        # Прогреваем мемо, чтобы поймать именно рассинхрон после вставки.
        self.assertEqual(editor.current_text(), "--old\n--filter-tcp=80\n")

        editor.editor.selectAll()
        self.app.clipboard().setText("--new\r\n--filter-tcp=443\r\n")
        editor.editor.paste()
        self.app.processEvents()

        document_text = editor.editor.toPlainText()
        self.assertEqual(document_text, "--new\n--filter-tcp=443\n")
        self.assertEqual(editor.current_text(), document_text)
        self.assertEqual(editor.resolve_save_text(None), document_text)


    def _editor(self, **kwargs):
        from presets.ui.common.raw_preset_text_editor import RawPresetTextEditor

        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        options = {
            "request_save": lambda *, publish_content_changed=False: True,
            "set_footer": lambda _text: None,
            "cleanup_in_progress": lambda: False,
        }
        options.update(kwargs)
        editor = RawPresetTextEditor(parent, **options)
        self.addCleanup(editor.cleanup)
        return editor

    def test_search_highlights_every_match_in_preset_text(self) -> None:
        editor = self._editor()
        editor.apply_loaded_text("--new\n--filter-tcp=443\n--new\n")

        editor.search_input.setText("--new")
        self.app.processEvents()

        self.assertEqual(editor.find_controller.result.count, 2)
        # текущая строка + два совпадения
        self.assertEqual(len(editor.editor.extraSelections()), 3)
        self.assertEqual(editor.find_bar.counterLabel.text(), "1 / 2")

    def test_line_operations_keep_text_cache_in_sync(self) -> None:
        editor = self._editor()
        editor.apply_loaded_text("--new\n--filter-tcp=80\n")
        # Прогреваем мемо, чтобы поймать рассинхрон кэша после правки строк.
        self.assertEqual(editor.current_text(), "--new\n--filter-tcp=80\n")

        editor.editor.duplicate_selected_lines()
        editor.editor.move_selected_lines(delta=1)
        editor.editor.toggle_selected_comment()
        self.app.processEvents()

        document_text = editor.editor.toPlainText()
        self.assertEqual(editor.current_text(), document_text)
        self.assertEqual(editor.resolve_save_text(None), document_text)

    def test_replace_all_keeps_text_cache_in_sync_and_marks_dirty(self) -> None:
        save_requests: list[bool] = []
        editor = self._editor(
            request_save=lambda *, publish_content_changed=False: save_requests.append(
                bool(publish_content_changed)
            )
            or True,
        )
        editor.apply_loaded_text("--filter-tcp=80\n--filter-udp=80\n")
        self.assertEqual(editor.current_text(), "--filter-tcp=80\n--filter-udp=80\n")

        editor.find_bar.search_input.setText("80")
        editor.find_bar.replace_input.setText("443")
        self.app.processEvents()
        replaced = editor.find_controller.replace_all()
        self.app.processEvents()

        self.assertEqual(replaced, 2)
        document_text = editor.editor.toPlainText()
        self.assertEqual(document_text, "--filter-tcp=443\n--filter-udp=443\n")
        self.assertEqual(editor.current_text(), document_text)
        self.assertTrue(editor.content_publish_pending)

    def test_cursor_status_callback_receives_position(self) -> None:
        statuses: list[str] = []
        editor = self._editor(set_cursor_status=statuses.append)
        editor.apply_loaded_text("--new\n--filter-tcp=443\n")

        editor.editor.goto_line(2)
        self.app.processEvents()

        self.assertTrue(statuses)
        self.assertIn("Стр 2", statuses[-1])
        self.assertIn("всего строк: 3", statuses[-1])

    def test_theme_change_does_not_mark_preset_as_edited(self) -> None:
        # Регрессия: QSyntaxHighlighter.rehighlight() закрывает edit-block и Qt
        # эмитит textChanged без правки текста — смена темы помечала пресет
        # изменённым и запускала автосохранение.
        footer_messages: list[str] = []
        save_requests: list[bool] = []
        editor = self._editor(
            request_save=lambda *, publish_content_changed=False: save_requests.append(
                bool(publish_content_changed)
            )
            or True,
            set_footer=footer_messages.append,
        )
        editor.apply_loaded_text("--new\n--filter-tcp=443\n")
        editor.content_dirty = False
        editor.content_publish_pending = False
        footer_messages.clear()

        editor.editor._apply_highlighter_theme(True)
        editor.editor._apply_highlighter_theme(False)
        self.app.processEvents()

        self.assertFalse(editor.content_publish_pending)
        self.assertFalse(editor.content_dirty)
        self.assertEqual(footer_messages, [])
        self.assertEqual(save_requests, [])
        self.assertFalse(editor.save_timer.isActive())

    def test_user_edit_still_marks_preset_as_edited(self) -> None:
        footer_messages: list[str] = []
        editor = self._editor(set_footer=footer_messages.append)
        editor.apply_loaded_text("--new\n")
        editor.content_publish_pending = False
        footer_messages.clear()

        editor.editor.insertPlainText("--filter-tcp=443\n")
        self.app.processEvents()

        self.assertTrue(editor.content_publish_pending)
        self.assertIn("Изменения", footer_messages[-1])

    def test_editor_uses_shared_code_editor_widget(self) -> None:
        from ui.code_editor.editor import CodeEditor
        from ui.code_editor.find_bar import FindReplaceBar

        editor = self._editor()

        self.assertIsInstance(editor.editor, CodeEditor)
        self.assertIsInstance(editor.find_bar, FindReplaceBar)
        self.assertIs(editor.search_input, editor.find_bar.search_input)


if __name__ == "__main__":
    unittest.main()
