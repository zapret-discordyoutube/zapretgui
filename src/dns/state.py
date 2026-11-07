from __future__ import annotations

from dataclasses import dataclass

from dns.adapters import DnsAdapter


@dataclass(frozen=True, slots=True)
class LineCheck:
    """Прошлая проверка «DNS-серверов» на линии пользователя (dns.line_check)."""

    # Когда проверяли, ISO-время записи истории.
    time: str
    # (сервер, вывод CARD_*, коротко что не так) — по одному на сервер.
    cards: tuple[tuple[str, str, str], ...] = ()

    def card(self, server: str) -> tuple[str, str] | None:
        for name, status, note in self.cards:
            if name == server:
                return status, note
        return None


@dataclass(frozen=True, slots=True)
class DnsState:
    """Снимок DNS-слоя для страницы: адаптеры с их DNS, есть ли IPv6 и DoH, свои серверы."""

    adapters: tuple[DnsAdapter, ...] = ()
    ipv6_available: bool = False
    doh_supported: bool = False
    # Переключатель «Шифровать DNS (DoH)» включён (dns.doh_switch).
    doh_enabled: bool = True
    # Режим работающего шифрованного DNS (dns.local_proxy) или пустая строка.
    local_proxy_mode: str = ""
    # Название плитки, чей сервер работает через шифрованный DNS в режиме DoH; иначе пусто.
    local_proxy_server: str = ""
    # Свои DNS-серверы пользователя из настроек (записи dns.custom_servers).
    custom_servers: tuple[dict, ...] = ()
    # Прошлая проверка серверов на этой линии; None — её нет или она устарела.
    line_check: LineCheck | None = None


@dataclass(frozen=True, slots=True)
class DnsCommandResult:
    success: bool
    message: str = ""
    affected_count: int = 0
    total_count: int = 0
    # Движок шифрованного DNS слушает ещё и ::1 (ответ start_local_proxy).
    listen_ipv6: bool = False
    # DNS поставлен, но DoH до сервера не доходит: Windows спрашивает без шифрования.
    doh_closed: bool = False


@dataclass(frozen=True, slots=True)
class CustomServerResult:
    """Итог действия со своими DNS: новый список или ошибка.

    field — поле страницы «Свой DNS», к которому относится ошибка (имена из
    dns.custom_servers). notice — оговорка к удачному сохранению.
    """

    success: bool
    servers: tuple[dict, ...] = ()
    server: dict | None = None
    error: str = ""
    field: str = ""
    notice: str = ""
