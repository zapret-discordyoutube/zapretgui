"""Большой текст в поле только для чтения — частями, без подвисания окна.

`setPlainText` раскладывает и подсвечивает весь текст за один вызов в потоке
интерфейса. Для обычного списка это мгновенно, но системная база бывает
огромной. Замер на быстром компьютере (поле с подсветкой списков):

    cloudflare-ipset.txt       7 900 строк      52 мс
    ipset-all.txt             33 000 строк     215 мс
    russia-blacklist.txt     117 000 строк     686 мс

На слабом компьютере это секунды, в которые окно не отвечает.

Здесь начало текста показывается сразу, а остальное дописывается в конец
порциями: между порциями Qt успевает обработать мышь, клавиатуру и отрисовку.
Итоговый текст в поле тот же самый, строка в строку.

Только для полей, которые пользователь не редактирует: пока текст
дописывается, в поле лежит лишь его начало, и сохранять оттуда нельзя.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QTextCursor


# Текст короче этого вставляется обычным setPlainText: порции ему не нужны.
CHUNKED_FILL_FIRST_LINES = 1_000
# Порция с подсветкой занимает ~6 мс на быстром компьютере и ~25 мс на слабом.
CHUNKED_FILL_CHUNK_LINES = 1_000


class ChunkedReadOnlyFill(QObject):
    """Заполняет поле только для чтения, не занимая интерфейс надолго."""

    def __init__(
        self,
        editor,
        *,
        first_lines: int = CHUNKED_FILL_FIRST_LINES,
        chunk_lines: int = CHUNKED_FILL_CHUNK_LINES,
    ) -> None:
        super().__init__(editor)
        self._editor = editor
        self._first_lines = max(1, int(first_lines))
        self._chunk_lines = max(1, int(chunk_lines))
        self._lines: list[str] = []
        self._next_line = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._append_next_chunk)

    def set_text(self, text: str) -> None:
        """Показывает начало текста сразу, остальное дописывает порциями."""
        self.stop()
        lines = str(text or "").split("\n")
        if len(lines) <= self._first_lines:
            self._editor.setPlainText(text)
            return
        self._lines = lines
        self._next_line = self._first_lines
        self._editor.setPlainText("\n".join(lines[: self._first_lines]))
        self._timer.start(0)

    def is_filling(self) -> bool:
        return self._next_line < len(self._lines)

    def stop(self) -> None:
        """Бросает недописанный текст: поле скрыто или в него идёт новый."""
        self._timer.stop()
        self._lines = []
        self._next_line = 0

    def finish_now(self) -> None:
        """Дописывает остаток сразу — когда нужен весь текст целиком."""
        self._timer.stop()
        while self.is_filling():
            self._append_chunk()

    def _append_next_chunk(self) -> None:
        if not self.is_filling():
            return
        try:
            self._append_chunk()
        except RuntimeError:
            # Поле уже уничтожено вместе со страницей.
            self._lines = []
            self._next_line = 0
            return
        if self.is_filling():
            self._timer.start(0)

    def _append_chunk(self) -> None:
        start = self._next_line
        end = min(len(self._lines), start + self._chunk_lines)
        chunk = "\n" + "\n".join(self._lines[start:end])
        self._next_line = end
        if end >= len(self._lines):
            self._lines = []
            self._next_line = 0
        document = self._editor.document()
        # Свой курсор документа, а не курсор поля: прокрутка и выделение,
        # которые видит пользователь, остаются на месте.
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        undo_enabled = document.isUndoRedoEnabled()
        document.setUndoRedoEnabled(False)
        try:
            cursor.insertText(chunk)
        finally:
            document.setUndoRedoEnabled(undo_enabled)


__all__ = [
    "CHUNKED_FILL_CHUNK_LINES",
    "CHUNKED_FILL_FIRST_LINES",
    "ChunkedReadOnlyFill",
]
