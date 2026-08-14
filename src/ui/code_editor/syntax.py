"""Подсветка синтаксиса конфигурации winws2/winws и файлов списков.

Палитра берётся только из темы qfluentwidgets: акцент (`themeColor()`) и
штатный цвет текста виджета. Роли различаются насыщенностью и начертанием,
а не собственными цветами, — иначе подсветка спорит с выбранной темой и
акцентом пользователя.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PyQt6.QtCore import QRegularExpression
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

# Насыщенность роли: (источник цвета, альфа, жирный, курсив).
# «accent» — акцент темы, «text» — штатный цвет текста редактора.
ROLE_STYLES: dict[str, tuple[str, int, bool, bool]] = {
    "comment": ("text", 110, False, True),
    "operator": ("text", 150, False, False),
    "flag": ("accent", 255, False, False),
    "section": ("accent", 255, True, False),
    "label": ("accent", 190, False, False),
    "number": ("accent", 160, False, False),
    "path": ("text", 235, False, False),
}


@dataclass(frozen=True, slots=True)
class SyntaxTheme:
    accent: QColor
    text: QColor

    def key(self) -> tuple[str, str]:
        return (self.accent.name(QColor.NameFormat.HexArgb), self.text.name(QColor.NameFormat.HexArgb))


def _role_color(theme: SyntaxTheme, role: str) -> QColor:
    source, alpha, _bold, _italic = ROLE_STYLES[role]
    base = theme.accent if source == "accent" else theme.text
    color = QColor(base)
    color.setAlpha(int(alpha))
    return color


class BaseSyntaxHighlighter(QSyntaxHighlighter):
    """Общая механика: правила «регулярка → роль» плюс тема из qfluentwidgets."""

    RULES: tuple[tuple[str, str], ...] = ()
    COMMENT_RULE: str | None = None

    def __init__(self, document, *, theme: SyntaxTheme | None = None) -> None:
        super().__init__(document)
        self._theme = theme or SyntaxTheme(QColor("#0078d4"), QColor("#000000"))
        self._formats: dict[str, QTextCharFormat] = {}
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._comment_expression = (
            QRegularExpression(self.COMMENT_RULE) if self.COMMENT_RULE else None
        )
        self._rebuild_formats()

    @property
    def theme(self) -> SyntaxTheme:
        return self._theme

    def apply_theme(self, theme: SyntaxTheme) -> bool:
        """Перекрашивает подсветку; возвращает True, если цвета изменились."""
        if theme.key() == self._theme.key():
            return False
        self._theme = theme
        self._rebuild_formats()
        try:
            self.rehighlight()
        except Exception:
            pass
        return True

    def _rebuild_formats(self) -> None:
        self._formats = {}
        for role, (_source, _alpha, bold, italic) in ROLE_STYLES.items():
            text_format = QTextCharFormat()
            text_format.setForeground(_role_color(self._theme, role))
            if bold:
                text_format.setFontWeight(QFont.Weight.Bold)
            if italic:
                text_format.setFontItalic(True)
            self._formats[role] = text_format

        self._rules = [
            (QRegularExpression(pattern), self._formats[role]) for pattern, role in self.RULES
        ]

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        line = str(text or "")
        if not line:
            return

        if self._comment_expression is not None:
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


class PresetSyntaxHighlighter(BaseSyntaxHighlighter):
    """Строки вида `--flag=value` с комментариями `#` (winws2/winws)."""

    # Порядок важен: правила применяются последовательно, поэтому более
    # специфичные (метка blob, --new) идут после общих.
    RULES = (
        (r"(?<![\w-])--[A-Za-z][\w-]*", "flag"),
        (r"=", "operator"),
        (r"(?<![\w.])0x[0-9A-Fa-f]+", "number"),
        (r"(?<![\w.:-])\d+(?:-\d+)?(?![\w.-])", "number"),
        (r"@?[\w./\\-]*\.(?:txt|bin|lua|log|list|json)(?![\w])", "path"),
        (r"(?<==)[A-Za-z_][\w]*(?=:)", "label"),
        (r"(?<![\w-])--new(?![\w-])", "section"),
    )
    COMMENT_RULE = r"^\s*#.*$"


class ListFileSyntaxHighlighter(BaseSyntaxHighlighter):
    """Файлы списков: домены, IP/CIDR, маски и комментарии `#`."""

    RULES = (
        (r"^\s*[\w.*-]+", "path"),
        (r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?", "number"),
        (r"\*", "section"),
        (r":\d+", "number"),
    )
    COMMENT_RULE = r"^\s*[#;].*$"


def is_comment_line(line: str) -> bool:
    """Строка целиком является комментарием."""
    return bool(re.match(r"^\s*[#;]", str(line or "")))
