"""Шаги выхода: всё, что программа обязана сделать перед концом процесса.

Единственное место, где программа записывается на «выполнить при выходе».
Прямой ``atexit.register`` в остальном коде запрещён архитектурной проверкой:
после работы окна процесс завершает ``main/process_exit.py`` через
``os._exit``, а тот обработчики ``atexit`` не запускает. Запускать их все
вручную тоже нельзя — среди них уборка PyQt, из-за которой программа падала
при выходе. Поэтому свои шаги программа держит в собственном списке.

Шаги выполняются в порядке, обратном регистрации, каждый один раз. Журнал
программы регистрируется первым и потому закрывается последним: остальные шаги
ещё могут в него писать.

При выходе без окна (``--version``, второй экземпляр, перезапуск с правами
администратора) процесс завершается обычным ``sys.exit``; тогда тот же список
выполняет один обработчик ``atexit``, зарегистрированный здесь.

Модуль не импортирует Qt и модули программы: его подключает журнал, который
загружается раньше всего остального.
"""

from __future__ import annotations

import atexit
import sys
import threading
import traceback
from collections.abc import Callable


_steps: list[tuple[str, Callable[[], None]]] = []
_lock = threading.Lock()


def register_exit_step(name: str, callback: Callable[[], None]) -> None:
    """Добавляет шаг выхода. ``name`` — что делает шаг, для сообщения об ошибке."""
    with _lock:
        _steps.append((str(name), callback))


def run_exit_steps() -> None:
    """Выполняет все шаги выхода. Повторный вызов ничего не делает."""
    while True:
        with _lock:
            if not _steps:
                return
            name, callback = _steps.pop()
        try:
            callback()
        except Exception:
            # Журнал программы к этому моменту может быть уже закрыт.
            try:
                print(f"Шаг выхода «{name}» не выполнен:", file=sys.__stderr__)
                traceback.print_exc(file=sys.__stderr__)
            except Exception:
                pass


atexit.register(run_exit_steps)


__all__ = ["register_exit_step", "run_exit_steps"]
