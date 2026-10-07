"""Ваша сеть: под каким адресом вас видит интернет и чей это провайдер.

Зачем это в проверке:

- отчёт без провайдера мало что говорит: блокировки у операторов разные;
- если снаружи виден адрес чужой сети (VPN, прокси), вся проверка описывает
  сеть вместе с ним — об этом нужно сказать сразу;
- адрес компьютера из диапазона 100.64.0.0/10 значит, что провайдер держит
  вас за общим адресом (так устроены мобильные и многие домашние сети).

Внешний адрес спрашивается у Cloudflare по его собственному адресу, без DNS:
страница ``/cdn-cgi/trace`` отвечает строками ``ip=…``, ``loc=…``. Владелец
сети узнаётся по службе Team Cymru шифрованным запросом (``utils.ip_owner``).

Здесь только сбор фактов и чистый вывод; чем ходить в сеть, решает вызывающий.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass

from utils.address_kinds import AddressKind, address_kind
from utils.ip_owner import IpOwner

__all__ = [
    "TRACE_HOST",
    "TRACE_PATH",
    "TRACE_SERVERS",
    "NetworkFacts",
    "NetworkLine",
    "collect",
    "judge",
    "local_address",
    "parse_trace",
]

TRACE_HOST = "one.one.one.one"
TRACE_PATH = "/cdn-cgi/trace"
# Два адреса одного владельца: провайдер иногда закрывает один из них.
TRACE_SERVERS = ("1.1.1.1", "1.0.0.1")


@dataclass(frozen=True, slots=True)
class NetworkFacts:
    # Адрес, под которым нас видит интернет. Пусто — узнать не удалось.
    external_ip: str = ""
    # Страна по мнению Cloudflare (двухбуквенный код).
    country: str = ""
    owner: IpOwner | None = None
    # Адрес сетевого адаптера, через который компьютер выходит в сеть.
    local_ip: str = ""


@dataclass(frozen=True, slots=True)
class NetworkLine:
    # ok / warn / info / unknown
    state: str
    name: str
    text: str


def parse_trace(body: bytes | str) -> tuple[str, str]:
    """(адрес, страна) из ответа ``/cdn-cgi/trace``. Пустые строки — нет в ответе."""
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body or "")
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    ip = fields.get("ip", "").strip()
    if address_kind(ip) == AddressKind.INVALID:
        ip = ""
    return ip, fields.get("loc", "").strip().upper()


def local_address() -> str:
    """Адрес адаптера, через который идёт выход в интернет. Пакеты при этом не отправляются."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP-сокет только выбирает дорогу: соединения нет и в сеть ничего не уходит.
        sock.connect((TRACE_SERVERS[0], 53))
        return str(sock.getsockname()[0])
    except OSError:
        return ""
    finally:
        sock.close()


def collect(
    *,
    fetch: Callable[[str], bytes | None],
    owner_of: Callable[[str], IpOwner | None],
    local: Callable[[], str] = local_address,
) -> NetworkFacts:
    """``fetch(адрес сервера)`` отдаёт тело ``/cdn-cgi/trace`` или None; ``owner_of(адрес)`` — владельца сети."""
    external, country = "", ""
    for server in TRACE_SERVERS:
        body = fetch(server)
        if body:
            external, country = parse_trace(body)
            if external:
                break
    owner = owner_of(external) if external else None
    return NetworkFacts(external_ip=external, country=country, owner=owner, local_ip=local())


def _owner_text(owner: IpOwner | None) -> str:
    if owner is None:
        return ""
    parts = [owner.owner, f"AS{owner.asn}" if owner.asn else ""]
    return " · ".join(part for part in parts if part)


def judge(facts: NetworkFacts, *, bypass_tools=()) -> tuple[NetworkLine, ...]:
    """Строки для отчёта. Вывод о VPN не делается: названа только сеть, которую видно снаружи."""
    lines: list[NetworkLine] = []
    if facts.external_ip:
        place = f" ({facts.country})" if facts.country else ""
        lines.append(NetworkLine("info", "Внешний адрес", f"{facts.external_ip}{place}"))
        owner = _owner_text(facts.owner)
        if owner:
            country = facts.owner.country if facts.owner is not None else ""
            lines.append(NetworkLine("info", "Провайдер", f"{owner}{f', {country}' if country else ''}"))
            if facts.owner is not None and facts.owner.prefix:
                lines.append(NetworkLine("info", "Сеть", facts.owner.prefix))
        else:
            lines.append(NetworkLine("unknown", "Провайдер", "узнать не удалось"))
    else:
        lines.append(NetworkLine("unknown", "Внешний адрес", "узнать не удалось: сервер Cloudflare не ответил"))

    kind = address_kind(facts.local_ip) if facts.local_ip else AddressKind.INVALID
    if kind == AddressKind.CARRIER:
        lines.append(
            NetworkLine(
                "info",
                "Адрес компьютера",
                f"{facts.local_ip} — провайдер держит вас за общим адресом: входящие соединения снаружи невозможны",
            )
        )
    elif facts.local_ip and facts.external_ip and facts.local_ip == facts.external_ip:
        lines.append(NetworkLine("info", "Адрес компьютера", f"{facts.local_ip} — совпадает с внешним, прямое подключение"))
    elif facts.local_ip and kind == AddressKind.PUBLIC and facts.external_ip:
        # У компьютера свой адрес в интернете, а снаружи виден другой: между ними кто-то есть.
        lines.append(
            NetworkLine(
                "info",
                "Адрес компьютера",
                f"{facts.local_ip} — а снаружи виден другой адрес: между ними прокси, VPN или общий адрес провайдера",
            )
        )
    elif facts.local_ip:
        lines.append(NetworkLine("info", "Адрес компьютера", f"{facts.local_ip} — за роутером"))

    tools = [str(item) for item in bypass_tools if item]
    if tools:
        lines.append(
            NetworkLine(
                "warn",
                "Другие программы обхода и VPN",
                f"работают: {', '.join(tools)} — внешний адрес и вся проверка описывают сеть вместе с ними",
            )
        )
    return tuple(lines)
