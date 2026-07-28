# winws_runtime/health/winws_output.py
"""Разбор стартового вывода winws/winws2 — единый источник правды.

winws2 при каждом запуске первой строкой печатает служебный баннер вида
``github version v1.0.3 (b78b52c4...) lua_compat_ver 6``. Из-за него наивная
проверка "вывод пустой" никогда не срабатывает для winws2: процесс, убитый
антивирусом до единого сообщения об ошибке, выглядит как процесс с выводом, а
пользователю в качестве "причины" показывалась строка версии.

Поэтому здесь баннер отделён от диагностики:
- :func:`has_diagnostic_output` — сказал ли процесс хоть что-то по существу;
- :func:`relevant_error_line` — какая строка достойна показа пользователю.

Оба ответа обязаны быть одинаковыми во всех точках диагностики (раннеры,
post-mortem, WinDivert-диагноз), поэтому дублировать эту логику нельзя.
"""

from __future__ import annotations

import re
from typing import Iterator, Literal


# Служебные строки, которые winws/winws2 печатает всегда, независимо от исхода.
_BANNER_PATTERNS = (
    # winws2: "github version v1.0.3 (b78b52c4...) lua_compat_ver 6"
    re.compile(r"^(?:github\s+)?version\s+\S+", re.IGNORECASE),
    # winws1/nfqws: "winws v70.3", "winws2.exe v1.0.3"
    re.compile(r"^(?:winws|nfqws)\d*(?:\.exe)?\s+v?\d", re.IGNORECASE),
)

# Маркеры настоящей диагностики. Строка с ними никогда не считается служебной,
# даже если формально похожа на баннер.
_WINDIVERT_MARKERS = ("windivert:", "error opening filter")
_ERROR_MARKERS = ("error", "ошибка")

FallbackLine = Literal["first", "last", "none"]


def is_banner_line(line: str) -> bool:
    """True для служебной строки версии, не несущей диагностики."""
    text = str(line or "").strip()
    if not text:
        return False
    lower = text.lower()
    if any(marker in lower for marker in _WINDIVERT_MARKERS):
        return False
    if any(marker in lower for marker in _ERROR_MARKERS):
        return False
    return any(pattern.match(text) for pattern in _BANNER_PATTERNS)


def iter_diagnostic_lines(output: str) -> Iterator[str]:
    """Непустые строки вывода без служебного баннера версии."""
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if line and not is_banner_line(line):
            yield line


def diagnostic_lines(output: str) -> list[str]:
    return list(iter_diagnostic_lines(output))


def has_diagnostic_output(output: str) -> bool:
    """True, если процесс успел сказать что-то кроме строки версии.

    Ровно этот предикат заменяет ``not output.strip()`` во всех местах, где
    важно "процесс умер молча": для winws2 буквально пустого вывода не бывает.
    """
    return any(True for _ in iter_diagnostic_lines(output))


def relevant_error_line(output: str, *, fallback: FallbackLine = "first") -> str:
    """Самая содержательная строка вывода для показа пользователю.

    Порядок приоритетов повторяет прежние (продублированные) реализации:
    сначала WinDivert-строка с конца вывода, затем любая строка с признаком
    ошибки с конца, затем — ``fallback``-строка среди диагностических.
    ``fallback="none"`` отключает последний шаг: вызывающий код сам решит, что
    показать, когда содержательного текста нет.
    """
    lines = diagnostic_lines(output)
    if not lines:
        return ""

    for line in reversed(lines):
        lower = line.lower()
        if any(marker in lower for marker in _WINDIVERT_MARKERS):
            return line
    for line in reversed(lines):
        lower = line.lower()
        if any(marker in lower for marker in _ERROR_MARKERS):
            return line

    if fallback == "first":
        return lines[0]
    if fallback == "last":
        return lines[-1]
    return ""
