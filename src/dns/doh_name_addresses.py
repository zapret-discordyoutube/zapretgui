"""Адреса имени DoH-сервера: где шифрованный сервис живёт на самом деле.

Обычный DNS и DoH одного поставщика бывают на разных адресах. У GeoHide обычный DNS —
193.233.112.67 и .68 (это же его посредники), а DoH отвечает только на 37.230.192.51, адрес имени
geohide.ru (проверено из США 2026-11-17: на .67 DoH молчит, на .68 отказ). У DNS-AI так же
(обычный 192.144.59.14, DoH — 194.87.196.13 и 45.151.102.219). Поэтому шифрованному движку и
проверке hosts нужны адреса самого имени, а не только адреса из списка серверов.

Имя спрашивается у эталонного шифрованного DNS (``utils.dns_reference``): обычный запрос провайдер может
перехватить и ответить за сервер.
"""

from __future__ import annotations

import threading
import time

__all__ = ["MAX_NAME_ADDRESSES", "doh_host_addresses"]

# Сколько адресов имени отдавать движку: остальное — лишние попытки при запуске.
MAX_NAME_ADDRESSES = 3

TIMEOUT_S = 3.0
# Адрес имени DoH-сервера запоминается на столько: проверка спрашивает десятки имён подряд.
HOST_CACHE_S = 3600.0
_host_cache: dict[str, tuple[float, list[str]]] = {}
_host_lock = threading.Lock()


def doh_host_addresses(tls_host: str) -> list[str]:
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
    # Неудачный вопрос не запоминается: после сбоя сети следующая попытка спросит заново.
    if found:
        with _host_lock:
            _host_cache[tls_host] = (now, found)
    return found
