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


# Отказы winws2 из-за файлов списков (nfq2/hostlist.c, nfq2/ipset.c:
# RegisterHostlist/RegisterIpset). «cannot access» — файла нет (stat не прошёл);
# «failed to register» без него — с 31.10.2026 (zapret2 abbe480) почти всегда
# повтор: один и тот же файл подключён в одном профиле дважды одного типа
# (--hostlist и --hostlist-auto считаются одним типом).
_LIST_ACCESS_RE = re.compile(r"cannot access (hostlist|ipset) file '([^']*)'", re.IGNORECASE)
_LIST_REGISTER_RE = re.compile(r"failed to register (hostlist|ipset) '([^']*)'", re.IGNORECASE)


def explain_list_registration_failure(output: str) -> str:
    """Понятная причина отказа winws2 из-за файла списка; пусто — не этот случай."""
    text = str(output or "")
    access = _LIST_ACCESS_RE.search(text)
    if access:
        return (
            f"не найден файл списка {access.group(2)}. "
            "Проверьте, что файл есть в папке lists"
        )
    register = _LIST_REGISTER_RE.search(text)
    if register:
        option = "--hostlist/--hostlist-auto/--hostlist-exclude" if register.group(1).lower() == "hostlist" else "--ipset/--ipset-exclude"
        return (
            f"файл списка {register.group(2)} подключён в одном профиле дважды. "
            f"Новые версии winws2 так не запускаются: оставьте в профиле одну строку {option} с этим файлом "
            "(редактор пресета подчеркнёт повтор)"
        )
    return ""
