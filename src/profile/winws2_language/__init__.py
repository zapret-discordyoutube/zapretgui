"""Язык пресета winws2: справочник опций и Lua-функций, проверка и подсказки.

Пакет чистый: не знает про Qt и не читает файлы (кроме
``collect_winws2_file_facts``, которую зовут только из рабочего потока).
Редактор пресета получает отсюда проблемы с местом в тексте и исправлениями,
подсказки при наборе, описания при наведении и быстрые действия.
"""

from .completion import complete_winws2
from .context import EMPTY_CONTEXT, LanguageContext
from .diagnostics import count_by_severity, diagnose_winws2_text, problems_summary_text
from .file_refs import FileFacts, collect_winws2_file_facts
from .hover import describe_winws2_at, quick_actions_winws2
from .model import (
    SEVERITY_ERROR,
    SEVERITY_HINT,
    SEVERITY_WARNING,
    CompletionItem,
    CompletionResult,
    Diagnostic,
    QuickFix,
    TextEdit,
)

__all__ = [
    "CompletionItem",
    "CompletionResult",
    "Diagnostic",
    "EMPTY_CONTEXT",
    "FileFacts",
    "LanguageContext",
    "QuickFix",
    "SEVERITY_ERROR",
    "SEVERITY_HINT",
    "SEVERITY_WARNING",
    "TextEdit",
    "collect_winws2_file_facts",
    "complete_winws2",
    "count_by_severity",
    "describe_winws2_at",
    "diagnose_winws2_text",
    "problems_summary_text",
    "quick_actions_winws2",
]
