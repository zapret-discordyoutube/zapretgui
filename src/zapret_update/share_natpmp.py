"""Открыть порт на роутере по NAT-PMP и PCP: второй способ, когда UPnP выключен.

У многих роутеров UPnP выключен, а NAT-PMP (RFC 6886) или его наследник PCP
(RFC 6887) включены, и программа может открыть порт ими. Оба способа — один
короткий UDP-пакет на порт 5351 роутера, поэтому порядок такой: сначала PCP, если
роутер не понял — NAT-PMP.

Меры предосторожности те же, что у UPnP (``share_portmap``):

- спрашиваем только адреса из локальной сети: основной шлюз Windows и догадка «.1»
  в подсети каждого адаптера; ответ принимается только от того адреса, которому
  писали;
- порт открывается на время (аренда), продлевается, пока раздача идёт, и снимается
  при остановке;
- не «белый» внешний адрес (провайдерский NAT) — раздачи по интернету не будет.

Только стандартная библиотека.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import struct
import subprocess
import sys
import time
from collections.abc import Callable, Sequence

from .share_discover import local_lan_addresses
from .share_portmap import LEASE_SECONDS, Gateway, Mapping, address_kind, is_global_address, local_address_towards
from .share_serve import is_lan_address

PMP_PORT = 5351
MAX_GATEWAYS = 3  # столько шлюзов расспрашиваем: у человека их обычно один-два
RETRY_DELAYS = (0.25, 0.5, 1.0)  # по RFC: первая попытка, потом с растущей паузой
PCP_RESULT_TEXT = {
    1: "роутер не поддерживает эту версию PCP",
    2: "роутер не разрешает открывать порты (NOT_AUTHORIZED)",
    3: "запрос не понят роутером",
    4: "роутер не поддерживает этот вид запроса",
    8: "у роутера не хватает места под ещё один проброс",
    11: "роутер не разрешает нужный порт",
    12: "роутер не разрешает нужный адрес",
}
PMP_RESULT_TEXT = {
    1: "роутер не поддерживает эту версию NAT-PMP",
    2: "роутер не разрешает открывать порты",
    3: "у роутера нет сети снаружи",
    4: "у роутера не хватает места под ещё один проброс",
}
_ROUTE_ROW = re.compile(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)\s+\S+\s+(\d+)\s*$", re.M)
CREATE_NO_WINDOW = 0x08000000

Send = Callable[[str, int, bytes], bytes | None]  # (адрес, порт, пакет) -> ответ или None


def _run_route_print() -> str:
    options: dict = {}
    if sys.platform == "win32":
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 0
        options = {"creationflags": CREATE_NO_WINDOW, "startupinfo": info}
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    done = subprocess.run(
        [os.path.join(root, "System32", "route.exe"), "print", "-4", "0.0.0.0"],
        capture_output=True, text=True, errors="replace", timeout=8, stdin=subprocess.DEVNULL, **options,
    )
    return done.stdout or ""


def _linux_gateways(table: str) -> list[tuple[int, str]]:
    found = []
    for line in table.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 7 and fields[1] == "00000000":
            try:
                address = socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
                found.append((int(fields[6]), address))
            except (ValueError, struct.error, OSError):
                continue
    return found


def default_gateways(
    *, run_route: Callable[[], str] = _run_route_print, lan_addresses: Callable[[], Sequence[str]] = local_lan_addresses,
    allow_loopback: bool = False, linux_table: Callable[[], str] | None = None,
) -> list[str]:
    """Адреса шлюзов из локальной сети: основной маршрут, затем догадка «.1» в подсети каждого адаптера."""
    ranked: list[tuple[int, str]] = []
    try:
        if sys.platform == "win32":
            ranked = [(int(metric), host) for host, metric in _ROUTE_ROW.findall(run_route())]
        else:
            reader = linux_table or (lambda: open("/proc/net/route", encoding="ascii").read())
            ranked = _linux_gateways(reader())
    except Exception:  # noqa: BLE001 — шлюз не нашёлся этим способом, остаётся догадка
        ranked = []
    found = [host for _metric, host in sorted(ranked)]
    try:
        for own in lan_addresses():
            parts = own.split(".")
            if len(parts) == 4:
                found.append(".".join([*parts[:3], "1"]))
    except Exception:  # noqa: BLE001
        pass
    result: list[str] = []
    for host in found:
        if host not in result and is_lan_address(host, allow_loopback=allow_loopback):
            result.append(host)
    return result[:MAX_GATEWAYS]


def exchange(host: str, port: int, payload: bytes, *, delays: Sequence[float] = RETRY_DELAYS) -> bytes | None:
    """Посылает UDP-пакет роутеру и ждёт ответ от него же; без ответа — ``None``."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for pause in delays:
            sock.settimeout(pause)
            try:
                sock.sendto(payload, (host, port))
                deadline = time.monotonic() + pause
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    sock.settimeout(left)
                    data, sender = sock.recvfrom(1100)
                    if sender[0] == host and len(data) >= 4:
                        return data
            except socket.timeout:
                continue
            except OSError:
                return None
    return None


def _pcp_request(client: str, port: int, wanted: int, lease: int, nonce: bytes) -> bytes:
    mapped = lambda text: b"\x00" * 10 + b"\xff\xff" + socket.inet_aton(text)  # noqa: E731
    header = struct.pack("!BBHI", 2, 1, 0, int(lease)) + mapped(client)
    body = nonce + struct.pack("!B3xHH", 6, int(port), int(wanted)) + b"\x00" * 16  # 6 — TCP
    return header + body


def pcp_map(host: str, internal: str, port: int, *, lease: int = LEASE_SECONDS, send: Send | None = None,
            udp_port: int = PMP_PORT, nonce: bytes | None = None) -> tuple[Mapping | None, str]:
    """PCP MAP для TCP-порта: ``(проброс, причина неудачи)``; причина ``silent`` — роутер не отозвался."""
    nonce = nonce or os.urandom(12)
    reply = (send or exchange)(host, udp_port, _pcp_request(internal, port, port, lease, nonce))
    if reply is None:
        return None, "silent"
    if len(reply) < 24 or reply[0] != 2 or not reply[1] & 0x80:
        return None, "unsupported"  # NAT-PMP-роутер отвечает на версию 2 своим кодом «версия не поддерживается»
    result = reply[3]
    if result != 0:
        return None, PCP_RESULT_TEXT.get(result, f"код ответа {result}")
    if len(reply) < 60 or reply[24:36] != nonce:
        return None, "ответ роутера не к нашему запросу"
    assigned_port = struct.unpack("!H", reply[42:44])[0]
    raw = reply[44:60]
    try:
        address = ipaddress.IPv6Address(raw)
        outside = str(address.ipv4_mapped) if address.ipv4_mapped is not None else str(address)
    except ValueError:
        return None, "роутер назвал странный внешний адрес"
    return Mapping(outside, assigned_port, internal, port), ""


def natpmp_external_ip(host: str, *, send: Send | None = None, udp_port: int = PMP_PORT) -> tuple[str | None, str]:
    reply = (send or exchange)(host, udp_port, struct.pack("!BB", 0, 0))
    if reply is None:
        return None, "silent"
    if len(reply) < 12 or reply[0] != 0 or reply[1] != 128:
        return None, "unsupported"
    result = struct.unpack("!H", reply[2:4])[0]
    if result != 0:
        return None, PMP_RESULT_TEXT.get(result, f"код ответа {result}")
    return socket.inet_ntoa(reply[8:12]), ""


def natpmp_map(host: str, internal: str, port: int, *, lease: int = LEASE_SECONDS, send: Send | None = None,
               udp_port: int = PMP_PORT) -> tuple[Mapping | None, str]:
    """NAT-PMP: сначала внешний адрес роутера, потом проброс TCP-порта."""
    address, why = natpmp_external_ip(host, send=send, udp_port=udp_port)
    if address is None:
        return None, why
    reply = (send or exchange)(host, udp_port, struct.pack("!BBHHHI", 0, 2, 0, int(port), int(port), int(lease)))
    if reply is None:
        return None, "silent"
    if len(reply) < 16 or reply[1] != 130:
        return None, "unsupported"
    result = struct.unpack("!H", reply[2:4])[0]
    if result != 0:
        return None, PMP_RESULT_TEXT.get(result, f"код ответа {result}")
    return Mapping(address, struct.unpack("!H", reply[10:12])[0], internal, port), ""


def unmap(host: str, mapping: Mapping, protocol: str, *, send: Send | None = None, udp_port: int = PMP_PORT) -> bool:
    """Снимает проброс: аренда 0 (PCP — тот же запрос, NAT-PMP — запрос с внешним портом 0)."""
    send = send or exchange
    if protocol == "pcp":
        reply = send(host, udp_port, _pcp_request(mapping.internal_ip, mapping.internal_port, mapping.external_port, 0, os.urandom(12)))
        return reply is not None and len(reply) >= 4 and reply[3] == 0
    reply = send(host, udp_port, struct.pack("!BBHHHI", 0, 2, 0, mapping.internal_port, 0, 0))
    return reply is not None and len(reply) >= 4 and struct.unpack("!H", reply[2:4])[0] == 0


def try_open(
    hosts: Sequence[str], *, port: int, lease: int = LEASE_SECONDS, send: Send | None = None, udp_port: int = PMP_PORT,
    local_address: Callable[[Gateway], str | None] = local_address_towards,
) -> tuple[Mapping | None, str, str, str]:
    """Пробует PCP, потом NAT-PMP на каждом шлюзе.

    Возвращает ``(проброс, способ, шлюз, причина)``; способ — ``pcp`` или ``natpmp``.
    Причина пуста при успехе; ``silent`` — никто не отозвался.
    """
    reasons: list[str] = []
    for host in hosts:
        internal = local_address(Gateway(host, udp_port, "", ""))
        if internal is None:
            continue
        for name in ("pcp", "natpmp"):
            if name == "pcp":
                mapping, why = pcp_map(host, internal, port, lease=lease, send=send, udp_port=udp_port)
            else:
                mapping, why = natpmp_map(host, internal, port, lease=lease, send=send, udp_port=udp_port)
            if mapping is not None:
                if not is_global_address(mapping.external_ip):
                    # Роутер открыл порт, но снаружи всё равно не достучаться: провайдерский NAT или второй роутер.
                    unmap(host, mapping, name, send=send, udp_port=udp_port)
                    return None, name, host, f"внешний адрес роутера не белый: {address_kind(mapping.external_ip)}"
                return mapping, name, host, ""
            reasons.append(why)
    meaningful = [why for why in reasons if why not in ("silent", "unsupported")]
    best = meaningful[0] if meaningful else ("unsupported" if "unsupported" in reasons else "silent")
    return None, "", "", best


__all__ = [
    "PMP_PORT", "default_gateways", "exchange", "natpmp_external_ip", "natpmp_map", "pcp_map", "try_open", "unmap",
]
