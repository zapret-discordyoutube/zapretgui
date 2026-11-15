"""Отвечает ли DNS-сервер из сети пользователя — проверка перед применением.

Блокировка у каждого провайдера своя: один и тот же сервер может быть закрыт
у одного и открыт у другого. Поэтому «заблокирован» нельзя записать в список
серверов — это узнаётся только на месте, в момент, когда пользователь выбрал
сервер. Если сервер не отвечает ни одним способом, программа его не ставит:
иначе компьютер остался бы без DNS и без интернета.

Сервер считается живым, если ответил хотя бы один способ: обычный запрос
(UDP), запрос по TCP или шифрованный (DoH, когда у сервера есть шаблон). Так
программа не отказывает там, где провайдер режет только открытый DNS.

Обратный случай тоже отмечается: шаблон DoH есть, а шифрованный путь молчит,
хотя обычный отвечает. Windows записывается с откатом на обычный DNS
(``system_dns.write_dns``), поэтому DNS заработает, но без шифрования — об этом
человеку надо сказать, иначе он считает свои запросы закрытыми.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

from utils.dns_wire import TYPE_A, query_doh, query_tcp, query_udp

# Безобидное имя для самой проверки связи.
PROBE_NAME = "example.com"
UDP_TIMEOUT_S = 2.0
TCP_TIMEOUT_S = 3.0
DOH_TIMEOUT_S = 4.0


def _is_local(address: str) -> bool:
    """Свой компьютер или домашняя сеть: такие адреса снаружи не блокируют."""
    try:
        ip = ip_address(address)
    except ValueError:
        return True
    return ip.is_loopback or ip.is_private or ip.is_link_local


@dataclass(frozen=True, slots=True)
class ServerReach:
    # Почему сервер не ставится; пустая строка — ставить можно.
    blocked: str = ""
    # Шифрованный путь закрыт, а обычный отвечает: Windows будет спрашивать без шифрования.
    doh_closed: bool = False


@dataclass(frozen=True, slots=True)
class _Answers:
    plain: bool
    # None — шаблона DoH у адреса нет, спрашивать нечем.
    doh: bool | None


def _answers(address: str, template: str) -> _Answers:
    probes = [
        lambda: query_udp(address, PROBE_NAME, TYPE_A, timeout_s=UDP_TIMEOUT_S),
        lambda: query_tcp(address, PROBE_NAME, TYPE_A, timeout_s=TCP_TIMEOUT_S),
    ]
    url = urlsplit(template) if template else None
    if url is not None and url.hostname:
        probes.append(
            lambda: query_doh(
                address,
                PROBE_NAME,
                TYPE_A,
                tls_host=url.hostname,
                port=url.port or 443,
                path=url.path or "/dns-query",
                timeout_s=DOH_TIMEOUT_S,
            )
        )
    with ThreadPoolExecutor(max_workers=len(probes), thread_name_prefix="dns-reach") as pool:
        answered = [future.result().answered for future in [pool.submit(probe) for probe in probes]]
    return _Answers(plain=any(answered[:2]), doh=answered[2] if len(answered) > 2 else None)


def check_servers(ipv4: list[str], ipv6: list[str], templates: dict[str, str] | None = None) -> ServerReach:
    """Можно ли ставить сервер и дойдёт ли до него шифрование.

    Проверяются адреса IPv4; IPv6 — только когда других нет: у многих
    провайдеров IPv6 не работает вовсе, и это не блокировка сервера.
    """
    templates = templates or {}
    addresses = [item for item in (str(a or "").strip() for a in ipv4) if item and not _is_local(item)]
    if not addresses:
        addresses = [item for item in (str(a or "").strip() for a in ipv6) if item and not _is_local(item)]
    if not addresses:
        return ServerReach()
    with ThreadPoolExecutor(max_workers=len(addresses), thread_name_prefix="dns-reach") as pool:
        answers = list(pool.map(lambda address: _answers(address, str(templates.get(address) or "")), addresses))
    if not any(item.plain or item.doh for item in answers):
        return ServerReach(
            blocked=(
                f"Сервер {', '.join(addresses)} не отвечает из вашей сети ни обычным, ни шифрованным запросом — "
                "похоже, его блокирует провайдер. DNS не изменён: с этим сервером интернет перестал бы работать. "
                "Выберите другой сервер."
            )
        )
    with_doh = [item for item in answers if item.doh is not None]
    # Хоть один адрес ответил по DoH — Windows шифрует через него.
    return ServerReach(doh_closed=bool(with_doh) and not any(item.doh for item in with_doh))


__all__ = ["ServerReach", "check_servers"]
