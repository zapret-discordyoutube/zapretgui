"""Свежие адреса профиля hosts — у DNS-сервера самого профиля.

Профиль в «Редакторе hosts» — это ответы одного DNS-сервера (Comss DNS,
GeoHide и других): для сайтов, закрытых для России, сервер отдаёт адрес
своего посредника. Каталог hosts хранит эти ответы на день выпуска, а
серверы переводят имена на новые посредники без предупреждения (Comss
2026-11-01 перевёл 309 имён). Пока каталог не обновлён, профиль пишет в
hosts старые адреса, и сервис не открывается, хотя тот же DNS в системе
работает.

Поэтому программа спрашивает сервер профиля сама, с компьютера
пользователя — так ответ ещё и тот, что сервер даёт именно этой сети
(DNS-AI, например, подменяет адреса только домашним сетям России).
Спрашивает шифрованным запросом (DoH): обычный запрос провайдер может
перехватить и ответить за сервер. Адреса и шаблон DoH берутся из списка
серверов «Настройки DNS» (dns.dns_providers), второй копии здесь нет.

Zapret DNS здесь нет: за его адресом следит dns.zapret_dns_server. У XBOX DNS
в списке больше нет сервера — его профиль живёт на адресах каталога.
"""

from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit

__all__ = ["PROFILE_SERVERS", "ask_profile", "has_server"]

# Профиль каталога hosts → название сервера в списке «Настройки DNS».
PROFILE_SERVERS: dict[str, str] = {
    "comss_dns": "Comss DNS",
    "geohide": "GeoHide",
    "astracat": "AstraCat",
    "dns_ai": "DNS-AI",
}

TIMEOUT_S = 3.0
# Адрес имени DoH-сервера запоминается на столько: проверка спрашивает десятки имён подряд.
HOST_CACHE_S = 3600.0
_host_cache: dict[str, tuple[float, list[str]]] = {}
_host_lock = threading.Lock()


def _doh_host_addresses(tls_host: str) -> list[str]:
    """Адреса самого имени DoH (dns.dns-ai.ru и т. п.) по шифрованному DNS.

    Шифрованный сервис живёт не всегда там же, где обычный DNS из списка: у DNS-AI
    обычный DNS — 192.144.59.14, а DoH — 194.87.196.13 и 45.151.102.219 (2026-11-02).
    """
    now = time.monotonic()
    with _host_lock:
        cached = _host_cache.get(tls_host)
        if cached is not None and now - cached[0] < HOST_CACHE_S:
            return list(cached[1])
    from utils.dns_reference import reference_ask
    from utils.dns_wire import TYPE_A
    from utils.socket_cancel import SocketCancel

    try:
        answer = reference_ask(SocketCancel(), timeout_s=TIMEOUT_S)(tls_host, TYPE_A)
        found = [str(item) for item in answer.values(TYPE_A)] if answer is not None else []
    except Exception:
        found = []
    with _host_lock:
        _host_cache[tls_host] = (now, found)
    return found


def has_server(profile_id: str) -> bool:
    return str(profile_id or "") in PROFILE_SERVERS


def _server(profile_id: str) -> tuple[list[str], str, int, str] | None:
    """(адреса IPv4, имя для TLS, порт, путь) сервера профиля; None — спросить нечем."""
    from dns.dns_providers import iter_providers

    wanted = PROFILE_SERVERS.get(str(profile_id or ""))
    if not wanted:
        return None
    for _group, name, data in iter_providers():
        if name != wanted:
            continue
        template = urlsplit(str(data.get("doh") or ""))
        addresses = [str(item) for item in data.get("ipv4") or () if str(item or "").strip()]
        if not template.hostname or not addresses:
            return None
        return addresses, template.hostname, template.port or 443, template.path or "/dns-query"
    return None


def ask_profile(
    profile_id: str, host: str, want_ipv6: bool = False, *, query=None, host_addresses=None
) -> list[str] | None:
    """Адреса имени, которые сейчас отдаёт сервер профиля. None — сервера нет или он не ответил.

    Сначала пробуются адреса имени DoH, потом адреса сервера из списка — по
    очереди, пока один не ответит. Пустой список — сервер ответил, но адресов у
    имени нет. ``query`` и ``host_addresses`` — подмена сети для тестов.
    """
    from utils.dns_wire import TYPE_A, TYPE_AAAA, query_doh

    server = _server(profile_id)
    if server is None:
        return None
    listed, tls_host, port, path = server
    own = (host_addresses or _doh_host_addresses)(tls_host)
    addresses = list(dict.fromkeys([*own, *listed]))
    ask = query or query_doh
    found: list[str] = []
    for rtype in (TYPE_A, TYPE_AAAA) if want_ipv6 else (TYPE_A,):
        for address in addresses:
            try:
                result = ask(address, host, rtype, tls_host=tls_host, port=port, path=path, timeout_s=TIMEOUT_S)
            except Exception:
                continue
            if result.answered:
                found.extend(str(value).strip().lower() for value in result.values(rtype))
                break
        else:
            if rtype == TYPE_A:
                # Сервер не ответил ни по одному адресу: свежих данных нет — остаётся то, что известно.
                return None
    return list(dict.fromkeys(found))
