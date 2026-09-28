"""Результаты анализа текста пресета: проблемы, исправления, подсказки.

Имена полей совпадают с протоколом редактора ``ui.code_editor.language``:
редактор читает эти объекты напрямую, без перекладывания. Строки и колонки
считаются с нуля, колонка — позиция символа внутри строки (``end`` не
включается).
"""

from __future__ import annotations

from dataclasses import dataclass

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_HINT = "hint"


@dataclass(frozen=True, slots=True)
class TextEdit:
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    text: str


@dataclass(frozen=True, slots=True)
class QuickFix:
    title: str
    edits: tuple[TextEdit, ...]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    line: int
    start: int
    end: int
    severity: str
    message: str
    fixes: tuple[QuickFix, ...] = ()


@dataclass(frozen=True, slots=True)
class CompletionItem:
    label: str
    insert_text: str
    detail: str = ""
    kind: str = "value"
    # После вставки сразу открыть подсказки снова (например, после «--payload=»).
    reopen: bool = False


@dataclass(frozen=True, slots=True)
class CompletionResult:
    line: int
    start: int
    end: int
    items: tuple[CompletionItem, ...]


def replace_in_line(line: int, start: int, end: int, text: str) -> TextEdit:
    return TextEdit(line, int(start), line, int(end), str(text))


def insert_line_before(line: int, text: str) -> TextEdit:
    return TextEdit(line, 0, line, 0, f"{text}\n")


__all__ = [
    "CompletionItem",
    "CompletionResult",
    "Diagnostic",
    "QuickFix",
    "SEVERITY_ERROR",
    "SEVERITY_HINT",
    "SEVERITY_WARNING",
    "TextEdit",
    "insert_line_before",
    "replace_in_line",
]
