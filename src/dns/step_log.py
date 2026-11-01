"""Журнал шагов применения DNS: что делалось и сколько это заняло.

Применение DNS проходит несколько шагов, и каждый может ждать Windows или сеть:
проверка сервера, запуск службы шифрованного DNS, запись адаптеров. Когда
плитка долго крутит «применяю…», по этим строкам видно, какой шаг тянет время.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from log.log import log


@contextmanager
def timed_step(what: str):
    """Пишет в журнал «DNS: <what> — N мс» после шага, даже если шаг упал."""
    started = time.perf_counter()
    failed = True
    try:
        yield
        failed = False
    finally:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log(f"DNS: {what} — {elapsed_ms} мс{' (с ошибкой)' if failed else ''}", "INFO")


__all__ = ["timed_step"]
