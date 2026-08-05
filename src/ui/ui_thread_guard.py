"""Контракт потоков: тяжёлая работа не выполняется в GUI-потоке.

Фоновый воркер, который по ошибке исполняется в потоке интерфейса, не падает и
ничего не ломает явно — окно просто перестаёт отвечать, а Windows рисует
«программа не отвечает». Такую ошибку невозможно заметить по коду: она зависит
от того, как связывание сигнала со слотом отработало в конкретной сборке.

Здесь GUI-поток помечается один раз при старте, а фоновые точки входа
проверяют себя и громко пишут в лог, если оказались не в своём потоке.
"""

from __future__ import annotations

from collections.abc import Callable
import threading

_gui_thread_id: int | None = None
_reported_contexts: set[str] = set()
_lock = threading.Lock()


def mark_gui_thread() -> None:
    """Запоминает текущий поток как GUI-поток. Вызывается один раз при старте."""
    global _gui_thread_id
    _gui_thread_id = threading.get_ident()


def gui_thread_id() -> int | None:
    return _gui_thread_id


def is_gui_thread() -> bool:
    """True, если код выполняется в потоке интерфейса (или поток ещё не помечен)."""
    if _gui_thread_id is None:
        return False
    return threading.get_ident() == _gui_thread_id


def reset_gui_thread_marker() -> None:
    """Сбрасывает состояние. Нужно тестам, живому коду — нет."""
    global _gui_thread_id
    _gui_thread_id = None
    with _lock:
        _reported_contexts.clear()


def ensure_background_thread(context: str) -> bool:
    """Проверяет, что фоновая работа идёт вне GUI-потока.

    Возвращает True, если контракт соблюдён. Нарушение логируется как ERROR
    один раз на контекст: повторные срабатывания того же воркера не должны
    заваливать лог.
    """
    if not is_gui_thread():
        return True

    key = str(context or "").strip() or "<unknown>"
    with _lock:
        already_reported = key in _reported_contexts
        _reported_contexts.add(key)
    if already_reported:
        return False

    try:
        from log.log import log

        log(
            f"Фоновая работа {key} выполняется в GUI-потоке — интерфейс будет "
            "заблокирован до её завершения",
            "❌ ERROR",
        )
    except Exception:
        pass
    return False


def build_background_worker_launcher(
    worker,
    run_method: Callable[[], object],
    run_method_name: str = "run",
) -> Callable[[], None]:
    """Строит единую проверяемую точку входа фонового worker-а.

    Её нужно подключать к ``QThread.started`` через ``DirectConnection``:
    сигнал испускается уже новым потоком, поэтому тяжёлый ``run`` гарантированно
    выполняется там же и не блокирует интерфейс в собранном приложении.
    """
    try:
        label = f"{type(worker).__name__}.{run_method_name}"
    except Exception:
        label = str(run_method_name or "worker.run")

    def _launch() -> None:
        ensure_background_thread(label)
        run_method()

    return _launch


__all__ = [
    "build_background_worker_launcher",
    "ensure_background_thread",
    "gui_thread_id",
    "is_gui_thread",
    "mark_gui_thread",
    "reset_gui_thread_marker",
]
