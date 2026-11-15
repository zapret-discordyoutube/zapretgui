from __future__ import annotations

import os
from dataclasses import dataclass

from platforms import Cap, current as current_platform

from settings.mode import (
    ALL_WINWS_EXE_NAMES,
    EXE_NAME_WINWS1,
    EXE_NAME_WINWS2,
    ZAPRET1_MODE,
    ZAPRET2_MODE,
    exe_path_for_launch_method,
)
from winws_runtime.engine.process_control import normalize_image_path as _normalize_path

_WINWS_NAMES = ALL_WINWS_EXE_NAMES
_WINWS_NAME_SET = frozenset(_WINWS_NAMES)


@dataclass(frozen=True, slots=True)
class WinwsProcessRecord:
    pid: int
    name: str
    exe_path: str


def get_expected_winws_paths() -> dict[str, str]:
    paths = {
        EXE_NAME_WINWS1: _normalize_path(exe_path_for_launch_method(ZAPRET1_MODE)),
        EXE_NAME_WINWS2: _normalize_path(exe_path_for_launch_method(ZAPRET2_MODE)),
    }
    return {name: path for name, path in paths.items() if path}


def _iter_winws_process_entries() -> list[tuple[int, str]]:
    return [
        (int(pid), name)
        for pid, raw_name in current_platform().processes.list_processes()
        if (name := str(raw_name or "").strip().lower()) in _WINWS_NAME_SET
    ]


def _query_process_image_path(pid: int) -> str:
    return _normalize_path(current_platform().processes.exe_path(int(pid)))


def query_process_start_time(pid: int) -> float:
    """Когда система создала процесс (секунды Unix); 0.0 — узнать не удалось."""
    return current_platform().processes.start_time(int(pid))


def _engine_owned_pids() -> list[int]:
    """Where the engine is not a program the process list names (Linux: nfqws2 behind the root helper) the engine
    interface tells which process is ours. Windows keeps the scan by name and path."""
    platform = current_platform()
    if platform.has(Cap.DIRECT_ENGINE_PROCESS):
        return []
    try:
        return [int(pid) for pid in platform.engine.running_pids()]
    except Exception:
        return []


def find_expected_winws_processes(expected_exe_path: str) -> list[WinwsProcessRecord]:
    normalized_expected_path = _normalize_path(expected_exe_path)
    expected_name = os.path.basename(normalized_expected_path).strip().lower()

    if not current_platform().has(Cap.DIRECT_ENGINE_PROCESS):
        if expected_name != EXE_NAME_WINWS2:
            return []
        return [WinwsProcessRecord(pid=pid, name=EXE_NAME_WINWS2, exe_path=normalized_expected_path) for pid in sorted(_engine_owned_pids())]

    if expected_name not in _WINWS_NAME_SET or not normalized_expected_path:
        return []
    if not os.path.exists(normalized_expected_path):
        return []

    matches: list[WinwsProcessRecord] = []
    for pid, process_name in _iter_winws_process_entries():
        if process_name != expected_name:
            continue
        process_path = _query_process_image_path(pid)
        if not process_path or process_path != normalized_expected_path:
            continue
        matches.append(
            WinwsProcessRecord(
                pid=int(pid),
                name=process_name,
                exe_path=process_path,
            )
        )

    matches.sort(key=lambda item: item.pid)
    return matches


def find_foreign_winws_processes() -> list[WinwsProcessRecord]:
    """winws-named processes that are NOT our canonical executables.

    A record counts as foreign only when its image path was successfully
    queried and differs from the expected canonical path for that name —
    an unqueryable path never raises a false alarm.
    """
    expected_paths = get_expected_winws_paths()
    foreign: list[WinwsProcessRecord] = []
    for pid, process_name in _iter_winws_process_entries():
        process_path = _query_process_image_path(pid)
        if not process_path:
            continue
        expected_path = expected_paths.get(process_name, "")
        if expected_path and process_path == expected_path:
            continue
        foreign.append(
            WinwsProcessRecord(
                pid=int(pid),
                name=process_name,
                exe_path=process_path,
            )
        )
    foreign.sort(key=lambda item: item.pid)
    return foreign


def find_canonical_winws_processes() -> dict[str, list[WinwsProcessRecord]]:
    result: dict[str, list[WinwsProcessRecord]] = {}
    for process_name, expected_path in get_expected_winws_paths().items():
        matches = find_expected_winws_processes(expected_path)
        if matches:
            result[process_name] = matches
    return result


@dataclass(frozen=True, slots=True)
class WinwsProcessScan:
    """Канонические и посторонние winws-процессы по одному снимку процессов."""

    canonical_pids: dict[str, list[int]]
    foreign_paths: dict[int, str]


def scan_winws_processes() -> WinwsProcessScan:
    """Один снимок процессов и один запрос пути на каждый winws.

    Даёт тот же результат, что get_canonical_winws_process_pids() вместе с
    find_foreign_winws_processes(), но без трёх полных снимков подряд:
    монитор процессов зовёт это каждые 2 секунды всё время работы программы.
    """
    if not current_platform().has(Cap.DIRECT_ENGINE_PROCESS):
        owned = sorted(_engine_owned_pids())
        return WinwsProcessScan(canonical_pids={EXE_NAME_WINWS2: owned} if owned else {}, foreign_paths={})
    expected_paths = get_expected_winws_paths()
    existing_expected = {
        name: path for name, path in expected_paths.items() if os.path.exists(path)
    }
    canonical: dict[str, list[int]] = {}
    foreign: dict[int, str] = {}
    for pid, process_name in _iter_winws_process_entries():
        process_path = _query_process_image_path(pid)
        if not process_path:
            continue
        expected_path = expected_paths.get(process_name, "")
        if expected_path and process_path == expected_path:
            if process_name in existing_expected:
                canonical.setdefault(process_name, []).append(int(pid))
            continue
        foreign[int(pid)] = process_path
    for pids in canonical.values():
        pids.sort()
    return WinwsProcessScan(
        canonical_pids=canonical,
        foreign_paths=dict(sorted(foreign.items())),
    )


def get_canonical_winws_process_pids() -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for process_name, records in find_canonical_winws_processes().items():
        pids = [record.pid for record in records if isinstance(record.pid, int)]
        if pids:
            result[process_name] = pids
    return result


def is_expected_winws_running(expected_exe_path: str) -> bool:
    return bool(find_expected_winws_processes(expected_exe_path))


def is_any_canonical_winws_running() -> bool:
    return bool(get_canonical_winws_process_pids())


def is_winws_process_pid_alive(pid: int, expected_name: str = "") -> bool:
    try:
        target_pid = int(pid)
    except Exception:
        return False

    normalized_name = str(expected_name or "").strip().lower()
    for current_pid, process_name in _iter_winws_process_entries():
        if int(current_pid) != target_pid:
            continue
        if normalized_name and str(process_name or "").strip().lower() != normalized_name:
            return False
        return True
    return False
