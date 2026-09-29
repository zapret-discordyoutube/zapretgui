"""Вызовы Windows для DNS: адаптеры, чтение и запись DNS, сброс кэша.

Только тонкая обёртка над IP Helper API и DNS API, без решений «что
показывать». Решения живут в dns.adapters, а этот модуль отдаёт сырые
сведения в виде простых dataclass-ов.

- Адаптеры: GetAdaptersAddresses (без GAA_FLAG_INCLUDE_ALL_INTERFACES —
  этот флаг добавляет скрытые служебные интерфейсы: WFP/QoS/Npcap-фильтры,
  отладчик ядра и т.п.) плюс GetIfEntry2 для признаков Windows «аппаратный»,
  «фильтр», «есть разъём».
- Где интернет: GetBestInterfaceEx до публичного адреса (IPv4 и IPv6).
- DNS адаптера: GetInterfaceDnsSettings / SetInterfaceDnsSettings
  (Windows 10 2004+); DoH — через DNS_INTERFACE_SETTINGS3 (Windows 11).
- Кэш: DnsFlushResolverCache.

Библиотеки Windows подгружаются лениво, поэтому модуль импортируется и на
других системах (тесты подменяют `_iphlpapi` и `_dnsapi`).
"""

from __future__ import annotations

import ctypes
import socket
import sys
from ctypes import POINTER, Structure, Union, byref, c_int32, c_uint32, c_uint64, c_ubyte, c_ushort, c_void_p, c_wchar_p
from dataclasses import dataclass, field
from functools import lru_cache

ERROR_SUCCESS = 0
ERROR_BUFFER_OVERFLOW = 111

AF_UNSPEC = 0
AF_INET = 2
AF_INET6 = 23

GAA_FLAG_SKIP_UNICAST = 0x0001
GAA_FLAG_SKIP_ANYCAST = 0x0002
GAA_FLAG_SKIP_MULTICAST = 0x0004

IF_TYPE_ETHERNET_CSMACD = 6
IF_TYPE_SOFTWARE_LOOPBACK = 24
IF_TYPE_IEEE80211 = 71
IF_OPER_STATUS_UP = 1

# Биты MIB_IF_ROW2.InterfaceAndOperStatusFlags.
IF_FLAG_HARDWARE = 0x01
IF_FLAG_FILTER = 0x02
IF_FLAG_CONNECTOR_PRESENT = 0x04

DNS_INTERFACE_SETTINGS_VERSION1 = 1
DNS_INTERFACE_SETTINGS_VERSION3 = 3
DNS_SETTING_IPV6 = 0x0001
DNS_SETTING_NAMESERVER = 0x0002
DNS_SETTING_DOH = 0x1000
DNS_SERVER_PROPERTY_VERSION1 = 1
DNS_DOH_SERVER_SETTINGS_ENABLE = 0x0002
DNS_DOH_SERVER_SETTINGS_FALLBACK_TO_UDP = 0x0004
DNS_SERVER_DOH_PROPERTY = 1

# DNS_INTERFACE_SETTINGS3 (DoH) появился в Windows 11 (build 22000).
DOH_MIN_BUILD = 22000

# Публичные адреса только для выбора маршрута: пакеты туда не уходят.
INTERNET_PROBE_V4 = "1.1.1.1"
INTERNET_PROBE_V6 = "2606:4700:4700::1111"


class DnsWinApiError(OSError):
    """Windows вернула ошибку; текст уже понятен человеку."""


# ── структуры ─────────────────────────────────────────────────────────────


class GUID(Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_ushort), ("Data3", c_ushort), ("Data4", c_ubyte * 8)]


class SOCKET_ADDRESS(Structure):
    _fields_ = [("lpSockaddr", c_void_p), ("iSockaddrLength", c_int32)]


class IP_ADAPTER_DNS_SERVER_ADDRESS(Structure):
    pass


IP_ADAPTER_DNS_SERVER_ADDRESS._fields_ = [
    ("Length", c_uint32),
    ("Reserved", c_uint32),
    ("Next", POINTER(IP_ADAPTER_DNS_SERVER_ADDRESS)),
    ("Address", SOCKET_ADDRESS),
]


class IP_ADAPTER_ADDRESSES(Structure):
    """Начало IP_ADAPTER_ADDRESSES_LH — до поля OperStatus включительно."""


IP_ADAPTER_ADDRESSES._fields_ = [
    ("Length", c_uint32),
    ("IfIndex", c_uint32),
    ("Next", POINTER(IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", c_void_p),
    ("FirstAnycastAddress", c_void_p),
    ("FirstMulticastAddress", c_void_p),
    ("FirstDnsServerAddress", POINTER(IP_ADAPTER_DNS_SERVER_ADDRESS)),
    ("DnsSuffix", c_wchar_p),
    ("Description", c_wchar_p),
    ("FriendlyName", c_wchar_p),
    ("PhysicalAddress", c_ubyte * 8),
    ("PhysicalAddressLength", c_uint32),
    ("Flags", c_uint32),
    ("Mtu", c_uint32),
    ("IfType", c_uint32),
    ("OperStatus", c_int32),
    ("Ipv6IfIndex", c_uint32),
]


class MIB_IF_ROW2(Structure):
    _fields_ = [
        ("InterfaceLuid", c_uint64),
        ("InterfaceIndex", c_uint32),
        ("InterfaceGuid", GUID),
        ("Alias", ctypes.c_wchar * 257),
        ("Description", ctypes.c_wchar * 257),
        ("PhysicalAddressLength", c_uint32),
        ("PhysicalAddress", c_ubyte * 32),
        ("PermanentPhysicalAddress", c_ubyte * 32),
        ("Mtu", c_uint32),
        ("Type", c_uint32),
        ("TunnelType", c_int32),
        ("MediaType", c_int32),
        ("PhysicalMediumType", c_int32),
        ("AccessType", c_int32),
        ("DirectionType", c_int32),
        ("InterfaceAndOperStatusFlags", c_ubyte),
        ("OperStatus", c_int32),
        ("AdminStatus", c_int32),
        ("MediaConnectState", c_int32),
        ("NetworkGuid", GUID),
        ("ConnectionType", c_int32),
        ("TransmitLinkSpeed", c_uint64),
        ("ReceiveLinkSpeed", c_uint64),
        ("Counters", c_uint64 * 18),
    ]


class DNS_DOH_SERVER_SETTINGS(Structure):
    _fields_ = [("Template", c_wchar_p), ("Flags", c_uint64)]


class DNS_SERVER_PROPERTY_TYPES(Union):
    _fields_ = [("DohSettings", POINTER(DNS_DOH_SERVER_SETTINGS))]


class DNS_SERVER_PROPERTY(Structure):
    _fields_ = [
        ("Version", c_uint32),
        ("ServerIndex", c_uint32),
        ("Type", c_int32),
        ("Property", DNS_SERVER_PROPERTY_TYPES),
    ]


_DNS_SETTINGS_V1_FIELDS = [
    ("Version", c_uint32),
    ("Flags", c_uint64),
    ("Domain", c_wchar_p),
    ("NameServer", c_wchar_p),
    ("SearchList", c_wchar_p),
    ("RegistrationEnabled", c_uint32),
    ("RegisterAdapterName", c_uint32),
    ("EnableLLMNR", c_uint32),
    ("QueryAdapterName", c_uint32),
    ("ProfileNameServer", c_wchar_p),
]


class DNS_INTERFACE_SETTINGS(Structure):
    _fields_ = list(_DNS_SETTINGS_V1_FIELDS)


class DNS_INTERFACE_SETTINGS3(Structure):
    _fields_ = [
        *_DNS_SETTINGS_V1_FIELDS,
        ("DisableUnconstrainedQueries", c_uint32),
        ("SupplementalSearchList", c_wchar_p),
        ("cServerProperties", c_uint32),
        ("ServerProperties", POINTER(DNS_SERVER_PROPERTY)),
        ("cProfileServerProperties", c_uint32),
        ("ProfileServerProperties", POINTER(DNS_SERVER_PROPERTY)),
    ]


# ── данные наружу ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RawInterface:
    """Сетевой интерфейс так, как его описывает Windows."""

    guid: str
    index: int
    ipv6_index: int
    name: str
    description: str
    if_type: int
    connected: bool
    hardware: bool = False
    filter: bool = False
    connector: bool = False
    dns_ipv4: tuple[str, ...] = ()
    dns_ipv6: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InternetRoute:
    """Индексы интерфейсов, через которые идёт интернет (0 — маршрута нет)."""

    ipv4_index: int = 0
    ipv6_index: int = 0

    @property
    def has_ipv6(self) -> bool:
        return self.ipv6_index != 0


@dataclass(frozen=True, slots=True)
class StaticDns:
    """DNS, прописанные вручную; пусто — адаптер получает DNS автоматически."""

    ipv4: tuple[str, ...] = field(default_factory=tuple)
    ipv6: tuple[str, ...] = field(default_factory=tuple)


# ── библиотеки ────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _iphlpapi():
    return ctypes.WinDLL("iphlpapi")


@lru_cache(maxsize=1)
def _dnsapi():
    return ctypes.WinDLL("dnsapi")


def _error_text(api: str, code: int) -> str:
    detail = ""
    formatter = getattr(ctypes, "FormatError", None)
    if formatter is not None:
        try:
            detail = str(formatter(int(code)) or "").strip()
        except Exception:
            detail = ""
    return f"{api}: ошибка Windows {code}" + (f" — {detail}" if detail else "")


def guid_to_string(value: GUID) -> str:
    tail = bytes(value.Data4)
    return (
        f"{{{value.Data1:08X}-{value.Data2:04X}-{value.Data3:04X}-"
        f"{tail[:2].hex().upper()}-{tail[2:].hex().upper()}}}"
    )


def guid_from_string(text: str) -> GUID:
    raw = str(text or "").strip().strip("{}")
    parts = raw.split("-")
    if len(parts) != 5 or [len(part) for part in parts] != [8, 4, 4, 4, 12]:
        raise ValueError(f"Неверный GUID адаптера: {text!r}")
    tail = bytes.fromhex(parts[3] + parts[4])
    return GUID(int(parts[0], 16), int(parts[1], 16), int(parts[2], 16), (c_ubyte * 8)(*tail))


def _sockaddr_to_ip(address: SOCKET_ADDRESS) -> str:
    if not address.lpSockaddr or address.iSockaddrLength < 8:
        return ""
    data = ctypes.string_at(address.lpSockaddr, address.iSockaddrLength)
    family = int.from_bytes(data[:2], "little")
    if family == AF_INET and len(data) >= 8:
        return socket.inet_ntop(socket.AF_INET, data[4:8])
    if family == AF_INET6 and len(data) >= 24:
        return socket.inet_ntop(socket.AF_INET6, data[8:24])
    return ""


def split_name_servers(value: str | None) -> tuple[str, ...]:
    """«1.1.1.1,1.0.0.1» / «a b» / «a;b» → кортеж адресов без повторов."""
    text = str(value or "").replace(";", " ").replace(",", " ")
    return tuple(dict.fromkeys(part for part in text.split() if part))


# ── адаптеры ──────────────────────────────────────────────────────────────


def _interface_flags(index: int) -> int | None:
    row = MIB_IF_ROW2()
    row.InterfaceIndex = int(index)
    if int(_iphlpapi().GetIfEntry2(byref(row))) != ERROR_SUCCESS:
        return None
    return int(row.InterfaceAndOperStatusFlags)


def list_interfaces() -> list[RawInterface]:
    """Все обычные сетевые интерфейсы Windows (без скрытых служебных)."""
    flags = GAA_FLAG_SKIP_UNICAST | GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST
    size = c_uint32(32 * 1024)
    for _attempt in range(4):
        buffer = ctypes.create_string_buffer(int(size.value))
        first = ctypes.cast(buffer, POINTER(IP_ADAPTER_ADDRESSES))
        result = int(_iphlpapi().GetAdaptersAddresses(AF_UNSPEC, flags, None, first, byref(size)))
        if result == ERROR_BUFFER_OVERFLOW:
            continue
        if result != ERROR_SUCCESS:
            raise DnsWinApiError(_error_text("GetAdaptersAddresses", result))
        return _read_interfaces(first)
    raise DnsWinApiError("GetAdaptersAddresses: список адаптеров меняется слишком быстро")


def _read_interfaces(first) -> list[RawInterface]:
    interfaces: list[RawInterface] = []
    current = first
    while current:
        item = current.contents
        current = item.Next
        if int(item.IfType) == IF_TYPE_SOFTWARE_LOOPBACK:
            continue
        guid = bytes(item.AdapterName or b"").decode("ascii", errors="ignore").strip()
        name = str(item.FriendlyName or "").strip()
        if not guid or not name:
            continue
        ipv4: list[str] = []
        ipv6: list[str] = []
        server = item.FirstDnsServerAddress
        while server:
            address = _sockaddr_to_ip(server.contents.Address)
            if address:
                (ipv6 if ":" in address else ipv4).append(address)
            server = server.contents.Next
        index = int(item.IfIndex or item.Ipv6IfIndex)
        if_flags = _interface_flags(index)
        interfaces.append(
            RawInterface(
                guid=guid if guid.startswith("{") else "{" + guid + "}",
                index=int(item.IfIndex),
                ipv6_index=int(item.Ipv6IfIndex),
                name=name,
                description=str(item.Description or "").strip(),
                if_type=int(item.IfType),
                connected=int(item.OperStatus) == IF_OPER_STATUS_UP,
                hardware=bool(if_flags is not None and if_flags & IF_FLAG_HARDWARE),
                filter=bool(if_flags is not None and if_flags & IF_FLAG_FILTER),
                connector=bool(if_flags is not None and if_flags & IF_FLAG_CONNECTOR_PRESENT),
                dns_ipv4=tuple(dict.fromkeys(ipv4)),
                dns_ipv6=tuple(dict.fromkeys(ipv6)),
            )
        )
    return interfaces


def _best_interface(address: str) -> int:
    if ":" in address:
        raw = bytearray(28)
        raw[0:2] = AF_INET6.to_bytes(2, "little")
        raw[8:24] = socket.inet_pton(socket.AF_INET6, address)
    else:
        raw = bytearray(16)
        raw[0:2] = AF_INET.to_bytes(2, "little")
        raw[4:8] = socket.inet_aton(address)
    sockaddr = (ctypes.c_ubyte * len(raw)).from_buffer(raw)
    index = c_uint32(0)
    if int(_iphlpapi().GetBestInterfaceEx(sockaddr, byref(index))) != ERROR_SUCCESS:
        return 0
    return int(index.value)


def internet_route() -> InternetRoute:
    """Через какие интерфейсы Windows сейчас отправит трафик в интернет."""
    return InternetRoute(ipv4_index=_best_interface(INTERNET_PROBE_V4), ipv6_index=_best_interface(INTERNET_PROBE_V6))


# ── DNS адаптера ──────────────────────────────────────────────────────────


def windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)
    except (AttributeError, OSError):
        return 0


def is_doh_supported() -> bool:
    return windows_build() >= DOH_MIN_BUILD


def read_static_dns(guid: str) -> StaticDns:
    """DNS, заданные на адаптере вручную (IPv4 и IPv6)."""
    values: list[tuple[str, ...]] = []
    for flags in (0, DNS_SETTING_IPV6):
        settings = DNS_INTERFACE_SETTINGS()
        settings.Version = DNS_INTERFACE_SETTINGS_VERSION1
        settings.Flags = flags
        result = int(_iphlpapi().GetInterfaceDnsSettings(guid_from_string(guid), byref(settings)))
        if result != ERROR_SUCCESS:
            raise DnsWinApiError(_error_text("GetInterfaceDnsSettings", result))
        try:
            values.append(split_name_servers(settings.NameServer))
        finally:
            _iphlpapi().FreeInterfaceDnsSettings(byref(settings))
    return StaticDns(ipv4=values[0], ipv6=values[1])


def write_dns(guid: str, servers: list[str] | tuple[str, ...], *, ipv6: bool, doh_templates: dict[str, str] | None = None) -> None:
    """Прописывает DNS адаптеру; пустой список — вернуть автоматические DNS.

    doh_templates: {адрес: шаблон DoH}. Передаётся только там, где Windows
    умеет DoH; для адресов из словаря Windows будет шифровать запросы,
    а при недоступности DoH откатится на обычный DNS.
    """
    servers = tuple(dict.fromkeys(str(item).strip() for item in servers if str(item or "").strip()))
    flags = DNS_SETTING_NAMESERVER | (DNS_SETTING_IPV6 if ipv6 else 0)
    keep_alive: list[object] = []
    if doh_templates is not None:
        settings = DNS_INTERFACE_SETTINGS3()
        settings.Version = DNS_INTERFACE_SETTINGS_VERSION3
        flags |= DNS_SETTING_DOH
        properties = []
        for server_index, server in enumerate(servers):
            template = str(doh_templates.get(server) or "").strip()
            if not template:
                continue
            doh = DNS_DOH_SERVER_SETTINGS(template, DNS_DOH_SERVER_SETTINGS_ENABLE | DNS_DOH_SERVER_SETTINGS_FALLBACK_TO_UDP)
            keep_alive.append(doh)
            value = DNS_SERVER_PROPERTY_TYPES()
            value.DohSettings = ctypes.pointer(doh)
            properties.append(DNS_SERVER_PROPERTY(DNS_SERVER_PROPERTY_VERSION1, server_index, DNS_SERVER_DOH_PROPERTY, value))
        if properties:
            array = (DNS_SERVER_PROPERTY * len(properties))(*properties)
            keep_alive.append(array)
            settings.cServerProperties = len(properties)
            settings.ServerProperties = array
    else:
        settings = DNS_INTERFACE_SETTINGS()
        settings.Version = DNS_INTERFACE_SETTINGS_VERSION1
    settings.Flags = flags
    settings.NameServer = ("," if not ipv6 else " ").join(servers)
    result = int(_iphlpapi().SetInterfaceDnsSettings(guid_from_string(guid), byref(settings)))
    if result != ERROR_SUCCESS:
        raise DnsWinApiError(_error_text("SetInterfaceDnsSettings", result))


def flush_resolver_cache() -> bool:
    """Очищает кэш DNS Windows (как ipconfig /flushdns)."""
    return bool(_dnsapi().DnsFlushResolverCache())


__all__ = [
    "DnsWinApiError",
    "InternetRoute",
    "RawInterface",
    "StaticDns",
    "flush_resolver_cache",
    "guid_from_string",
    "guid_to_string",
    "internet_route",
    "is_doh_supported",
    "list_interfaces",
    "read_static_dns",
    "split_name_servers",
    "write_dns",
]
