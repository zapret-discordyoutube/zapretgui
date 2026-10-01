"""Типизированные привязки WinAPI для управления движком и драйвером.

Правила этого модуля — каждое закрывает уже случавшуюся поломку:

- Библиотеки грузятся приватно (``ctypes.WinDLL``), а не через общий
  ``ctypes.windll``: у общего объекта прототипы функций делят все модули
  процесса и могут молча перезаписать друг друга.
- У КАЖДОЙ функции заданы ``argtypes`` и ``restype``. Без ``argtypes``
  ctypes укладывает число в 32 бита, а хэндлы службы на 64-битной Windows
  лежат выше 4 ГБ — вызов ``CloseServiceHandle`` падал с ``int too long to
  convert``, хэндл оставался открытым, и Windows не могла убрать службу
  драйвера, помеченную на удаление.
- ``use_last_error=True``: код ошибки читается сразу после вызова, в том же
  потоке, и не затирается кодом интерпретатора.
- Хэндлы закрываются в ``finally`` функцией, которая не бросает исключений:
  сбой закрытия не должен подменять результат операции.

Типы объявлены с фиксированной шириной, а не через ``ctypes.wintypes``:
на Linux ``wintypes.DWORD`` занимает 8 байт, и проверка раскладки структур
в тестах была бы ложной.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p
LPCWSTR = ctypes.c_wchar_p
LPWSTR = ctypes.c_wchar_p

# --- Права доступа к процессу ---------------------------------------------
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000

# --- Ожидание ---------------------------------------------------------------
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
MAXIMUM_WAIT_OBJECTS = 64

# --- Диспетчер служб (SCM) --------------------------------------------------
SC_MANAGER_CONNECT = 0x0001
SERVICE_QUERY_CONFIG = 0x0001
SERVICE_QUERY_STATUS = 0x0004
SERVICE_STOP = 0x0020
SERVICE_DELETE = 0x00010000

SERVICE_STOPPED = 1
SERVICE_START_PENDING = 2
SERVICE_STOP_PENDING = 3
SERVICE_RUNNING = 4

SERVICE_BOOT_START = 0
SERVICE_SYSTEM_START = 1
SERVICE_AUTO_START = 2
SERVICE_DEMAND_START = 3
SERVICE_DISABLED = 4

SERVICE_CONTROL_STOP = 1

# --- Коды ошибок ------------------------------------------------------------
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_SERVICE_DOES_NOT_EXIST = 1060
ERROR_SERVICE_CANNOT_ACCEPT_CTRL = 1061
ERROR_SERVICE_NOT_ACTIVE = 1062
ERROR_SERVICE_MARKED_FOR_DELETE = 1072

MAX_PROCESS_PATH = 32768


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", DWORD),
        ("dwCurrentState", DWORD),
        ("dwControlsAccepted", DWORD),
        ("dwWin32ExitCode", DWORD),
        ("dwServiceSpecificExitCode", DWORD),
        ("dwCheckPoint", DWORD),
        ("dwWaitHint", DWORD),
    ]


class QUERY_SERVICE_CONFIGW(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", DWORD),
        ("dwStartType", DWORD),
        ("dwErrorControl", DWORD),
        ("lpBinaryPathName", LPWSTR),
        ("lpLoadOrderGroup", LPWSTR),
        ("dwTagId", DWORD),
        ("lpDependencies", ctypes.c_void_p),
        ("lpServiceStartName", LPWSTR),
        ("lpDisplayName", LPWSTR),
    ]


class WinApiError(OSError):
    """Отказ вызова WinAPI с кодом ошибки Windows."""

    def __init__(self, function: str, code: int):
        self.function = str(function)
        self.code = int(code)
        super().__init__(self.code, f"{self.function} failed with Windows error {self.code}")


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    """Снимок записи службы: только то, что сообщил сам диспетчер служб."""

    name: str
    state: int
    start_type: Optional[int]
    image_path: str


# Сигнатуры в одном месте: тест проверяет, что прототип задан у каждой функции.
_KERNEL32_SIGNATURES: dict[str, tuple[object, list]] = {
    "OpenProcess": (HANDLE, [DWORD, BOOL, DWORD]),
    "CloseHandle": (BOOL, [HANDLE]),
    "TerminateProcess": (BOOL, [HANDLE, ctypes.c_uint32]),
    "GetExitCodeProcess": (BOOL, [HANDLE, ctypes.POINTER(DWORD)]),
    "WaitForSingleObject": (DWORD, [HANDLE, DWORD]),
    "WaitForMultipleObjects": (DWORD, [DWORD, ctypes.POINTER(HANDLE), BOOL, DWORD]),
    "QueryFullProcessImageNameW": (BOOL, [HANDLE, DWORD, LPWSTR, ctypes.POINTER(DWORD)]),
}

_ADVAPI32_SIGNATURES: dict[str, tuple[object, list]] = {
    "OpenSCManagerW": (HANDLE, [LPCWSTR, LPCWSTR, DWORD]),
    "OpenServiceW": (HANDLE, [HANDLE, LPCWSTR, DWORD]),
    "CloseServiceHandle": (BOOL, [HANDLE]),
    "QueryServiceStatus": (BOOL, [HANDLE, ctypes.POINTER(SERVICE_STATUS)]),
    "QueryServiceConfigW": (BOOL, [HANDLE, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)]),
    "ControlService": (BOOL, [HANDLE, DWORD, ctypes.POINTER(SERVICE_STATUS)]),
    "DeleteService": (BOOL, [HANDLE]),
}


def _bind(library, signatures: dict[str, tuple[object, list]]) -> None:
    for name, (restype, argtypes) in signatures.items():
        function = getattr(library, name)
        function.restype = restype
        function.argtypes = argtypes


if hasattr(ctypes, "WinDLL"):
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _bind(_kernel32, _KERNEL32_SIGNATURES)
    _bind(_advapi32, _ADVAPI32_SIGNATURES)
else:  # pragma: no cover - импорт на не-Windows нужен тестам и инструментам
    _kernel32 = None
    _advapi32 = None


def is_available() -> bool:
    return _kernel32 is not None and _advapi32 is not None


def _require() -> None:
    if not is_available():
        raise WinApiError("WinAPI", 0)


def _last_error() -> int:
    return int(ctypes.get_last_error() or 0)


def _fail(function: str) -> WinApiError:
    return WinApiError(function, _last_error())


# ---------------------------------------------------------------------------
#  Процессы
# ---------------------------------------------------------------------------

def open_process(pid: int, access: int) -> int:
    """Открывает процесс. Бросает WinApiError (87 — процесса уже нет)."""
    _require()
    ctypes.set_last_error(0)
    handle = _kernel32.OpenProcess(int(access), 0, int(pid))
    if not handle:
        raise _fail("OpenProcess")
    return int(handle)


def close_handle(handle: Optional[int]) -> None:
    """Закрывает хэндл ядра. Никогда не бросает исключений."""
    if not handle or _kernel32 is None:
        return
    try:
        _kernel32.CloseHandle(int(handle))
    except Exception:
        pass


def terminate_process(handle: int, exit_code: int) -> None:
    _require()
    ctypes.set_last_error(0)
    if not _kernel32.TerminateProcess(int(handle), int(exit_code) & 0xFFFFFFFF):
        raise _fail("TerminateProcess")


def get_exit_code(handle: int) -> int:
    _require()
    code = DWORD(0)
    ctypes.set_last_error(0)
    if not _kernel32.GetExitCodeProcess(int(handle), ctypes.byref(code)):
        raise _fail("GetExitCodeProcess")
    return int(code.value)


def _timeout_ms(timeout_seconds: float) -> int:
    seconds = max(0.0, float(timeout_seconds))
    # 0xFFFFFFFF означает «ждать вечно» — в этом слое такого ожидания нет.
    return min(int(seconds * 1000), 0xFFFFFFFE)


def wait_for_handle(handle: int, timeout_seconds: float) -> bool:
    """True, если объект стал сигнальным (процесс вышел) за отведённое время."""
    _require()
    ctypes.set_last_error(0)
    result = int(_kernel32.WaitForSingleObject(int(handle), _timeout_ms(timeout_seconds)))
    if result == WAIT_OBJECT_0:
        return True
    if result == WAIT_TIMEOUT:
        return False
    raise _fail("WaitForSingleObject")


def wait_for_all_handles(handles: Sequence[int], timeout_seconds: float) -> bool:
    """True, если ВСЕ объекты стали сигнальными за отведённое время."""
    _require()
    items = [int(handle) for handle in handles if handle]
    if not items:
        return True
    if len(items) > MAXIMUM_WAIT_OBJECTS:
        raise WinApiError("WaitForMultipleObjects", ERROR_INVALID_PARAMETER)
    array = (HANDLE * len(items))(*items)
    ctypes.set_last_error(0)
    result = int(_kernel32.WaitForMultipleObjects(len(items), array, 1, _timeout_ms(timeout_seconds)))
    if WAIT_OBJECT_0 <= result < WAIT_OBJECT_0 + len(items):
        return True
    if result == WAIT_TIMEOUT:
        return False
    raise _fail("WaitForMultipleObjects")


def query_image_path(handle: int) -> str:
    """Полный путь к exe процесса по уже открытому хэндлу."""
    _require()
    buffer = ctypes.create_unicode_buffer(MAX_PROCESS_PATH)
    size = DWORD(len(buffer))
    ctypes.set_last_error(0)
    if not _kernel32.QueryFullProcessImageNameW(int(handle), 0, buffer, ctypes.byref(size)):
        raise _fail("QueryFullProcessImageNameW")
    return str(buffer.value[: size.value] or buffer.value)


# ---------------------------------------------------------------------------
#  Диспетчер служб
# ---------------------------------------------------------------------------

def _close_service_handle(handle: Optional[int]) -> None:
    """Закрывает хэндл службы. Никогда не бросает исключений.

    Незакрытый хэндл — не мелочь: пока он открыт в любом процессе, Windows не
    убирает запись службы, помеченную на удаление.
    """
    if not handle or _advapi32 is None:
        return
    try:
        _advapi32.CloseServiceHandle(int(handle))
    except Exception:
        pass


def _open_scm() -> int:
    ctypes.set_last_error(0)
    scm = _advapi32.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    if not scm:
        raise _fail("OpenSCManagerW")
    return int(scm)


def _open_service(scm: int, name: str, access: int) -> Optional[int]:
    """Хэндл службы или None, если службы нет (1060). Прочее — WinApiError."""
    ctypes.set_last_error(0)
    service = _advapi32.OpenServiceW(int(scm), str(name), int(access))
    if service:
        return int(service)
    code = _last_error()
    if code == ERROR_SERVICE_DOES_NOT_EXIST:
        return None
    raise WinApiError("OpenServiceW", code)


def _read_service_config(service: int) -> tuple[Optional[int], str]:
    needed = DWORD(0)
    ctypes.set_last_error(0)
    if _advapi32.QueryServiceConfigW(int(service), None, 0, ctypes.byref(needed)):
        return None, ""
    if _last_error() != ERROR_INSUFFICIENT_BUFFER or not needed.value:
        raise _fail("QueryServiceConfigW")

    buffer = ctypes.create_string_buffer(int(needed.value))
    ctypes.set_last_error(0)
    if not _advapi32.QueryServiceConfigW(int(service), buffer, needed.value, ctypes.byref(needed)):
        raise _fail("QueryServiceConfigW")
    config = ctypes.cast(buffer, ctypes.POINTER(QUERY_SERVICE_CONFIGW)).contents
    return int(config.dwStartType), str(config.lpBinaryPathName or "")


def query_service(name: str) -> Optional[ServiceInfo]:
    """Состояние службы или None, если её нет.

    «Службы нет» и «не удалось узнать» — разные ответы: при любой ошибке,
    кроме 1060, бросается WinApiError. Старый код считал оба случая
    отсутствием службы и на этом основании правил реестр.
    """
    _require()
    scm = _open_scm()
    try:
        service = _open_service(scm, name, SERVICE_QUERY_STATUS | SERVICE_QUERY_CONFIG)
        if service is None:
            return None
        try:
            status = SERVICE_STATUS()
            ctypes.set_last_error(0)
            if not _advapi32.QueryServiceStatus(service, ctypes.byref(status)):
                raise _fail("QueryServiceStatus")
            try:
                start_type, image_path = _read_service_config(service)
            except WinApiError:
                # Запись уже помечена на удаление и может не отдавать конфиг;
                # состояние при этом известно, его и возвращаем.
                start_type, image_path = None, ""
            return ServiceInfo(
                name=str(name),
                state=int(status.dwCurrentState),
                start_type=start_type,
                image_path=image_path,
            )
        finally:
            _close_service_handle(service)
    finally:
        _close_service_handle(scm)


def send_service_stop(name: str) -> Optional[int]:
    """Просит службу остановиться. Возвращает её состояние сразу после запроса.

    None — службы уже нет. ``SERVICE_STOPPED`` — уже остановлена (1062).
    """
    _require()
    scm = _open_scm()
    try:
        service = _open_service(scm, name, SERVICE_STOP | SERVICE_QUERY_STATUS)
        if service is None:
            return None
        try:
            status = SERVICE_STATUS()
            ctypes.set_last_error(0)
            if _advapi32.ControlService(service, SERVICE_CONTROL_STOP, ctypes.byref(status)):
                return int(status.dwCurrentState)
            code = _last_error()
            if code == ERROR_SERVICE_NOT_ACTIVE:
                return SERVICE_STOPPED
            if code == ERROR_SERVICE_CANNOT_ACCEPT_CTRL:
                # Служба уже в переходном состоянии — узнаём, в каком именно.
                ctypes.set_last_error(0)
                if _advapi32.QueryServiceStatus(service, ctypes.byref(status)):
                    return int(status.dwCurrentState)
            raise WinApiError("ControlService", code)
        finally:
            _close_service_handle(service)
    finally:
        _close_service_handle(scm)


def delete_service(name: str) -> bool:
    """Помечает службу на удаление через диспетчер служб. False — её уже нет.

    Это штатный способ убрать остановленную запись: диспетчер сам удалит её,
    как только закроется последний хэндл. Реестр при этом не трогается.
    """
    _require()
    scm = _open_scm()
    try:
        service = _open_service(scm, name, SERVICE_DELETE)
        if service is None:
            return False
        try:
            ctypes.set_last_error(0)
            if _advapi32.DeleteService(service):
                return True
            code = _last_error()
            if code == ERROR_SERVICE_MARKED_FOR_DELETE:
                return True
            raise WinApiError("DeleteService", code)
        finally:
            _close_service_handle(service)
    finally:
        _close_service_handle(scm)


def signature_tables() -> Iterable[tuple[str, dict[str, tuple[object, list]]]]:
    """Таблицы прототипов для проверки «у каждой функции заданы типы»."""
    return (("kernel32", _KERNEL32_SIGNATURES), ("advapi32", _ADVAPI32_SIGNATURES))
