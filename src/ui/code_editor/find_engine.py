"""Поиск и замена в тексте: чистая логика без Qt.

Вынесено отдельно от виджета, чтобы поведение поиска (регистр, целое слово,
регулярные выражения, wrap-around, лимит совпадений) тестировалось без
создания окон.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Подсветка всех совпадений строится через ExtraSelections, поэтому число
# совпадений ограничено: `.` по regex на большом файле иначе подвесит UI.
MAX_MATCHES = 5000


@dataclass(frozen=True, slots=True)
class SearchOptions:
    case_sensitive: bool = False
    whole_word: bool = False
    regex: bool = False


@dataclass(frozen=True, slots=True)
class SearchMatch:
    start: int
    end: int

    @property
    def length(self) -> int:
        return max(0, self.end - self.start)


@dataclass(frozen=True, slots=True)
class SearchResult:
    matches: tuple[SearchMatch, ...] = ()
    truncated: bool = False
    error: str = ""

    @property
    def count(self) -> int:
        return len(self.matches)

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass(frozen=True, slots=True)
class ReplaceResult:
    text: str = ""
    count: int = 0
    error: str = ""
    changed: bool = field(default=False)

    @property
    def ok(self) -> bool:
        return not self.error


_WORD_CHAR = re.compile(r"\w", re.UNICODE)


def _word_boundary_prefix(pattern_text: str, *, regex: bool) -> str:
    if regex:
        return r"\b"
    if pattern_text and _WORD_CHAR.match(pattern_text[0]):
        return r"\b"
    return ""


def _word_boundary_suffix(pattern_text: str, *, regex: bool) -> str:
    if regex:
        return r"\b"
    if pattern_text and _WORD_CHAR.match(pattern_text[-1]):
        return r"\b"
    return ""


def compile_query(query: str, options: SearchOptions | None = None):
    """Возвращает (скомпилированный паттерн | None, текст ошибки).

    Пустой запрос — не ошибка: это просто «искать нечего».
    """
    opts = options or SearchOptions()
    text = str(query or "")
    if not text:
        return None, ""

    body = text if opts.regex else re.escape(text)
    if opts.whole_word:
        body = (
            _word_boundary_prefix(text, regex=opts.regex)
            + f"(?:{body})"
            + _word_boundary_suffix(text, regex=opts.regex)
        )

    flags = re.MULTILINE | re.UNICODE
    if not opts.case_sensitive:
        flags |= re.IGNORECASE
    try:
        return re.compile(body, flags), ""
    except re.error as exc:
        return None, str(exc) or "Некорректное регулярное выражение"


def find_matches(
    text: str,
    query: str,
    options: SearchOptions | None = None,
    *,
    limit: int = MAX_MATCHES,
) -> SearchResult:
    """Все совпадения запроса в тексте.

    Совпадения нулевой длины (`a*`, `^`) пропускаются: подсвечивать и
    перебирать их нечего, а без пропуска regex-обход зацикливается.
    """
    pattern, error = compile_query(query, options)
    if error:
        return SearchResult(error=error)
    if pattern is None:
        return SearchResult()

    source = str(text or "")
    matches: list[SearchMatch] = []
    truncated = False
    position = 0
    length = len(source)
    max_matches = max(0, int(limit))
    while position <= length:
        found = pattern.search(source, position)
        if found is None:
            break
        start, end = found.span()
        if end == start:
            position = start + 1
            continue
        if max_matches and len(matches) >= max_matches:
            truncated = True
            break
        matches.append(SearchMatch(start, end))
        position = end

    return SearchResult(tuple(matches), truncated=truncated)


def locate_match(
    matches,
    position: int,
    *,
    reverse: bool = False,
    include_position: bool = False,
) -> int | None:
    """Индекс следующего (или предыдущего) совпадения относительно позиции.

    При отсутствии совпадения по направлению поиск заворачивается на другой
    конец документа — как в Notepad++.
    """
    items = tuple(matches or ())
    if not items:
        return None
    anchor = int(position)

    if reverse:
        for index in range(len(items) - 1, -1, -1):
            match = items[index]
            limit = match.end if not include_position else match.start + 1
            if limit <= anchor:
                return index
        return len(items) - 1

    for index, match in enumerate(items):
        start = match.start
        if start > anchor or (include_position and start >= anchor):
            return index
    return 0


def match_index_at(matches, position: int) -> int | None:
    """Индекс совпадения, внутри которого стоит позиция (или None)."""
    for index, match in enumerate(tuple(matches or ())):
        if match.start <= position <= match.end:
            return index
    return None


def build_counter_text(result: SearchResult, index: int | None, *, query: str = "") -> str:
    """Текст счётчика совпадений для панели поиска."""
    if result.error:
        return "Ошибка"
    if not str(query or ""):
        return ""
    if not result.count:
        return "Нет совпадений"
    total = f"{result.count}+" if result.truncated else str(result.count)
    if index is None:
        return f"{total} совп."
    return f"{index + 1} / {total}"


def replace_all(
    text: str,
    query: str,
    replacement: str,
    options: SearchOptions | None = None,
    *,
    limit: int = MAX_MATCHES,
) -> ReplaceResult:
    """Заменяет все совпадения; regex-режим поддерживает обратные ссылки."""
    opts = options or SearchOptions()
    pattern, error = compile_query(query, opts)
    if error:
        return ReplaceResult(text=str(text or ""), error=error)
    if pattern is None:
        return ReplaceResult(text=str(text or ""))

    source = str(text or "")
    target = str(replacement or "")
    result = find_matches(source, query, opts, limit=limit)
    if result.error:
        return ReplaceResult(text=source, error=result.error)
    if not result.count:
        return ReplaceResult(text=source)

    pieces: list[str] = []
    cursor = 0
    count = 0
    for match in result.matches:
        pieces.append(source[cursor:match.start])
        try:
            pieces.append(expand_replacement(pattern, source, match, target, opts))
        except re.error as exc:
            return ReplaceResult(text=source, error=str(exc) or "Некорректная замена")
        cursor = match.end
        count += 1
    pieces.append(source[cursor:])

    new_text = "".join(pieces)
    return ReplaceResult(text=new_text, count=count, changed=new_text != source)


def expand_replacement(
    pattern,
    text: str,
    match: SearchMatch,
    replacement: str,
    options: SearchOptions | None = None,
) -> str:
    """Текст замены для конкретного совпадения (с группами в regex-режиме)."""
    opts = options or SearchOptions()
    target = str(replacement or "")
    if not opts.regex or pattern is None:
        return target
    found = pattern.match(str(text or ""), match.start, match.end)
    if found is None:
        return target
    return found.expand(target)


def resolve_single_replacement(
    text: str,
    query: str,
    replacement: str,
    match: SearchMatch,
    options: SearchOptions | None = None,
) -> ReplaceResult:
    """Замена одного совпадения — без перестроения всего документа."""
    opts = options or SearchOptions()
    pattern, error = compile_query(query, opts)
    if error:
        return ReplaceResult(text=str(text or ""), error=error)
    if pattern is None:
        return ReplaceResult(text=str(text or ""))
    try:
        expanded = expand_replacement(pattern, text, match, replacement, opts)
    except re.error as exc:
        return ReplaceResult(text=str(text or ""), error=str(exc) or "Некорректная замена")
    source = str(text or "")
    new_text = source[:match.start] + expanded + source[match.end:]
    return ReplaceResult(text=new_text, count=1, changed=new_text != source)
