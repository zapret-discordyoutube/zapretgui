"""Поддержка языка в редакторе: проблемы, подсказки, описания, исправления.

Редактор не знает, какой язык редактирует: страница передаёт объект
``EditorLanguageSupport``. Результаты читаются по именам полей, поэтому
поставщик может вернуть свои неизменяемые объекты с такими же полями.

Строки и колонки считаются с нуля; ``end`` не включается.
"""

from __future__ import annotations

from typing import Protocol, Sequence

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_HINT = "hint"
SEVERITY_ORDER = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_HINT: 2}


class EditorTextEdit(Protocol):
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    text: str


class EditorQuickFix(Protocol):
    title: str
    edits: Sequence[EditorTextEdit]


class EditorDiagnostic(Protocol):
    line: int
    start: int
    end: int
    severity: str
    message: str
    fixes: Sequence[EditorQuickFix]


class EditorCompletionItem(Protocol):
    label: str
    insert_text: str
    detail: str
    kind: str
    reopen: bool


class EditorCompletion(Protocol):
    line: int
    start: int
    end: int
    items: Sequence[EditorCompletionItem]


class EditorLanguageSupport(Protocol):
    def diagnose(self, text: str) -> Sequence[EditorDiagnostic]: ...

    def complete(self, text: str, line: int, column: int, *, explicit: bool) -> EditorCompletion | None: ...

    def describe(self, text: str, line: int, column: int) -> str: ...

    def quick_actions(self, text: str, line: int, column: int) -> Sequence[EditorQuickFix]: ...


def worst_severity(severities) -> str:
    best = ""
    for severity in severities:
        if not best or SEVERITY_ORDER.get(severity, 9) < SEVERITY_ORDER.get(best, 9):
            best = severity
    return best


__all__ = [
    "EditorCompletion",
    "EditorCompletionItem",
    "EditorDiagnostic",
    "EditorLanguageSupport",
    "EditorQuickFix",
    "EditorTextEdit",
    "SEVERITY_ERROR",
    "SEVERITY_HINT",
    "SEVERITY_ORDER",
    "SEVERITY_WARNING",
    "worst_severity",
]
