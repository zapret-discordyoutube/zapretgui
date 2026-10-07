"""Последний шаг завершения процесса: после выхода из цикла событий Qt.

К этому моменту окно уже закрыто, а ``ApplicationLifecycle`` сохранил
настройки, остановил DPI (если просили), убрал значок из трея и попросил
фоновые потоки остановиться.

Почему программа падала при выходе
----------------------------------
При обычном завершении интерпретатора PyQt запускает свою уборку (обработчик
``atexit`` с именем ``_qtcore_cleanup``): идёт по таблице всех объектов Qt,
которыми владеет Python, и удаляет их подряд. Удаление окна рассылает события,
и они приходят в обработчики программы на Python (фильтры событий виджетов,
слоты). Обработчик заводит новые объекты, таблица переполняется и переезжает
в другую память, а уборка продолжает читать старую, уже освобождённую.
Пользователь видел системное окно «Ошибка приложения … память не может быть
read», в журнале падений оставалось «access violation» с ``<no Python frame>``.
Воспроизводится на двадцати строках чистого PyQt — см.
``tests/test_process_exit.py``.

Удалять объекты самим в «правильном» порядке тоже нельзя: во время удаления
PyQt доставляет отложенные сигналы уже мёртвым получателям и падает внутри
себя. У программы сотни обработчиков и больше сотни классов потоков — порядка,
безопасного для всех, не существует.

Осознанное решение
------------------
Объекты Qt при выходе не разрушаются вообще: память и окна процесса заберёт
Windows. Но всё, что обычное завершение Python делает полезного, выполняется
здесь явно и в том же порядке:

1. потоки Qt получают просьбу остановиться и время на это;
2. дожидаются обычные потоки Python (``threading._shutdown``);
3. закрывается база настроек (записи из журнала базы попадают в файл);
4. уборка PyQt снимается с регистрации, после чего выполняются все
   обработчики ``atexit``: журнал программы дописывается и закрывается,
   закрываются журналы запусков winws, освобождаются служба BFE и мьютекс
   единственного экземпляра, в журнале падений появляется «Session ended»;
5. ``os._exit`` с кодом выхода цикла событий.

У объектов Qt в программе нет деструкторов с полезной работой (временных
файлов, файлов-замков, общей памяти Qt программа не использует), так что
пропуск их разрушения ничего не теряет.
"""

from __future__ import annotations

import atexit
import gc
import os
import sys
import threading
import time
import types
from typing import NoReturn

from PyQt6 import sip
from PyQt6.QtCore import QThread

from log.log import log


# Общее время ожидания всех потоков Qt. Исправный поток заканчивается за
# миллисекунды; дольше ждать нельзя — пользователь уже нажал «Выход».
THREAD_DRAIN_TIMEOUT_MS = 2000

# Имя обработчика выхода PyQt (qpy/QtCore/qpycore_init.cpp). Если PyQt его
# переименует, тест test_pyqt_exit_cleanup_is_found_and_unregistered упадёт.
PYQT_EXIT_CLEANUP_NAME = "_qtcore_cleanup"


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
    """Просит потоки остановиться и ждёт их. Возвращает тех, кто не успел."""
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


def describe_thread(thread: QThread) -> str:
    cls = type(thread)
    name = thread.objectName()
    label = f"{cls.__module__}.{cls.__qualname__}"
    return f"{label} «{name}»" if name else label


def unregister_pyqt_exit_cleanup() -> bool:
    """Снимает уборку PyQt с регистрации в ``atexit``. False — не нашлась."""
    found = False
    for candidate in gc.get_objects():
        if (
            isinstance(candidate, types.BuiltinFunctionType)
            and candidate.__name__ == PYQT_EXIT_CLEANUP_NAME
        ):
            atexit.unregister(candidate)
            found = True
    return found


def _close_settings_database() -> None:
    from settings.store import close_settings_database

    close_settings_database()


def _wait_for_python_threads() -> None:
    # То же, с чего начинает обычное завершение интерпретатор: дождаться
    # потоков Python, не помеченных фоновыми, и рабочих потоков пулов.
    threading._shutdown()


def run_exit_steps() -> None:
    """Полезная часть обычного завершения Python, выполненная явно."""
    for title, step in (
        ("ожидание потоков Python", _wait_for_python_threads),
        ("закрытие базы настроек", _close_settings_database),
    ):
        try:
            step()
        except Exception as exc:
            log(f"Шаг выхода «{title}» не выполнен: {exc}", "WARNING")

    if not unregister_pyqt_exit_cleanup():
        log("Уборка PyQt при выходе не найдена: возможен сбой при завершении", "WARNING")

    # Последним: здесь закрывается журнал программы, писать в него дальше нельзя.
    try:
        atexit._run_exitfuncs()
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass


def finish_process(exit_code: int, *, timeout_ms: int = THREAD_DRAIN_TIMEOUT_MS) -> NoReturn:
    """Завершает процесс после ``app.exec()`` (см. описание модуля)."""
    try:
        unfinished = stop_running_qt_threads(timeout_ms)
    except Exception as exc:
        log(f"Не удалось дождаться фоновых потоков при выходе: {exc}", "WARNING")
        unfinished = []
    if unfinished:
        names = ", ".join(sorted(describe_thread(thread) for thread in unfinished))
        log(f"При выходе не остановились фоновые потоки: {names}", "WARNING")

    run_exit_steps()
    os._exit(int(exit_code))


__all__ = [
    "PYQT_EXIT_CLEANUP_NAME",
    "THREAD_DRAIN_TIMEOUT_MS",
    "describe_thread",
    "finish_process",
    "run_exit_steps",
    "running_qt_threads",
    "stop_running_qt_threads",
    "unregister_pyqt_exit_cleanup",
]
