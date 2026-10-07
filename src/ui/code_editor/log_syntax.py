"""Подсветка логов и текстовых отчётов проверок.

Лог читают глазами в поисках одного: что сломалось и где. Поэтому подсветка
отвечает на три вопроса:

- где раздел — заголовки (``=== Пинг ===``, ``ИТОГ``, ``Подробности:``)
  жирным акцентом, линии-разделители приглушены;
- что с результатом — слова и значки успеха, предупреждения и ошибки
  зелёным, оранжевым и красным (те же цвета, что на карточках проверок);
- о чём строка — адреса, сайты, время и параметры ``--flag`` выделены
  акцентом и насыщенностью, время в начале строки приглушено.

Разбор строки (``log_line_kind``) — чистая функция: по ней же страница
отчёта строит список разделов и считает ошибки.
"""

from __future__ import annotations

import re

from PyQt6.QtCore import QRegularExpression
from PyQt6.QtGui import QColor, QFont, QTextCharFormat

from ui.code_editor.syntax import BaseSyntaxHighlighter

KIND_PLAIN = ""
KIND_RULE = "rule"
KIND_HEADING = "heading"
KIND_OK = "ok"
KIND_WARN = "warn"
KIND_FAIL = "fail"

# (тёмная тема, светлая тема) — как на карточках результата.
_STATE_COLORS = {
    KIND_OK: ("#6ccb5f", "#0f7b0f"),
    KIND_WARN: ("#ffa033", "#a85d00"),
    KIND_FAIL: ("#ff5c5c", "#c42b1c"),
}

_RULE_LINE = re.compile(r"^\s*[=\-─━═*_~#]{3,}\s*$")
_FRAMED_HEADING = re.compile(r"^\s*[=\-─━═*#]{2,}\s*(?P<title>\S.*?)\s*[=\-─━═*#]{2,}\s*$")
_COLON_HEADING = re.compile(r"^(?P<title>[^\s\d\[\-•·*✓✗!].{1,58}):\s*$")
_LETTERS = re.compile(r"[^\W\d_]")
# Значок в строке красит её целиком: так строки результата видны боковым зрением.
_LINE_MARKS = (
    (KIND_FAIL, ("❌", "✗", "✘", "🚫", "⛔")),
    (KIND_WARN, ("⚠",)),
    (KIND_OK, ("✅", "✓", "✔")),
)

_OK_WORDS = (
    r"✅|✓|✔|(?<![\w-])(?:OK|SUCCESS|PASS(?:ED)?|WORKS|работает|работают|доступ(?:ен|на|но|ны)|"
    r"успе\w+|открывается|открываются|найден\w*|совпадает|совпадают)(?![\w-])"
)
_WARN_WORDS = (
    r"⚠️?|(?<![\w-])(?:WARN(?:ING)?|предупрежд\w+|через раз|нестабиль\w+|частично|"
    r"не полностью|медленн\w+|пропущен\w*)(?![\w-])"
)
# Идёт последним: «не работает» и «недоступен» должны перекрыть зелёное «работает».
_FAIL_WORDS = (
    r"❌|✗|✘|🚫|⛔|(?<![\w-])(?:FAIL(?:ED|URE)?|ERROR|TIMEOUT|TIMED OUT|BLOCKED|REFUSED|RESET|RST|"
    r"ошибк\w+|не работа\w+|не отвеча\w+|не открыва\w+|недоступ\w+|блокир\w+|заблокир\w+|"
    r"обрыв\w*|таймаут\w*|молчит|молчат|закрыт\w*|подмен\w+|перехват\w+|сайта нет|нет ответа)(?![\w-])"
)

_IPV6 = (
    r"(?<![\w:.])(?:(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,6}:(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?)(?![\w:])"
)
# Порядок важен: следующее правило перекрывает предыдущее.
_TOKEN_RULES = (
    (r"(?<![\w-])--[A-Za-z][\w-]*", "flag"),
    (r"(?<![\w.])\d+(?:[.,]\d+)?\s?(?:мс|ms|сек|с|s|КБ|МБ|KB|MB|%)(?![\w])", "measure"),
    (r"(?<![\w.@/-])(?:[a-z0-9-]+\.)+[a-z]{2,}(?![\w-])", "address"),
    (r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?(?:/\d{1,2})?(?![\w.])", "address"),
    (_IPV6, "address"),
    (r"\[[A-ZА-ЯЁ][A-ZА-ЯЁ0-9_ -]{1,18}\]", "tag"),
    # Время в начале строки: [12:34:56.789] или 12:34:56
    (r"^\s*\[?\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\]?", "time"),
)
# (источник цвета, альфа, жирный)
_TOKEN_STYLES = {
    "time": ("text", 105, False),
    "flag": ("accent", 200, False),
    "measure": ("text", 255, True),
    "address": ("accent", 255, False),
    "tag": ("accent", 170, True),
}


def heading_title(line: str) -> str:
    """Название раздела, если строка — заголовок; иначе пустая строка."""
    text = str(line or "")
    stripped = text.strip()
    if not stripped or _RULE_LINE.match(text):
        return ""
    framed = _FRAMED_HEADING.match(text)
    if framed:
        return framed.group("title").strip()
    letters = _LETTERS.findall(stripped)
    # Строка заглавными без отступа, не короче двух слов: «ПРОВЕРКА DNS-СЕРВЕРОВ».
    # Одно слово заглавными — это чаще ответ («TIMEOUT»), а не заголовок.
    shouting = stripped == stripped.upper() and " " in stripped and not text[:1].isspace()
    if shouting and len(letters) >= 6 and len(stripped) <= 70 and not stripped[:1].isdigit() and stripped[:1] != "[":
        return stripped.rstrip(":").strip()
    colon = _COLON_HEADING.match(text)
    if colon and len(letters) >= 3:
        return colon.group("title").strip()
    return ""


def log_line_kind(line: str) -> str:
    """Что это за строка: заголовок, линия-разделитель или строка результата со значком."""
    text = str(line or "")
    if not text.strip():
        return KIND_PLAIN
    if _RULE_LINE.match(text):
        return KIND_RULE
    if heading_title(text):
        return KIND_HEADING
    for kind, marks in _LINE_MARKS:
        if any(mark in text for mark in marks):
            return kind
    return KIND_PLAIN


def log_outline(text: str) -> tuple[tuple[int, str], ...]:
    """Разделы лога: (номер строки с единицы, название)."""
    return tuple(
        (number, title)
        for number, line in enumerate(str(text or "").split("\n"), start=1)
        if (title := heading_title(line))
    )


def count_log_states(text: str) -> dict[str, int]:
    """Сколько в логе строк результата каждого вида — для счётчиков над текстом."""
    counts = {KIND_OK: 0, KIND_WARN: 0, KIND_FAIL: 0}
    for line in str(text or "").split("\n"):
        kind = log_line_kind(line)
        if kind in counts:
            counts[kind] += 1
    return counts


def state_color(kind: str, *, dark: bool) -> QColor:
    dark_color, light_color = _STATE_COLORS[kind]
    return QColor(dark_color if dark else light_color)


def _utf16_length(text: str) -> int:
    """Длина строки так, как её считает Qt: значок-эмодзи занимает две позиции."""
    return len(text.encode("utf-16-le")) // 2


def _expression(pattern: str) -> QRegularExpression:
    options = (
        QRegularExpression.PatternOption.CaseInsensitiveOption
        | QRegularExpression.PatternOption.UseUnicodePropertiesOption
    )
    return QRegularExpression(pattern, options)


class LogSyntaxHighlighter(BaseSyntaxHighlighter):
    """Подсветка лога: разделы, результат и то, о чём строка."""

    def _rebuild_formats(self) -> None:
        super()._rebuild_formats()
        theme = self.theme
        dark = theme.text.lightness() > 128

        def styled(color: QColor, *, bold: bool = False) -> QTextCharFormat:
            text_format = QTextCharFormat()
            text_format.setForeground(color)
            if bold:
                text_format.setFontWeight(QFont.Weight.Bold)
            return text_format

        self._heading_format = styled(QColor(theme.accent), bold=True)
        faint = QColor(theme.text)
        faint.setAlpha(85)
        self._rule_format = styled(faint)
        # Строка со значком целиком — спокойным тоном, сами слова результата — жирным.
        self._line_formats: dict[str, QTextCharFormat] = {}
        self._word_formats: dict[str, QTextCharFormat] = {}
        for kind in (KIND_OK, KIND_WARN, KIND_FAIL):
            color = state_color(kind, dark=dark)
            self._line_formats[kind] = styled(color)
            self._word_formats[kind] = styled(color, bold=True)

        self._log_rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        for pattern, role in _TOKEN_RULES:
            source, alpha, bold = _TOKEN_STYLES[role]
            color = QColor(theme.accent if source == "accent" else theme.text)
            color.setAlpha(alpha)
            self._log_rules.append((_expression(pattern), styled(color, bold=bold)))
        for pattern, kind in ((_OK_WORDS, KIND_OK), (_WARN_WORDS, KIND_WARN), (_FAIL_WORDS, KIND_FAIL)):
            self._log_rules.append((_expression(pattern), self._word_formats[kind]))

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        line = str(text or "")
        if not line:
            return
        kind = log_line_kind(line)
        length = _utf16_length(line)
        if kind == KIND_RULE:
            self.setFormat(0, length, self._rule_format)
            return
        if kind == KIND_HEADING:
            self.setFormat(0, length, self._heading_format)
            return
        if kind in self._line_formats:
            self.setFormat(0, length, self._line_formats[kind])
        for expression, text_format in self._log_rules:
            iterator = expression.globalMatch(line)
            while iterator.hasNext():
                match = iterator.next()
                if match.capturedLength() > 0:
                    self.setFormat(match.capturedStart(), match.capturedLength(), text_format)


__all__ = [
    "KIND_FAIL",
    "KIND_HEADING",
    "KIND_OK",
    "KIND_PLAIN",
    "KIND_RULE",
    "KIND_WARN",
    "LogSyntaxHighlighter",
    "count_log_states",
    "heading_title",
    "log_line_kind",
    "log_outline",
    "state_color",
]
