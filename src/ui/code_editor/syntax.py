"""Подсветка синтаксиса конфигурации winws2/winws.

Палитра намеренно фиксированная (в духе VS Code Dark+/Light+), а не
производная от акцента темы: акцент выбирает пользователь, и произвольный
цвет легко делает текст нечитаемым. Акцент используется только для
служебных подсветок редактора (текущая строка, совпадения поиска).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PyQt6.QtCore import QRegularExpression
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat


@dataclass(frozen=True, slots=True)
class SyntaxPalette:
    comment: str
    flag: str
    section: str
    operator: str
    number: str
    path: str
    label: str


DARK_PALETTE = SyntaxPalette(
    comment="#6a9955",
    flag="#569cd6",
    section="#c586c0",
    operator="#9aa6b2",
    number="#b5cea8",
    path="#ce9178",
    label="#dcdcaa",
)

LIGHT_PALETTE = SyntaxPalette(
    comment="#008000",
    flag="#0451a5",
    section="#af00db",
    operator="#5c6773",
    number="#098658",
    path="#a31515",
    label="#795e26",
)


# Порядок важен: правила применяются последовательно, поэтому более
# специфичные (метка blob, --new) идут после общих.
_RULES: tuple[tuple[str, str, bool], ...] = (
    (r"(?<![\w-])--[A-Za-z][\w-]*", "flag", False),
    (r"=", "operator", False),
    (r"(?<![\w.])0x[0-9A-Fa-f]+", "number", False),
    (r"(?<![\w.:-])\d+(?:-\d+)?(?![\w.-])", "number", False),
    (r"@?[\w./\\-]*\.(?:txt|bin|lua|log|list|json)(?![\w])", "path", False),
    (r"(?<==)[A-Za-z_][\w]*(?=:)", "label", False),
    (r"(?<![\w-])--new(?![\w-])", "section", True),
)

_COMMENT_RULE = r"^\s*#.*$"


class PresetSyntaxHighlighter(QSyntaxHighlighter):
    """Подсветка строк вида `--flag=value` с комментариями `#`."""

    def __init__(self, document, *, is_light: bool = False) -> None:
        super().__init__(document)
        self._is_light = bool(is_light)
        self._formats: dict[str, QTextCharFormat] = {}
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._comment_expression = QRegularExpression(_COMMENT_RULE)
        self._rebuild_formats()

    @property
    def palette(self) -> SyntaxPalette:
        return LIGHT_PALETTE if self._is_light else DARK_PALETTE

    def set_light_theme(self, is_light: bool) -> bool:
        """Переключает палитру; возвращает True, если что-то изменилось."""
        value = bool(is_light)
        if value == self._is_light:
            return False
        self._is_light = value
        self._rebuild_formats()
        try:
            self.rehighlight()
        except Exception:
            pass
        return True

    def _rebuild_formats(self) -> None:
        palette = self.palette
        self._formats = {}
        for name in ("comment", "flag", "section", "operator", "number", "path", "label"):
            text_format = QTextCharFormat()
            text_format.setForeground(QColor(getattr(palette, name)))
            self._formats[name] = text_format
        self._formats["section"].setFontWeight(QFont.Weight.Bold)
        self._formats["comment"].setFontItalic(True)

        self._rules = []
        for pattern, name, bold in _RULES:
            expression = QRegularExpression(pattern)
            text_format = QTextCharFormat(self._formats[name])
            if bold:
                text_format.setFontWeight(QFont.Weight.Bold)
            self._rules.append((expression, text_format))

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        line = str(text or "")
        if not line:
            return

        comment_match = self._comment_expression.match(line)
        if comment_match.hasMatch():
            self.setFormat(0, len(line), self._formats["comment"])
            return

        for expression, text_format in self._rules:
            iterator = expression.globalMatch(line)
            while iterator.hasNext():
                match = iterator.next()
                start = match.capturedStart()
                length = match.capturedLength()
                if length > 0:
                    self.setFormat(start, length, text_format)


def is_comment_line(line: str) -> bool:
    """Строка целиком является комментарием пресета."""
    return bool(re.match(r"^\s*#", str(line or "")))
