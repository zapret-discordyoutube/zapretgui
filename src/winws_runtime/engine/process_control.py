"""Остановка процессов движка с подтверждением выхода.

Здесь нет ни одной паузы «на всякий случай». Подтверждением служит сигнал
Windows на хэндле процесса: он приходит, когда процесс действительно
завершился и система закрыла его хэндлы, включая хэндл драйвера WinDivert.
Замер на живой Windows: 25 перезапусков winws2 подряд сразу после такого
сигнала прошли без единого сбоя, поэтому дополнительное ожидание не нужно.

Останавливаются только СВОИ процессы — те, чей полный путь к exe совпадает с
ожидаемым. Чужой winws (другая копия запрета) принадлежит не нам; убивать
его по одному только имени нельзя.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Iterable

from log.log import log
from utils.windows_process_probe import (
    ProcessSnapshotError,
    iter_process_records_winapi_strict,
)

from . import winapi

# Код завершения, с которым мы останавливаем движок сами. У TerminateProcess
# по умолчанию код 1 — такой же, как у молчаливого падения winws, из-за чего
# собственная остановка была неотличима от сбоя.
ENGINE_STOP_EXIT_CODE = 0x5A50

_STOP_ACCESS = (
    winapi.PROCESS_TERMINATE
    | winapi.SYNCHRONIZE
    | winapi.PROCESS_QUERY_LIMITED_INFORMATION
)


def normalize_image_path(path: str) -> str:
    """Путь к exe в виде, пригодном для сравнения."""
    text = str(path or "").strip()
    if not text:
        return ""
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    elif text.startswith("\\??\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\??\\"):
        text = text[4:]
    try:
        text = os.path.abspath(text)
    except Exception:
        pass
    return os.path.normcase(text)


def wait_process_exit(process, timeout: float) -> bool:
    """Ждёт выхода процесса. True — выход подтверждён.

    ``Popen.wait(timeout)`` на Windows — это ``WaitForSingleObject`` на хэндле
    процесса: поток спит до самого события, без опроса.
    """
    try:
        process.wait(timeout=max(0.0, float(timeout)))
        return True
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        try:
            return process.poll() is not None
        except Exception:
            return False


def _terminate(process) -> None:
    handle = getattr(process, "_handle", None)
    if handle is not None and winapi.is_available():
        try:
            winapi.terminate_process(int(handle), ENGINE_STOP_EXIT_CODE)
            return
        except winapi.WinApiError as exc:
            # 5 здесь означает, что процесс уже завершается сам.
            if exc.code != winapi.ERROR_ACCESS_DENIED:
                log(f"TerminateProcess для PID={getattr(process, 'pid', '?')}: {exc}", "DEBUG")
    process.terminate()


def stop_process(process, *, timeout: float = 5.0) -> bool:
    """Завершает запущенный нами процесс. True — выход подтверждён.

    У winws нет способа мягкой остановки для процесса без консоли (он собран
    под Cygwin и слушает только его сигналы), поэтому остановка — это
    TerminateProcess. Так же останавливает winws2 сам автор zapret в
    blockcheck2. Хэндл драйвера при этом закрывает Windows.
    """
    if process is None:
        return True
    try:
        if process.poll() is not None:
            return True
    except Exception:
        pass
    try:
        _terminate(process)
    except Exception as exc:
        log(f"Не удалось завершить PID={getattr(process, 'pid', '?')}: {exc}", "WARNING")
    return wait_process_exit(process, timeout)


@dataclass(frozen=True, slots=True)
class EngineProcessRecord:
    pid: int
    name: str
    exe_path: str


@dataclass(frozen=True, slots=True)
class EngineStopResult:
    """Итог остановки своих процессов движка.

    ``stopped`` — выход подтверждён; ``failed`` — процесс наш, но не вышел за
    отведённое время или не дал себя завершить; ``foreign`` — winws из другой
    папки, мы его не трогали.
    """

    stopped: tuple[int, ...] = ()
    failed: tuple[int, ...] = ()
    foreign: tuple[EngineProcessRecord, ...] = ()
    snapshot_failed: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed and not self.snapshot_failed


def _expected_by_name(expected_paths: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in expected_paths:
        normalized = normalize_image_path(path)
        if not normalized:
            continue
        # Разделитель разбираем сами: путь Windows должен читаться одинаково
        # и на Windows, и в тестах на другой системе.
        name = normalized.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
        if name:
            result[name] = normalized
    return result


def list_engine_processes(names: Iterable[str]) -> list[tuple[int, str]]:
    """Процессы с указанными именами exe. Бросает ProcessSnapshotError."""
    wanted = {str(name or "").strip().lower() for name in names if str(name or "").strip()}
    if not wanted:
        return []
    return [
        (int(pid), name)
        for pid, raw_name in iter_process_records_winapi_strict()
        if (name := str(raw_name or "").strip().lower()) in wanted
    ]


def stop_engine_processes(expected_paths: Iterable[str], *, timeout: float = 5.0) -> EngineStopResult:
    """Завершает все процессы движка, запущенные из наших exe.

    Принадлежность проверяется по тому же хэндлу, через который процесс затем
    завершается: между проверкой и завершением номер процесса не может
    достаться другой программе.
    """
    expected = _expected_by_name(expected_paths)
    if not expected:
        return EngineStopResult()

    try:
        candidates = list_engine_processes(expected.keys())
    except ProcessSnapshotError as exc:
        log(f"Не удалось получить список процессов: {exc}", "WARNING")
        return EngineStopResult(snapshot_failed=True)

    own_handles: dict[int, int] = {}
    failed: list[int] = []
    foreign: list[EngineProcessRecord] = []

    try:
        for pid, name in candidates:
            try:
                handle = winapi.open_process(pid, _STOP_ACCESS)
            except winapi.WinApiError as exc:
                if exc.code == winapi.ERROR_INVALID_PARAMETER:
                    continue  # процесс уже исчез
                # Не открылся — значит, проверить принадлежность нечем.
                # Считаем чужим: завершать без проверки пути нельзя.
                foreign.append(EngineProcessRecord(pid=pid, name=name, exe_path=""))
                log(f"Процесс {name} (PID {pid}) не открывается: {exc}", "DEBUG")
                continue

            keep_handle = False
            try:
                try:
                    image_path = normalize_image_path(winapi.query_image_path(handle))
                except winapi.WinApiError:
                    image_path = ""

                if image_path != expected[name]:
                    foreign.append(EngineProcessRecord(pid=pid, name=name, exe_path=image_path))
                    continue

                try:
                    winapi.terminate_process(handle, ENGINE_STOP_EXIT_CODE)
                except winapi.WinApiError as exc:
                    # 5: процесс уже завершается — дождёмся его ниже.
                    if exc.code != winapi.ERROR_ACCESS_DENIED:
                        log(f"TerminateProcess для PID={pid}: {exc}", "WARNING")
                        failed.append(pid)
                        continue
                own_handles[pid] = handle
                keep_handle = True
            finally:
                if not keep_handle:
                    winapi.close_handle(handle)

        stopped: list[int] = []
        if own_handles:
            pids = list(own_handles)
            # Ждём все процессы разом одним общим сроком, а не по очереди.
            all_exited = False
            for start in range(0, len(pids), winapi.MAXIMUM_WAIT_OBJECTS):
                chunk = pids[start:start + winapi.MAXIMUM_WAIT_OBJECTS]
                try:
                    all_exited = winapi.wait_for_all_handles(
                        [own_handles[pid] for pid in chunk],
                        timeout,
                    )
                except winapi.WinApiError as exc:
                    log(f"Ожидание выхода процессов движка: {exc}", "WARNING")
                    all_exited = False
                if not all_exited:
                    break
            for pid in pids:
                exited = all_exited
                if not exited:
                    try:
                        exited = winapi.wait_for_handle(own_handles[pid], 0.0)
                    except winapi.WinApiError:
                        exited = False
                (stopped if exited else failed).append(pid)
    finally:
        for handle in own_handles.values():
            winapi.close_handle(handle)

    if stopped:
        log(f"Процессы движка завершены, выход подтверждён: PID={stopped}", "INFO")
    if failed:
        log(f"Процессы движка не завершились за {timeout:g} с: PID={failed}", "ERROR")
    for record in foreign:
        log(
            f"Чужой процесс {record.name} (PID {record.pid}, {record.exe_path or 'путь неизвестен'}) "
            "не тронут: он запущен не из папки программы",
            "DEBUG",
        )

    return EngineStopResult(
        stopped=tuple(stopped),
        failed=tuple(failed),
        foreign=tuple(foreign),
    )
