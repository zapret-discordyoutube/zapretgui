"""Последний шаг завершения процесса: после выхода из цикла событий Qt.

К этому моменту окно уже закрыто, а ``ApplicationLifecycle`` сохранил
настройки, остановил DPI (если просили), убрал значок из трея и попросил
фоновые потоки остановиться.

Почему программа падала при выходе
----------------------------------
При обычном завершении интерпретатора PyQt запускает свою уборку (её
обработчик ``atexit``): идёт по таблице всех объектов Qt, которыми владеет
Python, и удаляет их подряд. Удаление окна рассылает события, и они приходят
в обработчики программы на Python (фильтры событий виджетов, слоты).
Обработчик заводит новые объекты, таблица переполняется и переезжает в другую
память, а уборка продолжает читать старую, уже освобождённую. Пользователь
видел системное окно «Ошибка приложения … память не может быть read», в
журнале падений оставалось «access violation» с ``<no Python frame>``.
Воспроизводится на двадцати строках чистого PyQt — см.
``tests/test_process_exit.py``.

Удалять объекты самим в «правильном» порядке тоже нельзя: во время удаления
PyQt доставляет отложенные сигналы уже мёртвым получателям и падает внутри
себя. У программы сотни обработчиков и больше сотни классов потоков — порядка,
безопасного для всех, не существует.

Как выход устроен
-----------------
Объекты Qt при выходе не разрушаются вообще, и завершение интерпретатора не
запускается: память и окна процесса заберёт Windows. Всё, что программа
обязана сделать перед концом процесса, записано явными шагами выхода
(``utils/exit_steps.py``) и выполняется здесь:

1. потоки Qt получают просьбу остановиться и время на это;
2. столько же времени получают обычные потоки Python, не помеченные фоновыми;
3. шаги выхода: закрытие базы настроек и журналов запусков winws,
   освобождение мьютекса единственного экземпляра, «Session ended» в журнале
   падений, закрытие журнала программы;
4. ``os._exit`` с кодом выхода цикла событий.

Ожидание везде ограничено по времени: зависший в сети поток не должен
держать выход (см. ``utils/net_resolve.py``).

У объектов Qt в программе нет деструкторов с полезной работой (временных
файлов, файлов-замков, общей памяти Qt программа не использует), так что
пропуск их разрушения ничего не теряет. Новое действие «при выходе» —
это новый шаг выхода, а не деструктор и не ``atexit``.
"""

from __future__ import annotations

import gc
import os
import sys
import threading
import time
from typing import NoReturn

from PyQt6 import sip
from PyQt6.QtCore import QThread

from log.log import log
from utils.exit_steps import run_exit_steps


# Время на остановку потоков. Исправный поток заканчивается за миллисекунды;
# дольше ждать нельзя — пользователь уже нажал «Выход».
THREAD_DRAIN_TIMEOUT_MS = 2000


def running_qt_threads() -> list[QThread]:
    """Потоки Qt, созданные программой и ещё работающие.

    Главный поток и потоки, которые Qt сам завёл для чужих потоков
    (``QThread.currentThread()`` из ``threading.Thread``), сюда не попадают:
    их объекты созданы не из Python, и ждать их бессмысленно.
    """
    threads: list[QThread] = []
    for candidate in gc.get_objects():
        if not isinstance(candidate, QThread):
            continue
        try:
            if sip.ispycreated(candidate) and candidate.isRunning():
                threads.append(candidate)
        except RuntimeError:
            # Объект Qt уже удалён, осталась только оболочка Python.
            continue
    return threads


def stop_running_qt_threads(timeout_ms: int = THREAD_DRAIN_TIMEOUT_MS) -> list[QThread]:
    """Просит потоки Qt остановиться и ждёт их. Возвращает тех, кто не успел."""
    threads = running_qt_threads()
    if not threads:
        return []

    for thread in threads:
        thread.requestInterruption()
        thread.quit()

    deadline = time.monotonic() + max(0, int(timeout_ms)) / 1000.0
    unfinished: list[QThread] = []
    for thread in threads:
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        if not thread.wait(remaining_ms):
            unfinished.append(thread)
    return unfinished


def wait_for_python_threads(timeout_ms: int = THREAD_DRAIN_TIMEOUT_MS) -> list[threading.Thread]:
    """Ждёт потоки Python, не помеченные фоновыми. Возвращает тех, кто не успел.

    Обычное завершение интерпретатора ждёт такие потоки без срока; здесь срок
    есть, иначе один зависший поток пула не дал бы программе закрыться.
    """
    current = threading.current_thread()
    threads = [
        thread
        for thread in threading.enumerate()
        if thread is not current and not thread.daemon
    ]
    deadline = time.monotonic() + max(0, int(timeout_ms)) / 1000.0
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return [thread for thread in threads if thread.is_alive()]


def describe_thread(thread: QThread) -> str:
    cls = type(thread)
    name = thread.objectName()
    label = f"{cls.__module__}.{cls.__qualname__}"
    return f"{label} «{name}»" if name else label


def finish_process(exit_code: int, *, timeout_ms: int = THREAD_DRAIN_TIMEOUT_MS) -> NoReturn:
    """Завершает процесс после ``app.exec()`` (см. описание модуля)."""
    try:
        names = [describe_thread(thread) for thread in stop_running_qt_threads(timeout_ms)]
        names += [thread.name for thread in wait_for_python_threads(timeout_ms)]
    except Exception as exc:
        log(f"Не удалось дождаться фоновых потоков при выходе: {exc}", "WARNING")
        names = []
    if names:
        log(f"При выходе не остановились фоновые потоки: {', '.join(sorted(names))}", "WARNING")

    # Последним из шагов закрывается журнал программы, писать в него дальше нельзя.
    run_exit_steps()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(int(exit_code))


__all__ = [
    "THREAD_DRAIN_TIMEOUT_MS",
    "describe_thread",
    "finish_process",
    "running_qt_threads",
    "stop_running_qt_threads",
    "wait_for_python_threads",
]
