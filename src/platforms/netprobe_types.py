"""Results and codes of network measurements, the same on every system.

The numbers of the DNS statuses are the Windows ones (9003 = "no such domain"): the diagnostics
compare them, so every backend answers in this vocabulary (the Linux backend maps DNS response
codes onto them).
"""

from __future__ import annotations

from dataclasses import dataclass

ERROR_SUCCESS = 0
ERROR_CANCELLED = 1223
ERROR_TIMEOUT = 1460
DNS_STATUS_SERVER_FAILURE = 9002
DNS_STATUS_NAME_ERROR = 9003
DNS_STATUS_REFUSED = 9005
DNS_STATUS_NO_RECORDS = 9501

_STATUS_TEXT = {
    ERROR_CANCELLED: "запрос отменён",
    ERROR_TIMEOUT: "DNS-сервер не ответил вовремя",
    DNS_STATUS_SERVER_FAILURE: "DNS-сервер вернул ошибку (SERVFAIL)",
    DNS_STATUS_NAME_ERROR: "DNS-сервер ответил, что такого домена нет",
    DNS_STATUS_REFUSED: "DNS-сервер отказался отвечать",
    DNS_STATUS_NO_RECORDS: "у домена нет IPv4-адресов",
}


@dataclass(frozen=True, slots=True)
class DnsAnswer:
    """Ответ DNS: адреса IPv4 из секции ответа и код результата (в числах Windows)."""

    ips: tuple[str, ...] = ()
    cnames: tuple[str, ...] = ()
    status: int = 0
    detail: str = ""
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.ips)


def status_text(status: int) -> str:
    return _STATUS_TEXT.get(int(status), f"ошибка DNS {int(status)}")


@dataclass(frozen=True, slots=True)
class PingResult:
    ok: bool
    average_ms: float | None = None
    sent: int = 0
    received: int = 0
    resolved_ip: str | None = None
    error_code: str | None = None
    detail: str = ""
    min_ms: float | None = None
    max_ms: float | None = None
    ttl: int | None = None


HOP_ROUTER = "router"  # ответил промежуточный узел: срок жизни пакета истёк на нём
HOP_TARGET = "target"  # ответил сам адрес назначения
HOP_SILENT = "silent"  # никто не ответил за отведённое время
HOP_UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class TraceHopResult:
    kind: str
    address: str = ""
    rtt_ms: float | None = None
