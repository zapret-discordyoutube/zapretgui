"""DNS-запросы через dnsapi.dll: настоящий таймаут, отмена, без nslookup.

Зачем свой модуль
-----------------
``socket.getaddrinfo`` не даёт ни таймаута, ни отмены и молча подмешивает
кэш и файл hosts. Для диагностики нужен ответ именно DNS-сервера, поэтому
запрос идёт через ``DnsQueryEx`` в асинхронном режиме: вызывающая сторона
ждёт ответ с дедлайном, а по истечении времени или по кнопке «Стоп» запрос
снимается через ``DnsCancelQuery``. Поток при этом не зависает.

Файл hosts читается отдельно (``hosts_file_ipv4``): программа должна уметь
сказать «адрес задан в hosts», а не путать это с подменой провайдером.
"""

from __future__ import annotations

import ctypes
import itertools
import os
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "DNS_STATUS_NAME_ERROR",
    "DNS_STATUS_NO_RECORDS",
    "DnsAnswer",
    "hosts_file_ipv4",
    "is_dns_api_available",
    "query_ipv4",
    "system_dns_servers",
]


DNS_TYPE_A = 1
DNS_TYPE_CNAME = 5

# Только сеть: без кэша резолвера и без файла hosts.
DNS_QUERY_WIRE_ONLY = 0x00000100

DNS_QUERY_REQUEST_VERSION1 = 1
DNS_REQUEST_PENDING = 9506
DNS_FREE_RECORD_LIST = 1
DNS_SECTION_ANSWER = 1
DNS_CONFIG_DNS_SERVER_LIST = 6

ERROR_SUCCESS = 0
ERROR_MORE_DATA = 234
ERROR_CANCELLED = 1223
ERROR_TIMEOUT = 1460
DNS_STATUS_SERVER_FAILURE = 9002
DNS_STATUS_NAME_ERROR = 9003
DNS_STATUS_REFUSED = 9005
DNS_STATUS_NO_RECORDS = 9501

_STATUS_TEXT = {
    ERROR_CANCELLED: "запрос отменён",
    ERROR_TIMEOUT: "DNS-сервер не ответил вовремя",
    DNS_STATUS_SERVER_FAILURE: "DNS-сервер вернул ошибку (SERVFAIL)",
    DNS_STATUS_NAME_ERROR: "DNS-сервер ответил, что такого домена нет",
    DNS_STATUS_REFUSED: "DNS-сервер отказался отвечать",
    DNS_STATUS_NO_RECORDS: "у домена нет IPv4-адресов",
}


@dataclass(frozen=True, slots=True)
class DnsAnswer:
    """Ответ DNS: адреса IPv4 из секции ответа и код результата Windows."""

    ips: tuple[str, ...] = ()
    cnames: tuple[str, ...] = ()
    status: int = 0
    detail: str = ""
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.ips)


def status_text(status: int) -> str:
    return _STATUS_TEXT.get(int(status), f"ошибка DNS {int(status)}")


# ---------------------------------------------------------------------------
# Структуры dnsapi
# ---------------------------------------------------------------------------


class _DnsRecordData(ctypes.Union):
    _fields_ = [
        ("A", ctypes.c_uint32),
        ("pNameHost", ctypes.c_wchar_p),
        ("_reserved", ctypes.c_byte * 64),
    ]


class DNS_RECORDW(ctypes.Structure):
    pass


DNS_RECORDW._fields_ = [
    ("pNext", ctypes.POINTER(DNS_RECORDW)),
    ("pName", ctypes.c_wchar_p),
    ("wType", ctypes.c_uint16),
    ("wDataLength", ctypes.c_uint16),
    ("Flags", ctypes.c_uint32),
    ("dwTtl", ctypes.c_uint32),
    ("dwReserved", ctypes.c_uint32),
    ("Data", _DnsRecordData),
]


class DNS_QUERY_REQUEST(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_ulong),
        ("QueryName", ctypes.c_wchar_p),
        ("QueryType", ctypes.c_uint16),
        ("QueryOptions", ctypes.c_uint64),
        ("pDnsServerList", ctypes.c_void_p),
        ("InterfaceIndex", ctypes.c_ulong),
        ("pQueryCompletionCallback", ctypes.c_void_p),
        ("pQueryContext", ctypes.c_void_p),
    ]


class DNS_QUERY_RESULT(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_ulong),
        ("QueryStatus", ctypes.c_long),
        ("QueryOptions", ctypes.c_uint64),
        ("pQueryRecords", ctypes.POINTER(DNS_RECORDW)),
        ("Reserved", ctypes.c_void_p),
    ]


class DNS_QUERY_CANCEL(ctypes.Structure):
    _fields_ = [("Reserved", ctypes.c_char * 32)]


if sys.platform == "win32":
    _WINAPI = ctypes.WINFUNCTYPE
    _dnsapi = ctypes.WinDLL("dnsapi.dll", use_last_error=True)

    _DnsQueryEx = _dnsapi.DnsQueryEx
    _DnsQueryEx.argtypes = [
        ctypes.POINTER(DNS_QUERY_REQUEST),
        ctypes.POINTER(DNS_QUERY_RESULT),
        ctypes.POINTER(DNS_QUERY_CANCEL),
    ]
    _DnsQueryEx.restype = ctypes.c_long

    _DnsCancelQuery = _dnsapi.DnsCancelQuery
    _DnsCancelQuery.argtypes = [ctypes.POINTER(DNS_QUERY_CANCEL)]
    _DnsCancelQuery.restype = ctypes.c_long

    _DnsFree = _dnsapi.DnsFree
    _DnsFree.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _DnsFree.restype = None

    _DnsQueryConfig = _dnsapi.DnsQueryConfig
    _DnsQueryConfig.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    _DnsQueryConfig.restype = ctypes.c_long
else:  # pragma: no cover - модуль импортируется и на Linux в тестах
    _WINAPI = ctypes.CFUNCTYPE
    _DnsQueryEx = None
    _DnsCancelQuery = None
    _DnsFree = None
    _DnsQueryConfig = None


def is_dns_api_available() -> bool:
    return _DnsQueryEx is not None


# ---------------------------------------------------------------------------
# Асинхронный запрос
# ---------------------------------------------------------------------------


class _PendingQuery:
    """Всё, что Windows держит по указателю, пока запрос не завершился."""

    __slots__ = ("request", "result", "cancel", "done", "abandoned")

    def __init__(self) -> None:
        self.request = DNS_QUERY_REQUEST()
        self.result = DNS_QUERY_RESULT()
        self.cancel = DNS_QUERY_CANCEL()
        self.done = threading.Event()
        self.abandoned = False


_pending_lock = threading.Lock()
_pending: dict[int, _PendingQuery] = {}
_pending_ids = itertools.count(1)


def _free_records(pending: _PendingQuery) -> None:
    records = pending.result.pQueryRecords
    if records and _DnsFree is not None:
        _DnsFree(ctypes.cast(records, ctypes.c_void_p), DNS_FREE_RECORD_LIST)
    pending.result.pQueryRecords = None


def _on_query_complete(context, _result) -> None:
    key = int(context or 0)
    with _pending_lock:
        pending = _pending.get(key)
        if pending is None:
            return
        if pending.abandoned:
            # Ожидающий уже ушёл по таймауту: память освобождаем сами.
            _pending.pop(key, None)
            _free_records(pending)
            return
        pending.done.set()


# Один колбэк на весь процесс: он живёт вечно, поэтому Windows никогда не
# вызовет уже собранный сборщиком мусора ctypes-объект.
_COMPLETION_ROUTINE = _WINAPI(None, ctypes.c_void_p, ctypes.c_void_p)(_on_query_complete)


def _read_records(result: DNS_QUERY_RESULT) -> tuple[tuple[str, ...], tuple[str, ...]]:
    ips: list[str] = []
    cnames: list[str] = []
    record = result.pQueryRecords
    visited = 0
    while record and visited < 256:
        visited += 1
        item = record.contents
        section = int(item.Flags) & 0x3
        if section == DNS_SECTION_ANSWER:
            if item.wType == DNS_TYPE_A:
                ip = socket.inet_ntoa(struct.pack("<I", int(item.Data.A)))
                if ip not in ips:
                    ips.append(ip)
            elif item.wType == DNS_TYPE_CNAME and item.Data.pNameHost:
                cname = str(item.Data.pNameHost).rstrip(".").lower()
                if cname and cname not in cnames:
                    cnames.append(cname)
        record = item.pNext
    return tuple(ips), tuple(cnames)


def query_ipv4(
    name: str,
    *,
    timeout: float,
    cancelled: Callable[[], bool] | None = None,
) -> DnsAnswer:
    """Спрашивает A-записи у DNS-серверов системы, минуя кэш и hosts.

    Возвращает не позже ``timeout`` секунд. ``cancelled`` опрашивается
    каждые 50 мс, чтобы кнопка «Стоп» снимала запрос сразу.
    """
    started = time.perf_counter()
    host = str(name or "").strip().rstrip(".")
    if not host:
        return DnsAnswer(status=DNS_STATUS_NAME_ERROR, detail="пустое имя")
    if _DnsQueryEx is None:
        return DnsAnswer(status=-1, detail="DNS API Windows недоступен")

    key = next(_pending_ids)
    pending = _PendingQuery()
    request = pending.request
    request.Version = DNS_QUERY_REQUEST_VERSION1
    request.QueryName = host
    request.QueryType = DNS_TYPE_A
    request.QueryOptions = DNS_QUERY_WIRE_ONLY
    request.pDnsServerList = None
    request.InterfaceIndex = 0
    request.pQueryCompletionCallback = ctypes.cast(_COMPLETION_ROUTINE, ctypes.c_void_p).value
    request.pQueryContext = key
    pending.result.Version = DNS_QUERY_REQUEST_VERSION1

    with _pending_lock:
        _pending[key] = pending

    status = int(
        _DnsQueryEx(
            ctypes.byref(request),
            ctypes.byref(pending.result),
            ctypes.byref(pending.cancel),
        )
    )

    if status != DNS_REQUEST_PENDING:
        # Запрос завершился сразу, колбэк вызван не будет.
        with _pending_lock:
            _pending.pop(key, None)
        return _finish(pending, status, started)

    deadline = started + max(0.05, float(timeout))
    stop_reason = ""
    while not pending.done.wait(0.05):
        if cancelled is not None and cancelled():
            stop_reason = "cancelled"
            break
        if time.perf_counter() >= deadline:
            stop_reason = "timeout"
            break

    if stop_reason:
        _DnsCancelQuery(ctypes.byref(pending.cancel))
        code = ERROR_CANCELLED if stop_reason == "cancelled" else ERROR_TIMEOUT
        # После отмены Windows обязательно вызывает колбэк; ждём его, чтобы
        # освободить записи здесь. Если колбэк задержался, память освободит он.
        pending.done.wait(1.0)
        with _pending_lock:
            if pending.done.is_set():
                _pending.pop(key, None)
                _free_records(pending)
            else:
                pending.abandoned = True
        return DnsAnswer(
            status=code,
            detail=status_text(code),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    with _pending_lock:
        _pending.pop(key, None)
    return _finish(pending, int(pending.result.QueryStatus), started)


def _finish(pending: _PendingQuery, status: int, started: float) -> DnsAnswer:
    try:
        ips, cnames = _read_records(pending.result) if status == ERROR_SUCCESS else ((), ())
    finally:
        _free_records(pending)
    elapsed = (time.perf_counter() - started) * 1000
    if status == ERROR_SUCCESS and not ips:
        status = DNS_STATUS_NO_RECORDS
    return DnsAnswer(
        ips=ips,
        cnames=cnames,
        status=status,
        detail="" if ips else status_text(status),
        elapsed_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Настройки системы
# ---------------------------------------------------------------------------


def system_dns_servers() -> tuple[str, ...]:
    """IPv4-адреса DNS-серверов, которыми сейчас пользуется Windows."""
    if _DnsQueryConfig is None:
        return ()
    size = ctypes.c_ulong(1024)
    for _attempt in range(3):
        buffer = ctypes.create_string_buffer(int(size.value))
        status = int(
            _DnsQueryConfig(
                DNS_CONFIG_DNS_SERVER_LIST,
                0,
                None,
                None,
                ctypes.cast(buffer, ctypes.c_void_p),
                ctypes.byref(size),
            )
        )
        if status == ERROR_MORE_DATA:
            continue
        if status != ERROR_SUCCESS or int(size.value) < 4:
            return ()
        raw = buffer.raw[: int(size.value)]
        count = struct.unpack_from("<I", raw, 0)[0]
        servers: list[str] = []
        for index in range(min(count, (len(raw) - 4) // 4)):
            ip = socket.inet_ntoa(raw[4 + index * 4 : 8 + index * 4])
            if ip not in servers:
                servers.append(ip)
        return tuple(servers)
    return ()


def _hosts_path() -> str:
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    return os.path.join(root, "System32", "drivers", "etc", "hosts")


def hosts_file_ipv4(name: str, *, path: str | None = None) -> tuple[str, ...]:
    """IPv4-адреса, которые файл hosts задаёт для имени (без учёта регистра)."""
    host = str(name or "").strip().rstrip(".").lower()
    if not host:
        return ()
    try:
        with open(path or _hosts_path(), "r", encoding="utf-8", errors="ignore") as handle:
            lines = handle.readlines()
    except OSError:
        return ()

    found: list[str] = []
    for line in lines:
        content = line.split("#", 1)[0].split()
        if len(content) < 2:
            continue
        address, names = content[0], content[1:]
        try:
            socket.inet_aton(address)
        except OSError:
            continue
        if address.count(".") != 3:
            continue
        if host in (item.rstrip(".").lower() for item in names) and address not in found:
            found.append(address)
    return tuple(found)
