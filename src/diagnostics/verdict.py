"""Выводы диагностики: подменяет ли DNS адрес и что происходит с соединением.

Здесь нет сети — только правила. Движок (``diagnostics.engine``) собирает
факты, а этот модуль решает, что они значат.

Главное правило про DNS: **адрес, не похожий на эталон, сам по себе не
подмена**. Anycast и CDN отдают разным резолверам разные адреса, а DNS для
обхода блокировок (Comss, Xbox DNS и т. п.) специально возвращает адрес своего
прокси. Решает сертификат: настоящий сервер (или честный прокси, который
просто пересылает трафик) предъявит подлинный сертификат сайта, заглушка
провайдера — нет.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import Enum

from blockcheck.config import KNOWN_BLOCK_IPS
from utils.windows_dns_query import DNS_STATUS_NAME_ERROR, DNS_STATUS_NO_RECORDS
from utils.windows_http import (
    KIND_CANCELLED,
    KIND_CERT,
    KIND_CONNECT,
    KIND_DNS,
    KIND_OK,
    KIND_RESET,
    KIND_TIMEOUT,
    KIND_TLS,
    KIND_UNSUPPORTED,
)

__all__ = [
    "ChannelState",
    "DnsJudgement",
    "DnsState",
    "describe_channel",
    "judge_channel",
    "judge_dns",
]


class DnsState(Enum):
    OK = "ok"
    SPOOFED = "spoofed"
    LOCAL = "local"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DnsJudgement:
    state: DnsState
    reason: str


# VPN-клиенты в режиме fake-ip (sing-box, Clash, Happ) отдают адреса из этого
# диапазона и сами подставляют настоящий сервер. Это не провайдер.
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def _is_stub_ip(ip: str) -> bool:
    if ip in KNOWN_BLOCK_IPS:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if address in _FAKE_IP_NETWORK:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_unspecified
        or address.is_link_local
        or address.is_reserved
    )


def _is_fake_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in _FAKE_IP_NETWORK
    except ValueError:
        return False


def judge_dns(
    *,
    system_ips: tuple[str, ...],
    system_status: int,
    reference_ips: tuple[str, ...],
    hosts_ips: tuple[str, ...],
    https_kind: str,
    https_cert_problem: str = "",
    https_remote_ip: str = "",
) -> DnsJudgement:
    """Вердикт по одному домену.

    ``system_ips`` — ответ DNS-серверов системы (без кэша и hosts),
    ``reference_ips`` — эталон по DNS-over-HTTPS, ``https_kind`` — исход
    HTTPS-запроса к домену, который Windows сделала своим резолвером,
    ``https_remote_ip`` — адрес, к которому этот запрос реально подключился.
    """
    if hosts_ips:
        note = f"адрес задан в файле hosts ({', '.join(hosts_ips)}), программы берут его оттуда, а не у DNS"
        if https_kind == KIND_CERT:
            return DnsJudgement(DnsState.LOCAL, f"{note}; сервер по этому адресу предъявил чужой сертификат — запись в hosts устарела или неверна")
        return DnsJudgement(DnsState.LOCAL, note)

    if not system_ips:
        if system_status in (DNS_STATUS_NAME_ERROR, DNS_STATUS_NO_RECORDS) and reference_ips:
            return DnsJudgement(DnsState.SPOOFED, "DNS отвечает, что такого сайта нет, хотя он существует — сайт заблокирован через DNS")
        return DnsJudgement(DnsState.UNKNOWN, "DNS-сервер не дал адрес, сравнить не с чем")

    fake_ips = [ip for ip in system_ips if _is_fake_ip(ip)]
    if fake_ips:
        return DnsJudgement(DnsState.LOCAL, f"адрес {fake_ips[0]} выдал VPN-клиент (режим fake-ip) — это не провайдер")

    stubs = [ip for ip in system_ips if _is_stub_ip(ip)]
    if stubs:
        return DnsJudgement(DnsState.SPOOFED, f"DNS вернул адрес-заглушку {stubs[0]} вместо настоящего сервера")

    if reference_ips and set(system_ips) & set(reference_ips):
        return DnsJudgement(DnsState.OK, "адрес совпадает с эталоном (DNS-over-HTTPS)")

    if https_remote_ip and ":" not in https_remote_ip and https_remote_ip not in system_ips:
        # Windows взяла адрес из своего кэша, а не из свежего ответа DNS:
        # сертификат проверен не там, куда указывает DNS сейчас.
        return DnsJudgement(
            DnsState.UNKNOWN,
            f"адрес отличается от эталона, а сертификат проверился на другом адресе ({https_remote_ip}) "
            "из кэша Windows. Выполните ipconfig /flushdns и повторите проверку",
        )

    if https_kind == KIND_OK:
        if reference_ips:
            return DnsJudgement(
                DnsState.OK,
                "адрес отличается от эталона, но сервер предъявил подлинный сертификат сайта. "
                "Так работают CDN и DNS для обхода блокировок — это не подмена",
            )
        return DnsJudgement(DnsState.OK, "эталон недоступен, но сервер предъявил подлинный сертификат сайта")

    if https_kind == KIND_CERT:
        problem = https_cert_problem or "сертификат не прошёл проверку"
        return DnsJudgement(DnsState.SPOOFED, f"по адресу из DNS отвечает чужой сервер: {problem}")

    return DnsJudgement(
        DnsState.UNKNOWN,
        "адрес отличается от эталона, а проверить сертификат не удалось — соединение не установилось",
    )


class ChannelState(Enum):
    OK = "ok"
    DPI = "dpi"
    IP_BLOCK = "ip_block"
    CERT = "cert"
    NO_DNS = "no_dns"
    UNKNOWN = "unknown"


def judge_channel(*, https_kind: str, tcp_ok: bool | None) -> ChannelState:
    """Что происходит с соединением. ``tcp_ok`` — удалось ли открыть TCP 443."""
    if https_kind == KIND_OK:
        return ChannelState.OK
    if https_kind == KIND_CERT:
        return ChannelState.CERT
    if https_kind == KIND_DNS:
        return ChannelState.NO_DNS
    if tcp_ok is False or https_kind == KIND_CONNECT:
        return ChannelState.IP_BLOCK
    if https_kind in (KIND_TLS, KIND_RESET, KIND_TIMEOUT):
        return ChannelState.DPI
    return ChannelState.UNKNOWN


def describe_channel(kind: str, *, cert_problem: str = "", timeout: float = 0.0) -> str:
    """Короткое объяснение исхода HTTPS-запроса для строки отчёта."""
    if kind == KIND_CERT:
        return f"чужой сертификат: {cert_problem or 'не прошёл проверку'} — подмена адреса или перехват"
    if kind == KIND_TLS:
        return "соединение рвётся при установке шифрования — так выглядит блокировка DPI"
    if kind == KIND_RESET:
        return "соединение сброшено — так выглядит блокировка DPI"
    if kind == KIND_TIMEOUT:
        return f"сервер не ответил за {timeout:.0f} с — блокировка DPI или потеря пакетов"
    if kind == KIND_CONNECT:
        return "не удалось подключиться к серверу — адрес заблокирован или нет сети"
    if kind == KIND_DNS:
        return "имя сайта не разрешается"
    if kind == KIND_CANCELLED:
        return "проверка прервана — не хватило времени или нажата «Стоп»"
    if kind == KIND_UNSUPPORTED:
        return "не поддерживается этой версией Windows"
    return "не удалось выполнить запрос"
