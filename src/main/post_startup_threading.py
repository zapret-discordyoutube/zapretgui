"""Фоновые задачи запуска: по одной, а не пачкой.

В Python одновременно выполняется только один поток — остальные ждут общий
замок интерпретатора (GIL). Поэтому семь фоновых потоков не делают запуск в
семь раз быстрее: они по очереди отбирают замок друг у друга и у GUI-потока.
Замеры на win10 (подробнее — main/startup_contract.md, «Одна работа за раз»):

    проверка каталога hosts сама по себе                17 мс
    она же при запуске, рядом с шестью потоками      ~1100 мс
    список профилей при запуске, свой поток            960 мс
    он же на общей дорожке                              85 мс
    сборка списков адресов, свой поток                1097 мс
    она же на общей дорожке                             26 мс
    запуск обхода (winws2) рядом с шестью потоками    1695 мс
    он же рядом с одной дорожкой                       907 мс

Раньше у каждой подсистемы был свой поток, и после появления окна сразу
стартовали профили, пресеты, hosts, DNS, списки и подгрузка модулей. Теперь
правило одно: задача, занятая диском и процессором, в каждый момент
выполняется не больше одной — на общей дорожке (`StartupLane`). Свой поток
остаётся только у очередей из WAITING_QUEUES: их задачи почти всё время ждут
ответа сети или чужого процесса и замок не держат.

GUI-поток участвует в той же очерёдности. Пока он собирает скрытую страницу
(main.post_startup_idle_tasks), дорожка не начинает следующую задачу, а
страница не начинает собираться, пока дорожка занята.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from queue import Queue

from PyQt6.QtCore import QTimer

from main import startup_audit


LOCAL_LANE_THREAD_NAME = "StartupLane"

# Очереди общей дорожки: задачи заняты диском и процессором. Синхронизация
# пресетов с сервером тоже стоит в «presets», хотя ждёт сеть: она пишет файлы
# пресетов и не должна идти одновременно с их переводом в новый формат.
LOCAL_QUEUES: frozenset[str] = frozenset(
    {
        "startup",
        "imports",
        "profile",
        "presets",
        "hosts",
        "lists",
        "checks",
        "maintenance",
        "onboarding",
        "pages",
        "premium",
        "telegram_proxy",
    }
)

# Очереди, задачи которых почти всё время ждут: запрос в сеть (обновления),
# сон на десятки секунд (диагностика), ответ локального DNS-прокси при его
# починке (до 25 с). Ожидание не держит замок Python и окну не мешает, а на
# общей дорожке оно задержало бы всех остальных.
WAITING_QUEUES: frozenset[str] = frozenset(
    {
        "update",
        "diagnostics",
        "dns",
    }
)

# Дольше этого дорожка сборку страницы в GUI-потоке не ждёт: страховка на
# случай, если отметка о сборке не снялась.
GUI_BUILD_WAIT_MAX_SEC = 2.0

# Поднят, пока GUI-поток НЕ собирает скрытую страницу.
_GUI_BUILD_DONE = threading.Event()
_GUI_BUILD_DONE.set()


class _LocalLane:
    """Общая дорожка: одна задача за раз.

    Порядок: сначала то, что сразу видно на экране, потом обычные задачи,
    в конце прогревы. Внутри каждой группы — в порядке постановки.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._on_screen: deque[tuple[str, Callable[[], None]]] = deque()
        self._tasks: deque[tuple[str, Callable[[], None]]] = deque()
        self._warmups: deque[tuple[str, Callable[[], None]]] = deque()
        self._running = False
        self._thread: threading.Thread | None = None

    def put(
        self,
        task_name: str,
        target: Callable[[], None],
        *,
        warmup: bool,
        on_screen: bool,
    ) -> threading.Thread:
        with self._condition:
            if on_screen:
                tasks = self._on_screen
            elif warmup:
                tasks = self._warmups
            else:
                tasks = self._tasks
            tasks.append((task_name, target))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run,
                    daemon=True,
                    name=LOCAL_LANE_THREAD_NAME,
                )
                self._thread.start()
            self._condition.notify()
            return self._thread

    def is_busy(self) -> bool:
        with self._condition:
            return self._running or self._has_pending()

    def _has_pending(self) -> bool:
        return bool(self._on_screen or self._tasks or self._warmups)

    def _run(self) -> None:
        thread = threading.current_thread()
        while True:
            with self._condition:
                while not self._has_pending():
                    self._condition.wait()
                task_name, target = (self._on_screen or self._tasks or self._warmups).popleft()
                self._running = True
            _GUI_BUILD_DONE.wait(GUI_BUILD_WAIT_MAX_SEC)
            # Имя задачи видно в отчёте о рывках (ui.ui_freeze_watchdog):
            # там перечислены занятые потоки по именам.
            thread.name = f"{LOCAL_LANE_THREAD_NAME}:{task_name}"
            try:
                _run_audited_target(task_name, target)
            except Exception as exc:
                _log_task_failure(task_name, exc)
            finally:
                thread.name = LOCAL_LANE_THREAD_NAME
                with self._condition:
                    self._running = False


@dataclass(slots=True)
class _WaitingTaskQueue:
    name: str
    tasks: Queue = field(default_factory=Queue)
    thread: threading.Thread | None = None

    def ensure_started(self) -> threading.Thread:
        if self.thread is not None and self.thread.is_alive():
            return self.thread
        thread_name = f"StartupQueue-{self.name}"
        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=thread_name,
        )
        self.thread.start()
        return self.thread

    def put(self, task_name: str, target: Callable[[], None]) -> threading.Thread:
        thread = self.ensure_started()
        self.tasks.put((str(task_name or self.name), target))
        return thread

    def _run(self) -> None:
        while True:
            task_name, target = self.tasks.get()
            try:
                _run_audited_target(task_name, target)
            except Exception as exc:
                _log_task_failure(task_name, exc)
            finally:
                self.tasks.task_done()


_LOCAL_LANE = _LocalLane()
_WAITING_TASK_QUEUES: dict[str, _WaitingTaskQueue] = {}
_WAITING_TASK_QUEUES_LOCK = threading.RLock()


def start_daemon_thread(name: str, target: Callable[[], None]) -> threading.Thread:
    """Отдельный поток — только для задачи, которая в основном ждёт.

    Задачу, занятую диском и процессором, ставят через enqueue_subsystem_task:
    иначе она снова будет делить замок Python с остальными.
    """
    thread_name = str(name or "StartupPostInitWorker")
    thread = threading.Thread(
        target=lambda: _run_audited_target(thread_name, target),
        daemon=True,
        name=thread_name,
    )
    thread.start()
    return thread


def enqueue_subsystem_task(
    queue_name: str,
    task_name: str,
    target: Callable[[], None],
    *,
    warmup: bool = False,
    on_screen: bool = False,
) -> threading.Thread:
    """Ставит фоновую задачу запуска.

    Задачи выполняются в порядке постановки. warmup=True — задача только
    готовит данные заранее (кэш страницы): она пропускает вперёд всё, что
    нужно программе для работы, даже поставленное позже. on_screen=True —
    результат сразу виден на открытой странице (плитка «Профили» на главной):
    такая задача идёт раньше остальных, чтобы окно не стояло полупустым.

    Имя очереди должно стоять в LOCAL_QUEUES или в WAITING_QUEUES: автор задачи
    сам решает, считает она или ждёт (tests/test_startup_background_lane.py
    проверяет это по исходникам). Незнакомое имя идёт на общую дорожку.
    """
    normalized_queue_name = str(queue_name or "default").strip() or "default"
    normalized_task_name = str(task_name or normalized_queue_name)
    if normalized_queue_name not in WAITING_QUEUES:
        return _LOCAL_LANE.put(
            normalized_task_name,
            target,
            warmup=bool(warmup),
            on_screen=bool(on_screen),
        )
    with _WAITING_TASK_QUEUES_LOCK:
        task_queue = _WAITING_TASK_QUEUES.get(normalized_queue_name)
        if task_queue is None:
            task_queue = _WaitingTaskQueue(normalized_queue_name)
            _WAITING_TASK_QUEUES[normalized_queue_name] = task_queue
        return task_queue.put(normalized_task_name, target)


def is_local_lane_busy() -> bool:
    """На общей дорожке есть работа: выполняется или ждёт очереди."""
    return _LOCAL_LANE.is_busy()


# Сколько вложенных сборок сейчас идёт в GUI-потоке: сборка страницы про запас
# достраивает внутри себя её блоки первого экрана. Меняется только в GUI-потоке.
_gui_build_depth = 0


@contextmanager
def gui_build_turn() -> Iterator[None]:
    """Очередь GUI-потока: пока он собирает страницу или её блок, дорожка новую задачу не начинает.

    Вызывается только из GUI-потока; вложенные вызовы допустимы — дорожка
    ждёт, пока закончится самый внешний.
    """
    global _gui_build_depth
    _gui_build_depth += 1
    _GUI_BUILD_DONE.clear()
    try:
        yield
    finally:
        _gui_build_depth -= 1
        if _gui_build_depth <= 0:
            _gui_build_depth = 0
            _GUI_BUILD_DONE.set()


def _log_task_failure(task_name: str, exc: Exception) -> None:
    try:
        from log.log import log

        log(f"Фоновая задача запуска {task_name} завершилась ошибкой: {exc}", "DEBUG")
    except Exception:
        pass


def schedule_after(delay_ms: int, callback: Callable[[], None]) -> None:
    delay = int(delay_ms)
    callback_name = getattr(callback, "__name__", "startup_timer")
    startup_audit.audit_timer_queued(str(callback_name), delay)

    def _run_callback() -> None:
        startup_audit.audit_timer_fired(str(callback_name), delay)
        callback()

    QTimer.singleShot(delay, _run_callback)


def _run_audited_target(name: str, target: Callable[[], None]) -> None:
    if not startup_audit.is_startup_audit_enabled():
        target()
        return
    task_id = startup_audit.audit_task_begin(str(name or "StartupPostInitWorker"), "thread")
    try:
        target()
    finally:
        startup_audit.audit_task_end(task_id, str(name or "StartupPostInitWorker"), "thread")
