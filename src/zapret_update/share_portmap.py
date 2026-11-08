"""Открыть порт на домашнем роутере (UPnP), чтобы компьютер был доступен из интернета.

Раздавать файлы по интернету можно, только если до компьютера можно достучаться
снаружи. Большинство домашних роутеров умеют по просьбе программы открыть порт
(UPnP IGD): программа находит роутер запросом SSDP, читает его описание и просит
``AddPortMapping``. Не нашёлся роутер, он отказал или его внешний адрес не
«белый» (провайдерский NAT) — раздачи по интернету не будет, это не ошибка.

Меры против подвоха из локальной сети:

- адрес описания и управления должен быть из локальной сети и совпадать с тем,
  кто ответил на SSDP;
- описание роутера читается не больше ``MAX_DESCRIPTION`` байт, документы с
  объявлением типа (``<!DOCTYPE``) отбрасываются: разбор XML не раздувается;
- порт открывается на адрес этого компьютера и только на время (аренда
  ``LEASE_SECONDS``), продлевается пока раздача идёт, снимается при остановке.

Только стандартная библиотека.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .share_serve import is_lan_address

SSDP_ADDRESS = ("239.255.255.250", 1900)
LEASE_SECONDS = 3600
MAX_DESCRIPTION = 64 * 1024
SEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
)
SERVICE_TYPES = (
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
)
_LOCATION = re.compile(rb"(?im)^location:\s*(\S+)\s*$")


@dataclass(frozen=True, slots=True)
class Gateway:
    host: str
    port: int
    control_path: str
    service_type: str


@dataclass(frozen=True, slots=True)
class Mapping:
    external_ip: str
    external_port: int
    internal_ip: str
    internal_port: int


def is_global_address(text: str) -> bool:
    try:
        return ipaddress.ip_address(text).is_global
    except ValueError:
        return False


def find_gateway(
    *,
    timeout: float = 2.0,
    target: tuple[str, int] = SSDP_ADDRESS,
    source: str = "",
    allow_loopback: bool = False,
) -> Gateway | None:
    """Роутер, ответивший на SSDP, вместе с адресом управления; иначе ``None``."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((source, 0))
        sock.settimeout(0.3)
        for search in SEARCH_TARGETS:
            request = (
                "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
                f"MX: 1\r\nST: {search}\r\n\r\n"
            ).encode("ascii")
            try:
                sock.sendto(request, target)
            except OSError:
                return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, sender = sock.recvfrom(2048)
            except TimeoutError:
                continue
            except OSError:
                return None
            if not is_lan_address(sender[0], allow_loopback=allow_loopback):
                continue
            match = _LOCATION.search(data)
            if not match:
                continue
            gateway = _read_description(match.group(1).decode("ascii", "ignore"), sender[0], allow_loopback)
            if gateway is not None:
                return gateway
    except OSError:
        return None
    finally:
        sock.close()
    return None


def _http(host: str, port: int, method: str, path: str, body: bytes = b"", headers: dict | None = None, timeout: float = 4.0):
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request(method, path, body=body or None, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read(MAX_DESCRIPTION + 1)
    finally:
        connection.close()


def _read_description(location: str, sender: str, allow_loopback: bool) -> Gateway | None:
    parts = urllib.parse.urlsplit(location)
    if parts.scheme != "http" or parts.hostname != sender or not is_lan_address(parts.hostname, allow_loopback=allow_loopback):
        return None  # описание должно лежать на самом роутере
    try:
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        status, body = _http(parts.hostname, parts.port or 80, "GET", path)
    except (OSError, http.client.HTTPException):
        return None
    if status != 200 or len(body) > MAX_DESCRIPTION or b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    for service in root.iter():
        if not service.tag.endswith("}service") and service.tag != "service":
            continue
        fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in service}
        if fields.get("serviceType") in SERVICE_TYPES and fields.get("controlURL"):
            control = urllib.parse.urlsplit(urllib.parse.urljoin(location, fields["controlURL"]))
            if control.hostname != sender:
                continue  # управление чужим адресом не берём
            return Gateway(sender, control.port or 80, control.path or "/", fields["serviceType"])
    return None


def _soap(gateway: Gateway, action: str, arguments: dict[str, str]) -> tuple[int, str]:
    inner = "".join(f"<{name}>{value}</{name}>" for name, value in arguments.items())
    body = (
        '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{gateway.service_type}">{inner}</u:{action}></s:Body></s:Envelope>'
    ).encode("utf-8")
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": f'"{gateway.service_type}#{action}"',
    }
    status, reply = _http(gateway.host, gateway.port, "POST", gateway.control_path, body, headers)
    return status, reply.decode("utf-8", "replace")


def external_ip(gateway: Gateway) -> str | None:
    try:
        status, reply = _soap(gateway, "GetExternalIPAddress", {})
    except (OSError, http.client.HTTPException):
        return None
    match = re.search(r"<NewExternalIPAddress>\s*([0-9a-fA-F:.]+)\s*</NewExternalIPAddress>", reply)
    return match.group(1) if status == 200 and match else None


def local_address_towards(gateway: Gateway) -> str | None:
    """Адрес этого компьютера в сети роутера (UDP «connect» ничего не отправляет)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((gateway.host, gateway.port))
            return str(probe.getsockname()[0])
    except OSError:
        return None


def address_kind(text: str | None) -> str:
    """Какого рода внешний адрес роутера, без самого адреса (в журнал адрес человека не пишем)."""
    try:
        address = ipaddress.ip_address(str(text))
    except ValueError:
        return "неизвестен"
    if address.is_global:
        return "белый"
    if address in ipaddress.ip_network("100.64.0.0/10"):
        return "общий адрес провайдера (CGNAT)"
    if address.is_link_local or address.is_loopback or address.is_unspecified:
        return "служебный"
    if address.is_private:
        return "частный (за другим роутером или NAT провайдера)"
    return "служебный"


def try_add_mapping(gateway: Gateway, *, internal_ip: str, port: int, external_port: int | None = None,
                    lease: int = LEASE_SECONDS, description: str = "update sharing") -> tuple[Mapping | None, str]:
    """Просит роутер пробросить порт; второй элемент — почему не вышло (пусто, если вышло)."""
    outside = external_port or port
    address = external_ip(gateway)
    if address is None:
        return None, "роутер не назвал внешний адрес"
    if not is_global_address(address):
        # Провайдерский NAT или второй роутер: снаружи всё равно не достучаться.
        return None, f"внешний адрес роутера не белый: {address_kind(address)}"
    try:
        status, _ = _soap(gateway, "AddPortMapping", {
            "NewRemoteHost": "", "NewExternalPort": str(outside), "NewProtocol": "TCP",
            "NewInternalPort": str(port), "NewInternalClient": internal_ip, "NewEnabled": "1",
            "NewPortMappingDescription": description, "NewLeaseDuration": str(int(lease)),
        })
    except (OSError, http.client.HTTPException):
        return None, "роутер не ответил на просьбу открыть порт"
    if status != 200:
        return None, f"роутер отказался открыть порт (ответ {status})"
    return Mapping(address, outside, internal_ip, port), ""


def add_mapping(gateway: Gateway, *, internal_ip: str, port: int, external_port: int | None = None,
                lease: int = LEASE_SECONDS, description: str = "update sharing") -> Mapping | None:
    """Просит роутер пробросить порт; ``None`` — отказал или внешний адрес не «белый»."""
    return try_add_mapping(
        gateway, internal_ip=internal_ip, port=port, external_port=external_port, lease=lease, description=description
    )[0]


def delete_mapping(gateway: Gateway, mapping: Mapping) -> bool:
    try:
        status, _ = _soap(gateway, "DeletePortMapping", {
            "NewRemoteHost": "", "NewExternalPort": str(mapping.external_port), "NewProtocol": "TCP",
        })
    except (OSError, http.client.HTTPException):
        return False
    return status == 200


class PortMapper:
    """Держит проброс порта: продлевает аренду, снимает при остановке."""

    def __init__(self, port: int, *, renew_every: float = LEASE_SECONDS / 2, gateway_finder=find_gateway) -> None:
        self._port, self._renew_every, self._finder = int(port), float(renew_every), gateway_finder
        self._gateway: Gateway | None = None
        self.mapping: Mapping | None = None
        self.reason = ""  # почему порт не открылся: идёт в журнал, чтобы по журналам людей было видно, что мешает чаще
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> Mapping | None:
        self.reason = ""
        gateway = self._finder()
        if gateway is None:
            self.reason = "роутер с UPnP не нашёлся (UPnP выключен или его нет)"
            return None
        internal = local_address_towards(gateway)
        if internal is None:
            self.reason = "не удалось определить адрес компьютера в сети роутера"
            return None
        mapping, self.reason = try_add_mapping(gateway, internal_ip=internal, port=self._port)
        if mapping is None:
            return None
        self._gateway, self.mapping = gateway, mapping
        self._thread = threading.Thread(target=self._renew, name="share-portmap", daemon=True)
        self._thread.start()
        return mapping

    def _renew(self) -> None:
        while not self._stop.wait(self._renew_every):
            if self._gateway is not None and self.mapping is not None:
                add_mapping(self._gateway, internal_ip=self.mapping.internal_ip, port=self._port)

    def stop(self) -> None:
        self._stop.set()
        if self._gateway is not None and self.mapping is not None:
            delete_mapping(self._gateway, self.mapping)
        self._gateway = self.mapping = None


__all__ = [
    "Gateway", "Mapping", "PortMapper", "add_mapping", "address_kind", "delete_mapping", "external_ip",
    "find_gateway", "is_global_address", "local_address_towards", "try_add_mapping",
]
