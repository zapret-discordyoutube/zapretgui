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
import selectors
import socket
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass

from .share_discover import local_lan_addresses
from .share_serve import is_lan_address

SSDP_ADDRESS = ("239.255.255.250", 1900)
LEASE_SECONDS = 3600
MAX_DESCRIPTION = 64 * 1024
# Часть роутеров отвечает только на «upnp:rootdevice»: подходящий отбирается по описанию.
SEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
    "upnp:rootdevice",
)
MAX_DESCRIPTIONS = 8  # сколько разных устройств расспрашивать: в сети бывают принтеры и телевизоры
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
    timeout: float = 3.0,
    target: tuple[str, int] = SSDP_ADDRESS,
    source: str | None = None,
    sources: Sequence[str] | None = None,
    allow_loopback: bool = False,
) -> Gateway | None:
    """Роутер, ответивший на SSDP, вместе с адресом управления; иначе ``None``.

    Вопрос уходит с каждого адреса компьютера в локальной сети (``sources``), а не с одного:
    при VPN или виртуальном адаптере общий маршрут мог вести не в сеть роутера. ``source`` —
    один адрес (для тестов).
    """
    origins = [source] if source is not None else (list(sources) if sources is not None else [*local_lan_addresses(), ""])
    selector = selectors.DefaultSelector()
    sockets: list[socket.socket] = []
    try:
        for origin in dict.fromkeys(origins):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets.append(sock)
            try:
                sock.bind((origin, 0))
                if origin:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(origin))
                sock.setblocking(False)
                for search in SEARCH_TARGETS:
                    request = (
                        "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
                        f"MX: 2\r\nST: {search}\r\n\r\n"
                    ).encode("ascii")
                    sock.sendto(request, target)
                selector.register(sock, selectors.EVENT_READ)
            except OSError:
                continue  # этого адреса сейчас нет или он не годится: остальные работают
        deadline = time.monotonic() + timeout
        tried: set[str] = set()
        while time.monotonic() < deadline and len(tried) < MAX_DESCRIPTIONS:
            for key, _events in selector.select(max(deadline - time.monotonic(), 0.0)):
                try:
                    data, sender = key.fileobj.recvfrom(2048)
                except OSError:
                    continue
                if not is_lan_address(sender[0], allow_loopback=allow_loopback):
                    continue
                match = _LOCATION.search(data)
                if not match:
                    continue
                location = match.group(1).decode("ascii", "ignore")
                if location in tried:
                    continue
                tried.add(location)
                gateway = _read_description(location, sender[0], allow_loopback)
                if gateway is not None:
                    return gateway
    except OSError:
        return None
    finally:
        selector.close()
        for sock in sockets:
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
        status, body = _http(parts.hostname, parts.port or 80, "GET", path, timeout=2.0)
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
    """Держит проброс порта: продлевает аренду, снимает при остановке.

    Сначала UPnP, потом NAT-PMP/PCP (у многих роутеров включено только это).
    ``reason`` — почему порт не открылся (в журнал и на страницу), ``code`` — то же одним
    словом без подробностей, ``via`` — каким способом получилось (``upnp``/``pcp``/``natpmp``).
    """

    def __init__(self, port: int, *, renew_every: float = LEASE_SECONDS / 2, gateway_finder=find_gateway,
                 pmp_open=None, pmp_hosts=None) -> None:
        self._port, self._renew_every, self._finder = int(port), float(renew_every), gateway_finder
        self._pmp_open, self._pmp_hosts = pmp_open, pmp_hosts
        self._gateway: Gateway | None = None
        self._pmp_gateway = ""
        self.mapping: Mapping | None = None
        self.reason = ""  # почему порт не открылся: идёт в журнал, чтобы по журналам людей было видно, что мешает чаще
        self.code = ""
        self.via = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _code_of(reason: str) -> str:
        if "CGNAT" in reason:
            return "cgnat"
        if "частный" in reason:
            return "double_nat"
        if "не белый" in reason or "служебный" in reason:
            return "not_public"
        if "отказал" in reason or "не разрешает" in reason:
            return "refused"
        if "не назвал внешний" in reason:
            return "no_external_ip"
        if "не ответил" in reason:
            return "no_answer"
        return "other"

    def start(self) -> Mapping | None:
        self.reason = self.code = self.via = ""
        gateway = self._finder()
        upnp_reason = ""
        if gateway is None:
            upnp_reason = "роутер с UPnP не нашёлся (UPnP выключен или его нет)"
            code = "no_router"
        else:
            internal = local_address_towards(gateway)
            if internal is None:
                self.reason, self.code = "не удалось определить адрес компьютера в сети роутера", "no_local_ip"
                return None
            mapping, upnp_reason = try_add_mapping(gateway, internal_ip=internal, port=self._port)
            if mapping is not None:
                self._gateway, self.mapping, self.via, self.code = gateway, mapping, "upnp", "via_upnp"
                self._start_renewal()
                return mapping
            code = self._code_of(upnp_reason)
        # UPnP не помог: у многих роутеров он выключен, а NAT-PMP или PCP включены.
        mapping, via, host, pmp_reason = self._try_pmp(gateway.host if gateway is not None else "")
        if mapping is not None:
            self._pmp_gateway, self.mapping, self.via, self.code = host, mapping, via, f"via_{via}"
            self._start_renewal()
            return mapping
        meaningful = pmp_reason not in ("silent", "unsupported", "")
        if gateway is None and meaningful:
            self.reason, self.code = pmp_reason, self._code_of(pmp_reason)
        elif gateway is None:
            self.reason, self.code = upnp_reason + ", NAT-PMP и PCP тоже не ответили", "no_router"
        else:
            self.reason, self.code = upnp_reason, code
        return None

    def _try_pmp(self, upnp_host: str) -> tuple[Mapping | None, str, str, str]:
        try:
            from . import share_natpmp

            hosts = list(self._pmp_hosts() if self._pmp_hosts is not None else share_natpmp.default_gateways())
            if upnp_host and upnp_host not in hosts:
                hosts.insert(0, upnp_host)
            return (self._pmp_open or share_natpmp.try_open)(hosts[:share_natpmp.MAX_GATEWAYS + 1], port=self._port)
        except Exception:  # noqa: BLE001 — запасной способ не должен ломать основной
            return None, "", "", "silent"

    def _start_renewal(self) -> None:
        self._thread = threading.Thread(target=self._renew, name="share-portmap", daemon=True)
        self._thread.start()

    def _renew(self) -> None:
        while not self._stop.wait(self._renew_every):
            if self.mapping is None:
                continue
            if self.via == "upnp" and self._gateway is not None:
                add_mapping(self._gateway, internal_ip=self.mapping.internal_ip, port=self._port)
            elif self._pmp_gateway:
                self._try_pmp(self._pmp_gateway)

    def stop(self) -> None:
        self._stop.set()
        if self.mapping is not None:
            if self.via == "upnp" and self._gateway is not None:
                delete_mapping(self._gateway, self.mapping)
            elif self._pmp_gateway:
                try:
                    from . import share_natpmp

                    share_natpmp.unmap(self._pmp_gateway, self.mapping, self.via)
                except Exception:  # noqa: BLE001
                    pass
        self._gateway = self.mapping = None
        self._pmp_gateway = ""


__all__ = [
    "Gateway", "Mapping", "PortMapper", "add_mapping", "address_kind", "delete_mapping", "external_ip",
    "find_gateway", "is_global_address", "local_address_towards", "try_add_mapping",
]
