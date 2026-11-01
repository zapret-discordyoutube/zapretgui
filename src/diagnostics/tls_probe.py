"""Итог HTTPS-запроса и общий TLS-контекст Python для служебных проб.

Сам запрос «открывается ли сайт» идёт как у Chrome — ``utils.chrome_https``:
то же рукопожатие и тот же HTTP/2, что у браузера, потому что фильтр
провайдера различает программы по первому пакету соединения, а стратегии
обхода режут именно его. Здесь остались общие для всех проб вещи:

- ``ProbeResult`` и виды исхода ``KIND_*`` / этапы ``STAGE_*`` — выводы
  диагностики и подбора стратегий опираются только на них;
- ``client_context`` — контекст OpenSSL с проверкой сертификата для проб,
  которым нужен свой сокет (набор объёма по одному соединению, отправка,
  запрос к серверу проекта).
"""

from __future__ import annotations

import ssl
import threading
from dataclasses import dataclass

from utils.net_failure import FAILURE_REFUSED, FAILURE_TIMEOUT

__all__ = [
    "KIND_CANCELLED",
    "KIND_CERT",
    "KIND_CONNECT",
    "KIND_ERROR",
    "KIND_OK",
    "KIND_RESET",
    "KIND_TIMEOUT",
    "KIND_TLS",
    "ProbeResult",
    "client_context",
]

# Виды исхода запроса. Выводы диагностики опираются только на них.
KIND_OK = "ok"
KIND_CERT = "cert"
KIND_TLS = "tls"
KIND_RESET = "reset"
KIND_TIMEOUT = "timeout"
KIND_CONNECT = "connect"
KIND_CANCELLED = "cancelled"
KIND_ERROR = "error"

# Почему не соединились — вид сбоя из общего словаря ``utils.net_failure``.
CONNECT_TIMEOUT = FAILURE_TIMEOUT
CONNECT_REFUSED = FAILURE_REFUSED

STAGE_CONNECT = "connect"
STAGE_TLS = "tls"
STAGE_READ = "read"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ip: str
    kind: str
    stage: str = ""
    status: int | None = None
    body_size: int = 0
    # Ответ начал приходить, но оборвался (таймаут или сброс посреди тела).
    body_cut: bool = False
    # Байты ответа на проводе: заголовки и тело как пришло (сжатое не распаковано).
    # 0 — не измерено (тогда счёт идёт по ``body_size``). По ним и по ``handshake_size``
    # ``diagnostics.cut_window`` считает байты так, как их считает фильтр.
    received_size: int = 0
    # Байты рукопожатия TLS от сервера (сертификат и прочее); 0 — не измерено.
    handshake_size: int = 0
    cert_problem: str = ""
    tls_version: str = ""
    elapsed_ms: float = 0.0
    detail: str = ""
    body: bytes = b""
    # Заголовок Date ответа сервера (как пришёл), "" — его не было.
    server_date: str = ""
    # Почему не установилось соединение — вид сбоя ``utils.net_failure``: "timeout" —
    # ответа нет вовсе, "refused" — адрес сам отказал, "unreachable" — до адреса нет
    # дороги, "other" — прочая ошибка системы (например, запрет сетевого экрана).
    connect_fail: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == KIND_OK


_context_lock = threading.Lock()
_context: ssl.SSLContext | None = None


def client_context() -> ssl.SSLContext:
    """Один контекст на процесс: загрузка хранилища сертификатов не бесплатна."""
    global _context
    with _context_lock:
        if _context is None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_default_certs()
            # В Python на Windows хранилище Windows может быть неполным
            # (корневые сертификаты догружаются по требованию), и без certifi
            # подлинный сайт выглядел бы «сертификатом неизвестного центра».
            try:
                import certifi

                context.load_verify_locations(certifi.where())
            except Exception:
                pass
            try:
                context.set_alpn_protocols(["http/1.1"])
            except (NotImplementedError, ssl.SSLError):
                pass
            _context = context
        return _context
