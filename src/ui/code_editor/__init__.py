"""Переиспользуемый текстовый редактор кода/конфигов для страниц приложения.

Пакет не знает о пресетах, профилях и файловых операциях: страница передаёт
только текст и (при необходимости) фабрику подсветки синтаксиса.
"""

from ui.code_editor.editor import CodeEditor, CursorStatus, build_cursor_status_text
from ui.code_editor.find_bar import FindReplaceBar
from ui.code_editor.find_controller import FindController
from ui.code_editor.find_engine import (
    SearchMatch,
    SearchOptions,
    SearchResult,
    build_counter_text,
    find_matches,
    locate_match,
    replace_all,
)
from ui.code_editor.syntax import (
    ListFileSyntaxHighlighter,
    PresetSyntaxHighlighter,
    SyntaxTheme,
)

__all__ = [
    "CodeEditor",
    "CursorStatus",
    "FindController",
    "FindReplaceBar",
    "ListFileSyntaxHighlighter",
    "PresetSyntaxHighlighter",
    "SyntaxTheme",
    "SearchMatch",
    "SearchOptions",
    "SearchResult",
    "build_counter_text",
    "build_cursor_status_text",
    "find_matches",
    "locate_match",
    "replace_all",
]
