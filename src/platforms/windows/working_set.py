"""Какие страницы памяти нужны программе и как вернуть их с диска одним заходом.

Пока окно свёрнуто или в трее, Windows отбирает у программы страницы памяти
(рабочий набор). Когда окно возвращают, главный поток снова обращается к этим
страницам, и каждое обращение — отдельный поход на диск за 4 КБ. Замер на win10:
после выгрузки возврат окна стоит ~11 000 таких обращений на ~50 МБ; на быстром
диске это лишние 0,3 с, на обычном жёстком (около 8–10 мс на чтение) — минуты.

Здесь два действия, оба без Qt:

- ``snapshot_working_set`` — какие адреса сейчас в оперативной памяти программы
  (QueryWorkingSet), слитые в непрерывные куски;
- ``prefetch_working_set`` — просит Windows подтянуть эти куски большими
  пачками (PrefetchVirtualMemory). Система читает диск одним планом, а не по
  странице, и это идёт в отдельном потоке, пока окно рисуется.

Подтягивать страницы с диска — это уже лечение. Лучше не дать Windows их
отобрать: пока окно скрыто, программа просит систему считать её рабочий набор
«жёстким минимумом» (``hold_working_set``) — страницы в его пределах система
не отбирает, и возврат окна идёт из памяти, а не с диска. Размер берётся по
факту (сколько страниц программа держит в момент ухода окна) и ограничен.

Принудительной выгрузки памяти здесь нет и быть не должно: она как раз и
заставляет окно открываться медленно. Звать ``SetProcessWorkingSetSizeEx`` с
«минус единицами» (это и есть выгрузка) нельзя: хватка отпускается обычным
малым минимумом.
"""

from __future__ import annotations

import ctypes
import sys
import time

from platforms.base import PrefetchResult, WorkingSetHold, WorkingSetSnapshot
from platforms.hold_policy import (  # noqa: F401  (re-exported: the policy is shared, the system calls are here)
    HOLD_MARGIN_BYTES,
    HOLD_MAX_BYTES,
    HOLD_MIN_BYTES,
    HOLD_SPARE_BYTES,
    hold_size_for,
)

PAGE_SIZE = 4096
# Сколько кусков отдаём Windows за один вызов: система принимает массивом, но
# очень длинный массив лучше резать.
_PREFETCH_CHUNK = 2048
# Начальный размер буфера QueryWorkingSet в страницах (хватает на ~1 ГБ); при нехватке растёт.
_INITIAL_PAGES = 1 << 18

_MB = 1 << 20
RELEASED_MIN_BYTES = 1 * _MB

_QUOTA_LIMITS_HARDWS_MIN_ENABLE = 0x1
_QUOTA_LIMITS_HARDWS_MIN_DISABLE = 0x2
_QUOTA_LIMITS_HARDWS_MAX_DISABLE = 0x8
_ERROR_PRIVILEGE_NOT_HELD = 1314


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _MemoryRange(ctypes.Structure):
    # WIN32_MEMORY_RANGE_ENTRY
    _fields_ = [("VirtualAddress", ctypes.c_void_p), ("NumberOfBytes", ctypes.c_size_t)]


def _merge_pages(pages: list[int]) -> list[tuple[int, int]]:
    """Отсортированные адреса страниц → куски подряд идущих страниц."""
    merged: list[list[int]] = []
    for address in pages:
        if merged and merged[-1][0] + merged[-1][1] == address:
            merged[-1][1] += PAGE_SIZE
        else:
            merged.append([address, PAGE_SIZE])
    return [(start, length) for start, length in merged]


def snapshot_working_set() -> WorkingSetSnapshot | None:
    """Адреса страниц, лежащих сейчас в памяти этого процесса. Не Windows или сбой — None."""
    if sys.platform != "win32":
        return None
    started = time.perf_counter()
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.QueryWorkingSet.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        process = kernel32.GetCurrentProcess()
        capacity = _INITIAL_PAGES
        for _attempt in range(6):
            # Первое слово — число записей, дальше по слову на страницу.
            buffer = (ctypes.c_size_t * (capacity + 1))()
            if psapi.QueryWorkingSet(process, buffer, ctypes.sizeof(buffer)):
                break
            capacity *= 2
        else:
            return None
        count = int(buffer[0])
        # Младшие 12 бит записи — признаки страницы, остальное — адрес.
        pages = sorted({(int(buffer[index + 1]) >> 12) << 12 for index in range(min(count, capacity))})
        ranges = _merge_pages(pages)
    except Exception:
        return None
    return WorkingSetSnapshot(
        ranges=tuple(ranges),
        total_bytes=len(pages) * PAGE_SIZE,
        taken_ms=(time.perf_counter() - started) * 1000,
    )


def prefetch_working_set(snapshot: WorkingSetSnapshot | None) -> PrefetchResult | None:
    """Просит Windows подтянуть страницы снимка в память. Блокирует вызывающий поток — звать из фонового."""
    if snapshot is None or not snapshot.ranges or sys.platform != "win32":
        return None
    started = time.perf_counter()
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        prefetch = getattr(kernel32, "PrefetchVirtualMemory", None)
        if prefetch is None:  # Windows 7
            return None
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        prefetch.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_ulong]
        process = kernel32.GetCurrentProcess()
        ok = True
        ranges = snapshot.ranges
        for offset in range(0, len(ranges), _PREFETCH_CHUNK):
            chunk = ranges[offset : offset + _PREFETCH_CHUNK]
            entries = (_MemoryRange * len(chunk))()
            for index, (start, length) in enumerate(chunk):
                entries[index].VirtualAddress = start
                entries[index].NumberOfBytes = length
            ok = bool(prefetch(process, len(chunk), entries, 0)) and ok
    except Exception:
        return None
    return PrefetchResult(
        ranges=len(snapshot.ranges),
        total_bytes=snapshot.total_bytes,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        ok=ok,
    )


def process_page_faults() -> int | None:
    """Сколько подкачек (всех: мягких и с диска) было у программы с начала. Не Windows или сбой — None."""
    counters = _process_memory_counters()
    return None if counters is None else int(counters.PageFaultCount)


def current_working_set_bytes() -> int | None:
    """Сколько оперативной памяти программа держит сейчас. Не Windows или сбой — None."""
    counters = _process_memory_counters()
    return None if counters is None else int(counters.WorkingSetSize)


def _process_memory_counters() -> _ProcessMemoryCounters | None:
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return counters
    except Exception:
        return None


def available_physical_bytes() -> int | None:
    """Сколько оперативной памяти свободно или отдана кэшу (её можно взять сразу). Не Windows или сбой — None."""
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullAvailPhys)
    except Exception:
        return None


def _enable_increase_working_set_privilege() -> bool:
    """Включает право «увеличивать рабочий набор процесса» в токене (обычно оно уже есть, но выключено)."""

    class _Luid(ctypes.Structure):
        _fields_ = [("Low", ctypes.c_ulong), ("High", ctypes.c_long)]

    class _Privileges(ctypes.Structure):
        _fields_ = [("Count", ctypes.c_ulong), ("Luid", _Luid), ("Attributes", ctypes.c_ulong)]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        advapi32.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
        advapi32.AdjustTokenPrivileges.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(_Privileges),
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        token = ctypes.c_void_p()
        # TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x28, ctypes.byref(token)):
            return False
        try:
            luid = _Luid()
            if not advapi32.LookupPrivilegeValueW(None, "SeIncreaseWorkingSetPrivilege", ctypes.byref(luid)):
                return False
            privileges = _Privileges(1, luid, 0x2)  # SE_PRIVILEGE_ENABLED
            advapi32.AdjustTokenPrivileges(token, 0, ctypes.byref(privileges), 0, None, None)
            return ctypes.get_last_error() == 0
        finally:
            kernel32.CloseHandle(token)
    except Exception:
        return False


def _set_working_set_limits(minimum: int, maximum: int, flags: int) -> tuple[bool, int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    call = kernel32.SetProcessWorkingSetSizeEx
    call.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_ulong]
    process = kernel32.GetCurrentProcess()
    ctypes.set_last_error(0)
    ok = bool(call(process, minimum, maximum, flags))
    return ok, ctypes.get_last_error()


def hold_working_set(size_bytes: int) -> WorkingSetHold | None:
    """Просит Windows не отбирать у программы первые ``size_bytes`` рабочего набора («жёсткий минимум»).

    Верхний предел жёстким не делается: программа может расти как раньше.
    Не Windows или нечего удерживать — None; отказ системы виден в ``ok``/``error``.
    """
    if sys.platform != "win32" or size_bytes <= 0:
        return None
    flags = _QUOTA_LIMITS_HARDWS_MIN_ENABLE | _QUOTA_LIMITS_HARDWS_MAX_DISABLE
    maximum = max(size_bytes * 4, 1024 * _MB)
    try:
        ok, error = _set_working_set_limits(size_bytes, maximum, flags)
        if not ok and error == _ERROR_PRIVILEGE_NOT_HELD and _enable_increase_working_set_privilege():
            ok, error = _set_working_set_limits(size_bytes, maximum, flags)
    except Exception:
        return None
    return WorkingSetHold(bytes=size_bytes, ok=ok, error=0 if ok else error)


def release_working_set_hold() -> bool:
    """Снимает жёсткий минимум: система снова вправе отбирать страницы по обычным правилам.

    Минимум возвращается к малому числу (не «минус единицы»), поэтому
    страницы не выгружаются: просто больше никто их не держит.
    """
    if sys.platform != "win32":
        return False
    flags = _QUOTA_LIMITS_HARDWS_MIN_DISABLE | _QUOTA_LIMITS_HARDWS_MAX_DISABLE
    try:
        ok, _error = _set_working_set_limits(RELEASED_MIN_BYTES, 1024 * _MB, flags)
    except Exception:
        return False
    return ok


__all__ = [
    "HOLD_MARGIN_BYTES",
    "HOLD_MAX_BYTES",
    "HOLD_MIN_BYTES",
    "HOLD_SPARE_BYTES",
    "PAGE_SIZE",
    "PrefetchResult",
    "WorkingSetHold",
    "WorkingSetSnapshot",
    "available_physical_bytes",
    "current_working_set_bytes",
    "hold_size_for",
    "hold_working_set",
    "prefetch_working_set",
    "process_page_faults",
    "release_working_set_hold",
    "snapshot_working_set",
]
