"""Подтверждение запуска движка по его собственному выводу.

winws и winws2 печатают строку ``windivert initialized`` сразу после того,
как драйвер открыт и перехват начался. Это настоящее подтверждение запуска —
в отличие от прежнего правила «процесс прожил секунду, значит запустился»,
которое не знало ни про драйвер, ни про загрузку больших списков.

Вывод движка идёт в файл, а не в канал (pipe): движок, пишущий в канал,
встаёт или умирает, если программа перестала читать или закрылась, а режим
«выйти, оставив обход включённым» должен продолжать работать.

Строка печатается ДО загрузки Lua-скриптов, поэтому после неё есть короткое
окно ``settle``: если скрипты не загрузились, процесс умрёт именно в нём.
Окно — это ожидание на хэндле процесса: при смерти процесса оно прерывается
мгновенно, а не досыпает остаток.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .process_control import wait_process_exit

READY_MARKER = b"windivert initialized"

# Окно после строки готовности, в котором проявляются ошибки Lua-скриптов.
DEFAULT_SETTLE_SECONDS = 0.35
# Сколько ждать строку готовности. Большие списки хостов и адресов грузятся
# до открытия драйвера, поэтому срок с запасом.
DEFAULT_READY_TIMEOUT_SECONDS = 15.0
# Окно для запуска без файла вывода: подтвердить готовность нечем, остаётся
# убедиться, что процесс не умер сразу.
DEFAULT_ALIVE_WINDOW_SECONDS = 1.0
_TAIL_TICK_SECONDS = 0.01

REASON_READY = "ready"
REASON_ALIVE_WITHOUT_MARKER = "alive_without_marker"
REASON_EXITED = "exited"
REASON_EXITED_AFTER_READY = "exited_after_ready"


@dataclass(frozen=True, slots=True)
class EngineStartOutcome:
    started: bool
    reason: str
    marker_seen: bool
    elapsed: float


class _OutputTail:
    """Дочитывает новые байты файла вывода и ищет в них строку готовности."""

    def __init__(self, path: str, marker: bytes):
        self._path = str(path or "")
        self._marker = bytes(marker)
        self._offset = 0
        self._carry = b""

    def marker_appeared(self) -> bool:
        if not self._path:
            return False
        try:
            with open(self._path, "rb") as stream:
                stream.seek(self._offset)
                chunk = stream.read()
        except OSError:
            return False
        if not chunk:
            return False
        self._offset += len(chunk)
        window = self._carry + chunk
        if self._marker in window:
            return True
        # Строка может лечь на границу двух чтений — держим хвост.
        keep = max(0, len(self._marker) - 1)
        self._carry = window[-keep:] if keep else b""
        return False


def wait_engine_ready(
    process,
    output_path: str,
    *,
    settle: float = DEFAULT_SETTLE_SECONDS,
    ready_timeout: float = DEFAULT_READY_TIMEOUT_SECONDS,
    alive_window: float = DEFAULT_ALIVE_WINDOW_SECONDS,
    marker: bytes = READY_MARKER,
    clock=time.monotonic,
) -> EngineStartOutcome:
    """Ждёт подтверждения, что движок запустился и не умер при инициализации."""
    started_at = clock()

    def _elapsed() -> float:
        return max(0.0, clock() - started_at)

    if not str(output_path or ""):
        if wait_process_exit(process, max(0.0, float(alive_window))):
            return EngineStartOutcome(False, REASON_EXITED, False, _elapsed())
        return EngineStartOutcome(True, REASON_ALIVE_WITHOUT_MARKER, False, _elapsed())

    tail = _OutputTail(output_path, marker)
    deadline = started_at + max(0.0, float(ready_timeout))
    marker_seen = False
    while True:
        if tail.marker_appeared():
            marker_seen = True
            break
        # Тик ожидания — это ожидание на хэндле процесса: смерть процесса
        # будит поток сразу, а не через остаток паузы.
        if wait_process_exit(process, _TAIL_TICK_SECONDS):
            # Процесс мог напечатать строку и умереть между двумя чтениями.
            marker_seen = tail.marker_appeared()
            return EngineStartOutcome(
                False,
                REASON_EXITED_AFTER_READY if marker_seen else REASON_EXITED,
                marker_seen,
                _elapsed(),
            )
        if clock() >= deadline:
            break

    if not marker_seen:
        # Процесс жив, но строки нет: сборка без этой строки или очень долгая
        # загрузка списков. Раньше такой запуск считался удачным по одной
        # лишь живости процесса — не делаем хуже, но сообщаем вызывающему.
        return EngineStartOutcome(True, REASON_ALIVE_WITHOUT_MARKER, False, _elapsed())

    if wait_process_exit(process, max(0.0, float(settle))):
        return EngineStartOutcome(False, REASON_EXITED_AFTER_READY, True, _elapsed())
    return EngineStartOutcome(True, REASON_READY, True, _elapsed())
