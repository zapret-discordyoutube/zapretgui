"""Вызовы Windows для сброса сети: кэши, прокси и каталог Winsock.

Только тонкая обёртка над WinAPI, без решений «что чинить». Решения живут
в windows_features.internet_cleanup, а этот модуль читает и меняет ровно то,
о чём его попросили.

- Кэш адресов соседей и маршрутов: FlushIpNetTable2 / FlushIpPathTable.
- Прокси WinHTTP (общий для служб Windows):
  WinHttpGetDefaultProxyConfiguration / WinHttpSetDefaultProxyConfiguration.
- Системный прокси (тот, что в «Параметры → Прокси», им пользуются браузеры):
  InternetQueryOptionW / InternetSetOptionW.
- Каталог Winsock: WSCEnumProtocols / WSCDeinstallProvider и их пары с
  суффиксом 32 — у 64-битной Windows отдельный каталог для 32-битных программ.

Библиотеки Windows подгружаются лениво, поэтому модуль импортируется и на
других системах.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, Structure, Union, byref, c_int, c_ubyte, c_ulong, c_ushort, c_void_p, c_wchar, c_wchar_p, sizeof
from dataclasses import dataclass
from functools import lru_cache

AF_UNSPEC = 0
ERROR_SUCCESS = 0
ERROR_ACCESS_DENIED = 5
SOCKET_ERROR = -1
WSAEACCES = 10013
WSAENOBUFS = 10055

WINHTTP_ACCESS_TYPE_NO_PROXY = 1

INTERNET_OPTION_REFRESH = 37
INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_OPTION_PER_CONNECTION_OPTION = 75
INTERNET_PER_CONN_FLAGS = 1
INTERNET_PER_CONN_PROXY_SERVER = 2
PROXY_TYPE_DIRECT = 0x1
PROXY_TYPE_PROXY = 0x2

# ProtocolChain.ChainLen: 1 — обычный поставщик самой Windows или драйвера,
# 0 — надстройка (LSP), больше 1 — цепочка, проходящая через надстройку.
BASE_PROTOCOL = 1


class NetworkWinApiError(OSError):
    """Windows вернула ошибку; текст уже понятен человеку."""


class GUID(Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", c_ushort), ("Data3", c_ushort), ("Data4", c_ubyte * 8)]


class WSAPROTOCOLCHAIN(Structure):
    _fields_ = [("ChainLen", c_int), ("ChainEntries", c_ulong * 7)]


class WSAPROTOCOL_INFOW(Structure):
    _fields_ = [
        ("dwServiceFlags1", c_ulong),
        ("dwServiceFlags2", c_ulong),
        ("dwServiceFlags3", c_ulong),
        ("dwServiceFlags4", c_ulong),
        ("dwProviderFlags", c_ulong),
        ("ProviderId", GUID),
        ("dwCatalogEntryId", c_ulong),
        ("ProtocolChain", WSAPROTOCOLCHAIN),
        ("iVersion", c_int),
        ("iAddressFamily", c_int),
        ("iMaxSockAddr", c_int),
        ("iMinSockAddr", c_int),
        ("iSocketType", c_int),
        ("iProtocol", c_int),
        ("iProtocolMaxOffset", c_int),
        ("iNetworkByteOrder", c_int),
        ("iSecurityScheme", c_int),
        ("dwMessageSize", c_ulong),
        ("dwProviderReserved", c_ulong),
        ("szProtocol", c_wchar * 256),
    ]


class WINHTTP_PROXY_INFO(Structure):
    _fields_ = [("dwAccessType", c_ulong), ("lpszProxy", c_void_p), ("lpszProxyBypass", c_void_p)]


class _PER_CONN_VALUE(Union):
    # В настоящем объединении есть ещё FILETIME: он того же размера, что указатель.
    _fields_ = [("dwValue", c_ulong), ("pszValue", c_void_p), ("ftValue", c_ulong * 2)]


class INTERNET_PER_CONN_OPTIONW(Structure):
    _fields_ = [("dwOption", c_ulong), ("Value", _PER_CONN_VALUE)]


class INTERNET_PER_CONN_OPTION_LISTW(Structure):
    _fields_ = [
        ("dwSize", c_ulong),
        ("pszConnection", c_wchar_p),
        ("dwOptionCount", c_ulong),
        ("dwOptionError", c_ulong),
        ("pOptions", POINTER(INTERNET_PER_CONN_OPTIONW)),
    ]


@dataclass(frozen=True, slots=True)
class SystemProxy:
    """Системный прокси: включён ли и какой адрес записан (адрес хранится и у выключенного)."""

    enabled: bool
    server: str


@dataclass(frozen=True, slots=True)
class WinsockProvider:
    """Запись каталога Winsock, добавленная посторонней программой."""

    name: str
    provider_id: bytes
    for_32bit_apps: bool


@lru_cache(maxsize=1)
def _iphlpapi():
    return ctypes.WinDLL("iphlpapi")


@lru_cache(maxsize=1)
def _winhttp():
    return ctypes.WinDLL("winhttp", use_last_error=True)


@lru_cache(maxsize=1)
def _wininet():
    return ctypes.WinDLL("wininet", use_last_error=True)


@lru_cache(maxsize=1)
def _ws2_32():
    return ctypes.WinDLL("ws2_32")


@lru_cache(maxsize=1)
def _kernel32():
    return ctypes.WinDLL("kernel32")


def _error_text(api: str, code: int) -> str:
    if int(code) in (ERROR_ACCESS_DENIED, WSAEACCES):
        return "нужны права администратора"
    detail = ""
    formatter = getattr(ctypes, "FormatError", None)
    if formatter is not None:
        try:
            detail = str(formatter(int(code)) or "").strip()
        except Exception:
            detail = ""
    return f"{api}: ошибка Windows {code}" + (f" — {detail}" if detail else "")


def _last_error(api: str) -> NetworkWinApiError:
    return NetworkWinApiError(_error_text(api, ctypes.get_last_error()))


def _take_global_string(pointer: int | None) -> str:
    """Читает строку, которую Windows выделила через GlobalAlloc, и освобождает её."""
    if not pointer:
        return ""
    try:
        return ctypes.wstring_at(pointer)
    finally:
        free = _kernel32().GlobalFree
        free.argtypes = [c_void_p]
        free.restype = c_void_p
        free(pointer)


# ── кэш адресов и маршрутов ───────────────────────────────────────────────


def flush_neighbor_and_path_caches() -> None:
    """Забывает, какое устройство стоит за каким адресом и какой путь выбран до каждого сайта.

    То же, что `netsh interface ip delete arpcache` и `delete destinationcache`.
    Windows сразу узнаёт всё заново, настройки не затрагиваются.
    """
    api = _iphlpapi()
    result = int(api.FlushIpNetTable2(c_ushort(AF_UNSPEC), c_ulong(0)))
    if result != ERROR_SUCCESS:
        raise NetworkWinApiError(_error_text("FlushIpNetTable2", result))
    result = int(api.FlushIpPathTable(c_ushort(AF_UNSPEC)))
    if result != ERROR_SUCCESS:
        raise NetworkWinApiError(_error_text("FlushIpPathTable", result))


# ── прокси WinHTTP ────────────────────────────────────────────────────────


def read_winhttp_proxy() -> str:
    """Адрес прокси WinHTTP или пустая строка при прямом доступе."""
    api = _winhttp()
    api.WinHttpGetDefaultProxyConfiguration.argtypes = [POINTER(WINHTTP_PROXY_INFO)]
    api.WinHttpGetDefaultProxyConfiguration.restype = c_int
    info = WINHTTP_PROXY_INFO()
    if not api.WinHttpGetDefaultProxyConfiguration(byref(info)):
        raise _last_error("WinHttpGetDefaultProxyConfiguration")
    proxy = _take_global_string(info.lpszProxy)
    _take_global_string(info.lpszProxyBypass)
    if int(info.dwAccessType) == WINHTTP_ACCESS_TYPE_NO_PROXY:
        return ""
    return proxy or "прокси без адреса"


def reset_winhttp_proxy() -> None:
    """Возвращает WinHTTP к прямому доступу (как `netsh winhttp reset proxy`)."""
    api = _winhttp()
    api.WinHttpSetDefaultProxyConfiguration.argtypes = [POINTER(WINHTTP_PROXY_INFO)]
    api.WinHttpSetDefaultProxyConfiguration.restype = c_int
    info = WINHTTP_PROXY_INFO(WINHTTP_ACCESS_TYPE_NO_PROXY, None, None)
    if not api.WinHttpSetDefaultProxyConfiguration(byref(info)):
        raise _last_error("WinHttpSetDefaultProxyConfiguration")


# ── системный прокси ──────────────────────────────────────────────────────


def _per_connection_options(*option_ids: int):
    options = (INTERNET_PER_CONN_OPTIONW * len(option_ids))()
    for option, option_id in zip(options, option_ids):
        option.dwOption = option_id
    option_list = INTERNET_PER_CONN_OPTION_LISTW()
    option_list.dwSize = sizeof(INTERNET_PER_CONN_OPTION_LISTW)
    option_list.pszConnection = None  # обычное подключение, не дозвон и не VPN
    option_list.dwOptionCount = len(option_ids)
    option_list.pOptions = options
    return options, option_list


def _read_system_proxy_flags() -> tuple[int, str]:
    api = _wininet()
    api.InternetQueryOptionW.argtypes = [c_void_p, c_ulong, c_void_p, POINTER(c_ulong)]
    api.InternetQueryOptionW.restype = c_int
    options, option_list = _per_connection_options(INTERNET_PER_CONN_FLAGS, INTERNET_PER_CONN_PROXY_SERVER)
    size = c_ulong(sizeof(option_list))
    if not api.InternetQueryOptionW(None, INTERNET_OPTION_PER_CONNECTION_OPTION, byref(option_list), byref(size)):
        raise _last_error("InternetQueryOptionW")
    return int(options[0].Value.dwValue), _take_global_string(options[1].Value.pszValue)


def read_system_proxy() -> SystemProxy:
    flags, server = _read_system_proxy_flags()
    return SystemProxy(enabled=bool(flags & PROXY_TYPE_PROXY), server=server.strip())


def disable_system_proxy() -> None:
    """Снимает галочку «Использовать прокси-сервер». Адрес и остальные настройки остаются."""
    flags, _server = _read_system_proxy_flags()
    api = _wininet()
    api.InternetSetOptionW.argtypes = [c_void_p, c_ulong, c_void_p, c_ulong]
    api.InternetSetOptionW.restype = c_int
    options, option_list = _per_connection_options(INTERNET_PER_CONN_FLAGS)
    options[0].Value.dwValue = (flags & ~PROXY_TYPE_PROXY) | PROXY_TYPE_DIRECT
    if not api.InternetSetOptionW(None, INTERNET_OPTION_PER_CONNECTION_OPTION, byref(option_list), sizeof(option_list)):
        raise _last_error("InternetSetOptionW")
    # Без этих двух вызовов уже открытые программы продолжат ходить через старый прокси.
    api.InternetSetOptionW(None, INTERNET_OPTION_SETTINGS_CHANGED, None, 0)
    api.InternetSetOptionW(None, INTERNET_OPTION_REFRESH, None, 0)


# ── каталог Winsock ───────────────────────────────────────────────────────


def _winsock_function(name: str, *, for_32bit_apps: bool):
    # Каталог для 32-битных программ есть только у 64-битной Windows.
    return getattr(_ws2_32(), name + ("32" if for_32bit_apps else ""), None)


def _read_winsock_catalog(for_32bit_apps: bool) -> list[WSAPROTOCOL_INFOW]:
    enum_protocols = _winsock_function("WSCEnumProtocols", for_32bit_apps=for_32bit_apps)
    if enum_protocols is None:
        return []
    enum_protocols.argtypes = [c_void_p, c_void_p, POINTER(c_ulong), POINTER(c_int)]
    enum_protocols.restype = c_int
    size = c_ulong(0)
    error = c_int(0)
    # Первый вызов с пустым буфером сообщает, сколько байт нужно.
    count = enum_protocols(None, None, byref(size), byref(error))
    if count == SOCKET_ERROR and error.value != WSAENOBUFS:
        raise NetworkWinApiError(_error_text("WSCEnumProtocols", error.value))
    if not size.value:
        return []
    buffer = ctypes.create_string_buffer(size.value)
    count = enum_protocols(None, buffer, byref(size), byref(error))
    if count == SOCKET_ERROR:
        raise NetworkWinApiError(_error_text("WSCEnumProtocols", error.value))
    entries = ctypes.cast(buffer, POINTER(WSAPROTOCOL_INFOW))
    return [WSAPROTOCOL_INFOW.from_buffer_copy(entries[index]) for index in range(count)]


def list_layered_winsock_providers() -> tuple[WinsockProvider, ...]:
    """Надстройки (LSP) из обоих каталогов Winsock, по одной записи на надстройку.

    Обычные поставщики самой Windows и драйверов (ChainLen == 1) сюда не попадают.
    """
    found: dict[tuple[bool, bytes], WinsockProvider] = {}
    for for_32bit_apps in (False, True):
        for entry in _read_winsock_catalog(for_32bit_apps):
            if int(entry.ProtocolChain.ChainLen) == BASE_PROTOCOL:
                continue
            provider_id = bytes(entry.ProviderId)
            found.setdefault(
                (for_32bit_apps, provider_id),
                WinsockProvider(
                    name=str(entry.szProtocol or "").strip() or "без названия",
                    provider_id=provider_id,
                    for_32bit_apps=for_32bit_apps,
                ),
            )
    return tuple(found.values())


def remove_winsock_provider(provider: WinsockProvider) -> None:
    deinstall = _winsock_function("WSCDeinstallProvider", for_32bit_apps=provider.for_32bit_apps)
    if deinstall is None:
        raise NetworkWinApiError("WSCDeinstallProvider: функция недоступна в этой Windows")
    deinstall.argtypes = [POINTER(GUID), POINTER(c_int)]
    deinstall.restype = c_int
    provider_id = GUID.from_buffer_copy(provider.provider_id)
    error = c_int(0)
    if deinstall(byref(provider_id), byref(error)) != 0:
        raise NetworkWinApiError(_error_text("WSCDeinstallProvider", error.value))


__all__ = [
    "NetworkWinApiError",
    "SystemProxy",
    "WinsockProvider",
    "disable_system_proxy",
    "flush_neighbor_and_path_caches",
    "list_layered_winsock_providers",
    "read_system_proxy",
    "read_winhttp_proxy",
    "remove_winsock_provider",
    "reset_winhttp_proxy",
]
