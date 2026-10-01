"""Правила обращения со службой драйвера WinDivert.

Как устроен драйвер (проверено по исходникам WinDivert и на живой Windows):

- Службу драйвера создаёт и запускает не программа, а сам winws — библиотека
  WinDivert внутри него. Сразу после запуска она помечает службу на удаление.
  Поэтому у РАБОТАЮЩЕГО драйвера запись выглядит «отключена, помечена на
  удаление» (Start=4, DeleteFlag=1) — это штатное состояние, а не поломка.
- Сам драйвер не выгружается, когда winws завершился. Он остаётся загруженным,
  и следующий winws открывает его напрямую, не обращаясь к диспетчеру служб.
  Значит, между перезапусками службу трогать не нужно вообще.
- Выгрузить драйвер можно только командой остановки, и только когда им никто
  не пользуется. Тогда запись исчезает сама за доли секунды.

Отсюда два вида «служба зависла», оба воспроизведены на живой системе:

1. Остановка при живом winws → служба висит в «останавливается», новый запуск
   падает (код 433/177). Проходит, как только завершатся все winws.
2. Какая-то программа держит открытый хэндл службы → после остановки запись
   остаётся «остановлена, отключена, помечена на удаление», новый запуск
   падает (код 1058/34). Проходит, как только хэндл закроют.

Поэтому этот модуль никогда не правит реестр, не снимает пометку удаления и
не меняет тип запуска: такие «лечения» рассинхронизируют реестр с диспетчером
служб и оставляют запись до перезагрузки. Он только спрашивает состояние,
останавливает драйвер по правилам и честно сообщает, что мешает.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from log.log import log

from . import winapi

# Имена службы драйвера. Наша сборка использует Monkey; остальные — имена
# оригинального WinDivert, которые могут остаться от других программ.
OWN_DRIVER_SERVICE_NAME = "Monkey"
DRIVER_SERVICE_NAMES = (OWN_DRIVER_SERVICE_NAME, "WinDivert", "WinDivert14", "WinDivert64")

_POLL_INTERVAL_SECONDS = 0.05
DEFAULT_WAIT_SECONDS = 3.0

# Что мешает запуску.
BLOCKER_NONE = ""
BLOCKER_STOP_PENDING = "stop_pending"
BLOCKER_STUCK_ENTRY = "stuck_entry"

# Итоги выгрузки драйвера.
RELEASE_ABSENT = "absent"
RELEASE_RELEASED = "released"
RELEASE_SKIPPED_IN_USE = "skipped_in_use"
RELEASE_SKIPPED_ANTIVIRUS = "skipped_antivirus"
RELEASE_STUCK_STOP_PENDING = "stuck_stop_pending"
RELEASE_STUCK_ENTRY = "stuck_entry"
RELEASE_ERROR = "error"

MESSAGE_STOP_PENDING = (
    "Драйвер WinDivert ({name}) выгружается, но его ещё держит другая программа. "
    "Закройте другие программы обхода блокировок и повторите запуск; "
    "если не поможет — перезагрузите компьютер"
)
MESSAGE_STUCK_ENTRY = (
    "Запись службы драйвера WinDivert ({name}) осталась после остановки: её держит "
    "открытой другая программа (окно «Службы», Process Hacker, Process Explorer или "
    "антивирус). Закройте её и повторите запуск; если не поможет — перезагрузите компьютер"
)


@dataclass(frozen=True, slots=True)
class DriverPreflight:
    """Можно ли сейчас запускать движок с точки зрения службы драйвера."""

    ok: bool
    blocker: str = BLOCKER_NONE
    service: str = ""
    message: str = ""


@dataclass(frozen=True, slots=True)
class DriverReleaseResult:
    outcome: str
    service: str = ""
    message: str = ""

    @property
    def stuck(self) -> bool:
        return self.outcome in (RELEASE_STUCK_STOP_PENDING, RELEASE_STUCK_ENTRY)


def normalize_driver_image_path(image_path: str) -> str:
    """Путь к файлу драйвера из записи службы в обычном виде."""
    text = str(image_path or "").strip().strip('"')
    for prefix in ("\\??\\", "\\\\?\\"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if not text:
        return ""
    # Пути из диспетчера служб всегда с "\"; на тестовых POSIX-системах
    # os.path с ними не работает, поэтому приводим разделитель явно.
    return os.path.normpath(text.replace("\\", os.sep)).lower()


def _normalize_root(root: str) -> str:
    text = str(root or "").strip()
    if not text:
        return ""
    return os.path.normpath(text.replace("\\", os.sep)).lower()


def is_own_driver(image_path: str, own_roots: Iterable[str]) -> bool:
    """Лежит ли файл драйвера в одной из папок нашей установки."""
    driver_path = normalize_driver_image_path(image_path)
    if not driver_path:
        return False
    for root in own_roots:
        normalized_root = _normalize_root(root)
        if not normalized_root:
            continue
        if driver_path == normalized_root or driver_path.startswith(normalized_root + os.sep):
            return True
    return False


def _is_relevant(info: winapi.ServiceInfo, own_roots: list[str]) -> bool:
    """Относится ли запись к драйверу, которым пользуется наш движок.

    Наша сборка WinDivert называет службу Monkey, поэтому запись с этим
    именем наша при любом пути к файлу: если драйвер под этим именем загружен
    из другой папки (остался от другой копии программы), наш winws всё равно
    будет работать именно с ним. Записи с именами оригинального WinDivert
    принадлежат другим программам, пока их файл не лежит в нашей папке.
    """
    return info.name == OWN_DRIVER_SERVICE_NAME or is_own_driver(info.image_path, own_roots)


def list_driver_services(own_roots: Iterable[str] = ()) -> list[winapi.ServiceInfo]:
    """Записи службы драйвера, относящиеся к нашему движку.

    Бросает WinApiError, если диспетчер служб не ответил.
    """
    roots = [root for root in own_roots if str(root or "").strip()]
    services: list[winapi.ServiceInfo] = []
    for name in DRIVER_SERVICE_NAMES:
        info = winapi.query_service(name)
        if info is not None and _is_relevant(info, roots):
            services.append(info)
    return services


def _is_marked_leftover(info: winapi.ServiceInfo) -> bool:
    """Остановленная запись, помеченная на удаление: ждёт закрытия хэндлов."""
    return info.state == winapi.SERVICE_STOPPED and info.start_type == winapi.SERVICE_DISABLED


def _is_startable_as_is(info: winapi.ServiceInfo) -> bool:
    """Запись не мешает запуску: winws либо откроет драйвер, либо запустит его."""
    if info.state in (winapi.SERVICE_RUNNING, winapi.SERVICE_START_PENDING):
        return True
    if info.state == winapi.SERVICE_STOPPED:
        # Остановленная запись с обычным типом запуска — winws запустит её сам.
        # Остановленная и отключённая — та самая застрявшая запись: запустить
        # её нельзя, а создать новую не даёт пометка удаления.
        return not _is_marked_leftover(info)
    return False


def _wait_until(
    predicate: Callable[[], bool],
    *,
    wait_seconds: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> bool:
    """Ждёт, пока состояние службы не станет нужным.

    У диспетчера служб нет простого события «запись исчезла», поэтому
    состояние переспрашивается короткими шагами до общего срока. Каждый
    запрос открывает и сразу закрывает хэндл службы — этого достаточно, чтобы
    Windows убрала запись, которую больше никто не держит.
    """
    deadline = clock() + max(0.0, float(wait_seconds))
    while True:
        if predicate():
            return True
        if clock() >= deadline:
            return False
        sleep(_POLL_INTERVAL_SECONDS)


def ensure_driver_startable(
    *,
    own_roots: Iterable[str] = (),
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> DriverPreflight:
    """Проверяет перед запуском, что служба драйвера не застряла.

    Только чтение: ничего не останавливает и не правит. Если драйвер как раз
    выгружается, даёт ему до ``wait_seconds`` закончить.
    """
    roots = [root for root in own_roots if str(root or "").strip()]
    blocked: dict[str, winapi.ServiceInfo] = {}

    def _settled() -> bool:
        blocked.clear()
        for info in list_driver_services(roots):
            if not _is_startable_as_is(info):
                blocked[info.name] = info
        return not blocked

    try:
        settled = _wait_until(_settled, wait_seconds=wait_seconds, clock=clock, sleep=sleep)
    except winapi.WinApiError as exc:
        # Не смогли спросить — не мешаем запуску: причину отказа, если она
        # есть, назовёт сам winws в своём выводе.
        log(f"Не удалось проверить службу драйвера WinDivert: {exc}", "DEBUG")
        return DriverPreflight(ok=True)

    if settled:
        return DriverPreflight(ok=True)

    name, info = next(iter(blocked.items()))
    if info.state == winapi.SERVICE_STOP_PENDING:
        blocker, template = BLOCKER_STOP_PENDING, MESSAGE_STOP_PENDING
    else:
        blocker, template = BLOCKER_STUCK_ENTRY, MESSAGE_STUCK_ENTRY
    message = template.format(name=name)
    log(
        f"Служба драйвера {name} мешает запуску: state={info.state}, "
        f"start_type={info.start_type}, blocker={blocker}",
        "WARNING",
    )
    return DriverPreflight(ok=False, blocker=blocker, service=name, message=message)


def release_driver_if_unused(
    *,
    own_roots: Iterable[str],
    engine_in_use: Callable[[], bool],
    antivirus_blocks_unload: Optional[Callable[[], bool]] = None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> DriverReleaseResult:
    """Выгружает драйвер и убирает его службу, если им никто не пользуется.

    Условия, при которых драйвер не трогается:

    - жив хотя бы один winws (свой или чужой) — остановка при открытом
      хэндле драйвера и создаёт зависшую службу;
    - установлен антивирус, с фильтрами которого выгрузка драйвера опасна
      (Kaspersky: известен синий экран в tcpip.sys).

    Записи оригинального WinDivert, чей файл лежит не в нашей папке, сюда не
    попадают вовсе: они принадлежат другим программам.
    """
    roots = [root for root in own_roots if str(root or "").strip()]

    try:
        services = list_driver_services(roots)
    except winapi.WinApiError as exc:
        log(f"Не удалось прочитать службу драйвера WinDivert: {exc}", "WARNING")
        return DriverReleaseResult(RELEASE_ERROR, message=str(exc))

    if not services:
        return DriverReleaseResult(RELEASE_ABSENT)

    try:
        in_use = bool(engine_in_use())
    except Exception as exc:
        # Не знаем, пользуются ли драйвером, — значит, не трогаем.
        log(f"Не удалось проверить, занят ли драйвер WinDivert: {exc}", "WARNING")
        in_use = True
    if in_use:
        log("Драйвер WinDivert не выгружаем: им ещё пользуется процесс winws", "DEBUG")
        return DriverReleaseResult(RELEASE_SKIPPED_IN_USE, service=services[0].name)

    if antivirus_blocks_unload is not None:
        try:
            blocked = bool(antivirus_blocks_unload())
        except Exception as exc:
            # Сбой определения антивируса трактуем в безопасную сторону.
            log(f"Не удалось определить антивирус перед выгрузкой драйвера: {exc}", "WARNING")
            blocked = True
        if blocked:
            log(
                "Драйвер WinDivert не выгружаем: выгрузка рядом с фильтрами антивируса "
                "может привести к сбою системы",
                "INFO",
            )
            return DriverReleaseResult(RELEASE_SKIPPED_ANTIVIRUS, service=services[0].name)

    result = DriverReleaseResult(RELEASE_ABSENT)
    for info in services:
        outcome = _release_one(info, wait_seconds=wait_seconds, clock=clock, sleep=sleep)
        if outcome.stuck or outcome.outcome == RELEASE_ERROR:
            return outcome
        if outcome.outcome == RELEASE_RELEASED or result.outcome == RELEASE_ABSENT:
            result = outcome
    return result


def _release_one(
    info: winapi.ServiceInfo,
    *,
    wait_seconds: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> DriverReleaseResult:
    name = info.name

    try:
        if info.state == winapi.SERVICE_RUNNING:
            # Библиотека WinDivert уже пометила запись на удаление при запуске
            # драйвера: после остановки диспетчер служб уберёт её сам.
            winapi.send_service_stop(name)
        elif info.state == winapi.SERVICE_STOPPED and not _is_marked_leftover(info):
            # Остановленная запись без пометки — остаток от прошлых версий
            # программы или от перезагрузки. Убираем штатным вызовом.
            winapi.delete_service(name)
        # Иначе запись уже останавливается или ждёт удаления: остаётся
        # дождаться, пока Windows уберёт её сама.

        last: dict[str, Optional[winapi.ServiceInfo]] = {"info": info}

        def _gone() -> bool:
            last["info"] = winapi.query_service(name)
            return last["info"] is None

        gone = _wait_until(_gone, wait_seconds=wait_seconds, clock=clock, sleep=sleep)
    except winapi.WinApiError as exc:
        log(f"Не удалось выгрузить драйвер {name}: {exc}", "WARNING")
        return DriverReleaseResult(RELEASE_ERROR, service=name, message=str(exc))

    if gone:
        log(f"Драйвер {name} выгружен, запись службы убрана", "INFO")
        return DriverReleaseResult(RELEASE_RELEASED, service=name)

    remaining = last["info"]
    state = remaining.state if remaining is not None else None
    if state == winapi.SERVICE_STOP_PENDING:
        message = MESSAGE_STOP_PENDING.format(name=name)
        log(f"Драйвер {name} не выгрузился: его держит другая программа", "WARNING")
        return DriverReleaseResult(RELEASE_STUCK_STOP_PENDING, service=name, message=message)

    message = MESSAGE_STUCK_ENTRY.format(name=name)
    log(
        f"Запись службы {name} осталась после остановки драйвера (state={state}): "
        "её держит открытой другая программа",
        "WARNING",
    )
    return DriverReleaseResult(RELEASE_STUCK_ENTRY, service=name, message=message)
