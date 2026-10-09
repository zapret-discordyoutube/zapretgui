"""Отвечает ли DNS-сервер из сети пользователя — проверка перед применением.

Блокировка у каждого провайдера своя: один и тот же сервер может быть закрыт
у одного и открыт у другого. Поэтому «заблокирован» нельзя записать в список
серверов — это узнаётся только на месте, в момент, когда пользователь выбрал
сервер. Если сервер не отвечает ни одним способом, программа его не ставит:
иначе компьютер остался бы без DNS и без интернета.

Сервер считается живым, если ответил хотя бы один способ: обычный запрос
(UDP), запрос по TCP или шифрованный (DoH, когда у сервера есть шаблон). Так
программа не отказывает там, где провайдер режет только открытый DNS.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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


def _answers(address: str, template: str) -> bool:
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
        return any(future.result().answered for future in [pool.submit(probe) for probe in probes])


def unreachable_message(ipv4: list[str], ipv6: list[str], templates: dict[str, str] | None = None) -> str:
    """Пустая строка, если сервер отвечает или проверять нечего; иначе — причина отказа.

    Проверяются адреса IPv4; IPv6 — только когда других нет: у многих
    провайдеров IPv6 не работает вовсе, и это не блокировка сервера.
    """
    templates = templates or {}
    addresses = [item for item in (str(a or "").strip() for a in ipv4) if item and not _is_local(item)]
    if not addresses:
        addresses = [item for item in (str(a or "").strip() for a in ipv6) if item and not _is_local(item)]
    if not addresses:
        return ""
    with ThreadPoolExecutor(max_workers=len(addresses), thread_name_prefix="dns-reach") as pool:
        alive = list(pool.map(lambda address: _answers(address, str(templates.get(address) or "")), addresses))
    if any(alive):
        return ""
    return (
        f"Сервер {', '.join(addresses)} не отвечает из вашей сети ни обычным, ни шифрованным запросом — "
        "похоже, его блокирует провайдер. DNS не изменён: с этим сервером интернет перестал бы работать. "
        "Выберите другой сервер."
    )


__all__ = ["unreachable_message"]
