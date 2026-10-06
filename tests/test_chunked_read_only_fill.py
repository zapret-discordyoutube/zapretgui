"""Большой текст в поле только для чтения дописывается порциями."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

from ui.code_editor.chunked_fill import ChunkedReadOnlyFill  # noqa: E402


_APP = QApplication.instance() or QApplication([])


def _text(lines: int, *, final_newline: bool = True) -> str:
    body = "\n".join(f"site-{index}.example.com" for index in range(lines))
    return body + ("\n" if final_newline else "")


class ChunkedReadOnlyFillTests(unittest.TestCase):
    def _editor(self) -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        self.addCleanup(editor.deleteLater)
        return editor

    @staticmethod
    def _run_until_filled(fill: ChunkedReadOnlyFill) -> None:
        loop = QEventLoop()
        guard = QTimer()
        guard.setSingleShot(True)
        guard.timeout.connect(loop.quit)
        guard.start(5_000)
        poll = QTimer()
        poll.timeout.connect(lambda: None if fill.is_filling() else loop.quit())
        poll.start(1)
        loop.exec()
        poll.stop()
        guard.stop()

    def test_small_text_is_set_at_once(self) -> None:
        editor = self._editor()
        fill = ChunkedReadOnlyFill(editor, first_lines=100, chunk_lines=50)
        text = _text(20)

        fill.set_text(text)

        self.assertFalse(fill.is_filling())
        self.assertEqual(editor.toPlainText(), text)

    def test_big_text_shows_its_start_at_once_and_does_not_block(self) -> None:
        # Раньше 117 тысяч строк системной базы вставлялись одним вызовом:
        # 686 мс на быстром компьютере, окно в это время не отвечало.
        editor = self._editor()
        fill = ChunkedReadOnlyFill(editor, first_lines=100, chunk_lines=50)
        text = _text(1_000)

        fill.set_text(text)

        self.assertTrue(fill.is_filling())
        self.assertEqual(editor.document().blockCount(), 100)
        self.assertEqual(editor.toPlainText(), "\n".join(text.split("\n")[:100]))

    def test_one_tick_adds_one_chunk(self) -> None:
        editor = self._editor()
        fill = ChunkedReadOnlyFill(editor, first_lines=100, chunk_lines=50)
        fill.set_text(_text(1_000))

        fill._append_next_chunk()

        self.assertEqual(editor.document().blockCount(), 150)
        self.assertTrue(fill.is_filling())

    def test_full_text_arrives_unchanged(self) -> None:
        for final_newline in (True, False):
            with self.subTest(final_newline=final_newline):
                editor = self._editor()
                fill = ChunkedReadOnlyFill(editor, first_lines=100, chunk_lines=70)
                text = _text(1_003, final_newline=final_newline)

                fill.set_text(text)
                self._run_until_filled(fill)

                self.assertFalse(fill.is_filling())
                self.assertEqual(editor.toPlainText(), text)

    def test_new_text_replaces_unfinished_one(self) -> None:
        editor = self._editor()
        fill = ChunkedReadOnlyFill(editor, first_lines=100, chunk_lines=50)
        fill.set_text(_text(1_000))
        fill._append_next_chunk()
        new_text = "\n".join(f"10.0.{index}.0/24" for index in range(400))

        fill.set_text(new_text)
        self._run_until_filled(fill)

        self.assertEqual(editor.toPlainText(), new_text)

    def test_stop_leaves_field_alone(self) -> None:
        editor = self._editor()
        fill = ChunkedReadOnlyFill(editor, first_lines=100, chunk_lines=50)
        fill.set_text(_text(1_000))

        fill.stop()
        _APP.processEvents()

        self.assertFalse(fill.is_filling())
        self.assertEqual(editor.document().blockCount(), 100)

    def test_finish_now_gives_whole_text(self) -> None:
        editor = self._editor()
        fill = ChunkedReadOnlyFill(editor, first_lines=100, chunk_lines=50)
        text = _text(1_000)
        fill.set_text(text)

        fill.finish_now()

        self.assertFalse(fill.is_filling())
        self.assertEqual(editor.toPlainText(), text)

    def test_filling_keeps_view_at_the_top_and_adds_no_undo_steps(self) -> None:
        editor = self._editor()
        fill = ChunkedReadOnlyFill(editor, first_lines=100, chunk_lines=50)

        fill.set_text(_text(1_000))
        self._run_until_filled(fill)

        self.assertEqual(editor.textCursor().position(), 0)
        self.assertEqual(editor.verticalScrollBar().value(), 0)
        self.assertFalse(editor.document().isUndoAvailable())

    def test_profile_page_fills_base_list_through_chunks(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src/profile/ui/profile_setup_page.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("self._list_file_base_fill = ChunkedReadOnlyFill(self._list_file_base_text)", source)
        self.assertIn("base_fill.set_text(base_text)", source)
        # Поле с записями пользователя порциями не заполняется: автосохранение
        # читает его текст и записало бы на диск только начало списка.
        self.assertNotIn("ChunkedReadOnlyFill(self._list_file_text)", source)


if __name__ == "__main__":
    unittest.main()
