from __future__ import annotations

"""Долгие дела пользователя, которые нельзя обрывать перезапуском программы.

Проверка сети (BlockCheck) и подбор стратегии идут минутами и на это время
сами управляют Zapret. Обновление закрывает программу — посреди такого дела
человек потерял бы результат. Дело отмечает здесь, что оно идёт, а
автообновление ждёт, пока список опустеет.

Модуль не знает ни про Qt, ни про то, кто его спрашивает: отметку ставят из
любого потока.
"""

import itertools
import threading
from collections.abc import Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_ids = itertools.count(1)
_active: dict[int, str] = {}


def begin_long_task(name: str) -> int:
    """Отмечает, что началось долгое дело ``name``; возвращает номер отметки."""
    token = next(_ids)
    with _lock:
        _active[token] = str(name)
    return token


def end_long_task(token: int) -> None:
    """Снимает отметку; повторный вызов безвреден."""
    with _lock:
        _active.pop(token, None)


@contextmanager
def long_task(name: str) -> Iterator[None]:
    """То же одним блоком ``with``: отметка снимается при выходе из него."""
    token = begin_long_task(name)
    try:
        yield
    finally:
        end_long_task(token)


def active_long_tasks() -> tuple[str, ...]:
    """Названия дел, которые идут прямо сейчас."""
    with _lock:
        return tuple(_active.values())


__all__ = ["active_long_tasks", "begin_long_task", "end_long_task", "long_task"]
