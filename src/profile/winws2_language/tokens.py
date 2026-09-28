"""Аргументы winws2 с их местом в тексте пресета.

Запуск превращает текст пресета в аргументы функцией
``winws_runtime.runners.preset_runner_support.launch_args_from_preset_text``:
пустые строки и строки с ``#`` пропускаются, строка, начинающаяся с ``--``,
делится по пробелам перед следующим ``--``, остальные строки идут целиком.
Каждый аргумент затем отдельно экранируется в ``@config`` — winws2 видит
ровно эти аргументы. Здесь то же деление, но с номером строки и колонками,
чтобы редактор мог показать место ошибки. Совпадение с запуском проверяет
``tests/test_winws2_language_tokens.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

_INLINE_ARG_SPLIT_RE = re.compile(r"(?<=\S)\s+(?=--)")


@dataclass(frozen=True, slots=True)
class Token:
    text: str
    line: int
    start: int
    end: int


def document_lines(text: str) -> list[str]:
    """Строки документа так, как их нумерует редактор (по ``\\n``)."""
    return str(text or "").split("\n")


def line_tokens(raw_line: str, line_index: int) -> list[Token]:
    line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return []
    offset = len(line) - len(line.lstrip())
    if not stripped.startswith("--"):
        return [Token(stripped, line_index, offset, offset + len(stripped))]
    tokens: list[Token] = []
    cursor = 0
    for separator in _INLINE_ARG_SPLIT_RE.finditer(stripped):
        part = stripped[cursor:separator.start()]
        if part.strip():
            tokens.append(Token(part.strip(), line_index, offset + cursor, offset + separator.start()))
        cursor = separator.end()
    tail = stripped[cursor:]
    if tail.strip():
        tokens.append(Token(tail.strip(), line_index, offset + cursor, offset + len(stripped)))
    return tokens


def tokenize(text: str) -> list[Token]:
    lines = document_lines(text)
    tokens: list[Token] = []
    for index, raw in enumerate(lines):
        if index == 0 and raw.startswith("\ufeff"):
            body = line_tokens(raw[1:], index)
            tokens.extend(Token(t.text, t.line, t.start + 1, t.end + 1) for t in body)
            continue
        tokens.extend(line_tokens(raw, index))
    return tokens


__all__ = ["Token", "document_lines", "line_tokens", "tokenize"]
