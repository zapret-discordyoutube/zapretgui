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

Связь внутри своей сети. Сбой проверки сайта часто даёт не провайдер, а
слабый Wi‑Fi или второй роутер. Поэтому меряется по пять пингов до трёх точек:
до роутера (первый узел), до узла сразу за ним и до сервера в интернете.
Потери до роутера — беда дома, потери только дальше — у провайдера.

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
    "HopSample",
    "NetworkFacts",
    "NetworkLine",
    "collect",
    "judge",
    "local_address",
    "measure_hop",
    "parse_trace",
]

TRACE_HOST = "one.one.one.one"
TRACE_PATH = "/cdn-cgi/trace"
# Два адреса одного владельца: провайдер иногда закрывает один из них.
TRACE_SERVERS = ("1.1.1.1", "1.0.0.1")


PING_COUNT = 5
PING_TIMEOUT_MS = 1000
# Срок жизни пакета, которого хватает до любого сервера.
FAR_TTL = 64
# Роутер в своей комнате отвечает за единицы миллисекунд; дольше — слабый Wi‑Fi.
SLOW_ROUTER_MS = 30.0
# Сколько потерянных из пяти — уже не случайность.
LOSS_LIMIT = 2


@dataclass(frozen=True, slots=True)
class HopSample:
    """Несколько пингов до одной точки дороги."""

    address: str = ""
    sent: int = 0
    answered: int = 0
    avg_ms: float | None = None

    @property
    def lost(self) -> int:
        return self.sent - self.answered


def measure_hop(probe: Callable[[int], tuple[str, float | None] | None], ttl: int, count: int = PING_COUNT) -> HopSample | None:
    """``probe(срок жизни)`` → (адрес ответившего, время) или None, если ответа нет. None целиком — пинг недоступен."""
    address, times, answered = "", [], 0
    for _attempt in range(count):
        try:
            got = probe(ttl)
        except NotImplementedError:
            return None
        if got is None:
            continue
        answered += 1
        address = address or got[0]
        if got[1] is not None:
            times.append(float(got[1]))
    return HopSample(address, count, answered, sum(times) / len(times) if times else None)


@dataclass(frozen=True, slots=True)
class NetworkFacts:
    # Адрес, под которым нас видит интернет. Пусто — узнать не удалось.
    external_ip: str = ""
    # Страна по мнению Cloudflare (двухбуквенный код).
    country: str = ""
    owner: IpOwner | None = None
    # Адрес сетевого адаптера, через который компьютер выходит в сеть.
    local_ip: str = ""
    # Пинги до роутера, до узла за ним и до сервера в интернете. None — не меряли.
    router: HopSample | None = None
    beyond: HopSample | None = None
    internet: HopSample | None = None


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
    probe: Callable[[int], tuple[str, float | None] | None] | None = None,
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
    router = beyond = internet = None
    if probe is not None:
        router, beyond, internet = (measure_hop(probe, ttl) for ttl in (1, 2, FAR_TTL))
    return NetworkFacts(
        external_ip=external, country=country, owner=owner, local_ip=local(), router=router, beyond=beyond, internet=internet
    )


def _ms(value: float | None) -> str:
    if value is None:
        return ""
    return "меньше 1 мс" if value < 1 else f"{round(value)} мс"


def _link_lines(facts: NetworkFacts) -> list[NetworkLine]:
    """Связь до роутера, за ним и до интернета. Потери называются там, где они начинаются."""
    lines: list[NetworkLine] = []
    router, beyond, internet = facts.router, facts.beyond, facts.internet
    if router is None or internet is None:
        return lines
    home = router.answered and address_kind(router.address) == AddressKind.LOCAL
    router_bad = False
    if not router.answered:
        if internet.answered:
            lines.append(NetworkLine("info", "Роутер", "на пинг не отвечает — оценить связь с ним нельзя, но интернет отвечает"))
    elif not home:
        lines.append(NetworkLine("info", "Роутер", f"первый узел {router.address} — уже сеть провайдера: компьютер подключён без домашнего роутера"))
    elif router.lost >= LOSS_LIMIT:
        router_bad = True
        lines.append(
            NetworkLine(
                "warn",
                "Роутер",
                f"{router.address} — теряется {router.lost} из {router.sent} пакетов. Так бывает при слабом Wi‑Fi или плохом "
                "кабеле; сайты в проверке могут «не открываться» именно из-за этого",
            )
        )
    elif router.avg_ms is not None and router.avg_ms > SLOW_ROUTER_MS:
        router_bad = True
        lines.append(
            NetworkLine(
                "warn",
                "Роутер",
                f"{router.address} — отвечает медленно ({_ms(router.avg_ms)}). Похоже на слабый сигнал Wi‑Fi: "
                "подойдите ближе к роутеру или подключитесь кабелем",
            )
        )
    else:
        lines.append(NetworkLine("ok", "Роутер", f"{router.address} — отвечает за {_ms(router.avg_ms)}, потерь нет"))

    if home and beyond is not None and beyond.answered:
        kind = address_kind(beyond.address)
        if beyond.address.startswith("192.168."):
            lines.append(
                NetworkLine(
                    "info",
                    "За роутером",
                    f"{beyond.address} — ещё один роутер (двойная сеть). Работать не мешает, но входящие соединения усложняет",
                )
            )
        elif kind == AddressKind.CARRIER:
            lines.append(NetworkLine("info", "За роутером", f"{beyond.address} — общий адрес провайдера (CGNAT)"))
        elif kind == AddressKind.LOCAL:
            lines.append(NetworkLine("info", "За роутером", f"{beyond.address} — внутренняя сеть провайдера"))
        else:
            lines.append(NetworkLine("info", "За роутером", f"{beyond.address} — сеть провайдера"))

    if not internet.answered:
        lines.append(NetworkLine("unknown", "Связь с интернетом", "сервер на пинг не ответил — провайдер может резать пинг, это не поломка"))
    elif internet.lost >= LOSS_LIMIT:
        where = "они начинаются уже до роутера" if router_bad else "до роутера потерь нет — теряется дальше, у провайдера"
        lines.append(
            NetworkLine(
                "warn",
                "Связь с интернетом",
                f"теряется {internet.lost} из {internet.sent} пакетов ({where}). При таких потерях результаты проверки ненадёжны",
            )
        )
    else:
        lines.append(NetworkLine("ok", "Связь с интернетом", f"ответ за {_ms(internet.avg_ms)}, потерь нет"))
    return lines


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

    lines.extend(_link_lines(facts))
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
