"""Построчные операции редактора: чистая логика без Qt.

Каждая операция описывается планом «заменить строки [start_line..end_line]
на набор строк» — виджет применяет план одной undo-транзакцией, поэтому
Ctrl+Z откатывает операцию целиком.
"""

from __future__ import annotations

from dataclasses import dataclass

COMMENT_PREFIX = "#"
INDENT = "    "


@dataclass(frozen=True, slots=True)
class LineEditPlan:
    start_line: int
    end_line: int
    lines: tuple[str, ...]
    anchor_line: int
    cursor_line: int
    anchor_column_delta: int = 0
    cursor_column_delta: int = 0


def _normalize_range(lines, first_line: int, last_line: int):
    total = len(lines)
    if total <= 0:
        return None
    first = max(0, min(int(first_line), total - 1))
    last = max(0, min(int(last_line), total - 1))
    if first > last:
        first, last = last, first
    return first, last


def duplicate_lines(lines, first_line: int, last_line: int) -> LineEditPlan | None:
    """Дублирует выделенные строки ниже; курсор переходит на копию."""
    bounds = _normalize_range(lines, first_line, last_line)
    if bounds is None:
        return None
    first, last = bounds
    block = tuple(str(item) for item in lines[first:last + 1])
    span = last - first + 1
    return LineEditPlan(
        start_line=first,
        end_line=last,
        lines=block + block,
        anchor_line=first + span,
        cursor_line=last + span,
    )


def delete_lines(lines, first_line: int, last_line: int) -> LineEditPlan | None:
    """Удаляет выделенные строки целиком."""
    bounds = _normalize_range(lines, first_line, last_line)
    if bounds is None:
        return None
    first, last = bounds
    target_line = min(first, max(0, len(lines) - (last - first + 1) - 1))
    return LineEditPlan(
        start_line=first,
        end_line=last,
        lines=(),
        anchor_line=target_line,
        cursor_line=target_line,
    )


def move_lines(lines, first_line: int, last_line: int, *, delta: int) -> LineEditPlan | None:
    """Переносит блок строк на строку вверх (delta=-1) или вниз (delta=+1)."""
    bounds = _normalize_range(lines, first_line, last_line)
    if bounds is None:
        return None
    first, last = bounds
    step = int(delta)
    if step == 0:
        return None
    if step < 0:
        if first <= 0:
            return None
        neighbour = str(lines[first - 1])
        block = tuple(str(item) for item in lines[first:last + 1])
        return LineEditPlan(
            start_line=first - 1,
            end_line=last,
            lines=block + (neighbour,),
            anchor_line=first - 1,
            cursor_line=last - 1,
        )
    if last >= len(lines) - 1:
        return None
    neighbour = str(lines[last + 1])
    block = tuple(str(item) for item in lines[first:last + 1])
    return LineEditPlan(
        start_line=first,
        end_line=last + 1,
        lines=(neighbour,) + block,
        anchor_line=first + 1,
        cursor_line=last + 1,
    )


def _comment_state(block) -> bool:
    meaningful = [line for line in block if line.strip()]
    if not meaningful:
        return False
    return all(line.lstrip().startswith(COMMENT_PREFIX) for line in meaningful)


def _uncomment_line(line: str) -> str:
    stripped = line.lstrip()
    if not stripped.startswith(COMMENT_PREFIX):
        return line
    indent = line[:len(line) - len(stripped)]
    rest = stripped[len(COMMENT_PREFIX):]
    if rest.startswith(" "):
        rest = rest[1:]
    return indent + rest


def toggle_comment(lines, first_line: int, last_line: int) -> LineEditPlan | None:
    """Комментирует/раскомментирует строки префиксом `#`.

    Раскомментирование включается, только если закомментированы ВСЕ непустые
    строки блока — иначе смешанный блок комментируется целиком.
    """
    bounds = _normalize_range(lines, first_line, last_line)
    if bounds is None:
        return None
    first, last = bounds
    block = [str(item) for item in lines[first:last + 1]]
    if not any(line.strip() for line in block):
        return None

    if _comment_state(block):
        updated = tuple(_uncomment_line(line) if line.strip() else line for line in block)
        delta = 0
        for original, changed in zip(block, updated):
            if original != changed:
                delta = len(changed) - len(original)
                break
    else:
        indents = [
            len(line) - len(line.lstrip())
            for line in block
            if line.strip()
        ]
        column = min(indents) if indents else 0
        updated_lines = []
        for line in block:
            if not line.strip():
                updated_lines.append(line)
                continue
            updated_lines.append(f"{line[:column]}{COMMENT_PREFIX} {line[column:]}")
        updated = tuple(updated_lines)
        delta = len(COMMENT_PREFIX) + 1

    return LineEditPlan(
        start_line=first,
        end_line=last,
        lines=updated,
        anchor_line=first,
        cursor_line=last,
        anchor_column_delta=delta,
        cursor_column_delta=delta,
    )


def indent_lines(lines, first_line: int, last_line: int, *, indent: str = INDENT) -> LineEditPlan | None:
    """Добавляет отступ к каждой непустой строке блока."""
    bounds = _normalize_range(lines, first_line, last_line)
    if bounds is None:
        return None
    first, last = bounds
    prefix = str(indent or INDENT)
    block = [str(item) for item in lines[first:last + 1]]
    updated = tuple(f"{prefix}{line}" if line.strip() else line for line in block)
    if updated == tuple(block):
        return None
    return LineEditPlan(
        start_line=first,
        end_line=last,
        lines=updated,
        anchor_line=first,
        cursor_line=last,
        anchor_column_delta=len(prefix),
        cursor_column_delta=len(prefix),
    )


def _unindent_line(line: str, indent: str) -> tuple[str, int]:
    if line.startswith(indent):
        return line[len(indent):], len(indent)
    if line.startswith("\t"):
        return line[1:], 1
    stripped = line.lstrip(" ")
    removed = len(line) - len(stripped)
    if removed <= 0:
        return line, 0
    removed = min(removed, len(indent))
    return line[removed:], removed


def unindent_lines(lines, first_line: int, last_line: int, *, indent: str = INDENT) -> LineEditPlan | None:
    """Снимает один уровень отступа с блока строк."""
    bounds = _normalize_range(lines, first_line, last_line)
    if bounds is None:
        return None
    first, last = bounds
    prefix = str(indent or INDENT)
    block = [str(item) for item in lines[first:last + 1]]
    updated: list[str] = []
    anchor_delta = 0
    cursor_delta = 0
    for index, line in enumerate(block):
        changed, removed = _unindent_line(line, prefix)
        updated.append(changed)
        if index == 0:
            anchor_delta = -removed
        if index == len(block) - 1:
            cursor_delta = -removed
    if tuple(updated) == tuple(block):
        return None
    return LineEditPlan(
        start_line=first,
        end_line=last,
        lines=tuple(updated),
        anchor_line=first,
        cursor_line=last,
        anchor_column_delta=anchor_delta,
        cursor_column_delta=cursor_delta,
    )
