"""Telegram: принимают ли соединения его дата-центры.

Сайт ``telegram.org`` и само приложение — разные вещи. Приложение ходит не на
сайт, а прямо по адресам пяти дата-центров Telegram, и блокируют их отдельно
от сайта: сайт может открываться, а приложение — висеть на «Соединение…».

Проверка устанавливает обычное TCP-соединение с портом 443 каждого
дата-центра. Этого достаточно, чтобы увидеть, доходит ли дорога: дальше у
Telegram свой шифрованный протокол, и без учётной записи его не проверить.

Неудавшееся соединение повторяется после паузы. Один молчащий дата-центр —
не вывод: у Telegram они живут в разных сетях, и приложение переключается
между ними. Вывод «не принимают соединения» делается, только когда молчат
все, — и то с оговоркой, что это показывает дорогу без обхода и прокси.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass

from diagnostics.verdict import Level
from utils.socket_cancel import SocketCancel, close_quietly

__all__ = [
    "DATA_CENTERS",
    "DataCenter",
    "DcResult",
    "TelegramReport",
    "check_telegram",
    "connect_once",
    "summarize_telegram",
]

CONNECT_TIMEOUT_S = 4.0
RETRY_PAUSE_S = 1.0


@dataclass(frozen=True, slots=True)
class DataCenter:
    name: str
    address: str
    port: int = 443


# Адреса дата-центров Telegram (те же, что зашиты в его приложения).
DATA_CENTERS: tuple[DataCenter, ...] = (
    DataCenter("DC1 (Майами)", "149.154.175.53"),
    DataCenter("DC2 (Амстердам)", "149.154.167.51"),
    DataCenter("DC3 (Майами)", "149.154.175.100"),
    DataCenter("DC4 (Амстердам)", "149.154.167.91"),
    DataCenter("DC5 (Сингапур)", "91.108.56.130"),
)


@dataclass(frozen=True, slots=True)
class DcResult:
    center: DataCenter
    # None — проверку сняли.
    connected: bool | None
    ms: float | None = None
    attempts: int = 1


@dataclass(frozen=True, slots=True)
class TelegramReport:
    level: Level
    headline: str
    servers: tuple[DcResult, ...]
    advice: tuple[str, ...] = ()


def connect_once(address: str, port: int, *, timeout: float = CONNECT_TIMEOUT_S, cancel: SocketCancel | None = None):
    """(соединились ли, за сколько мс). (None, None) — проверку сняли."""
    token = cancel or SocketCancel()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if not token.track(sock):
            return None, None
        sock.settimeout(timeout)
        started = time.perf_counter()
        sock.connect((address, int(port)))
        return True, (time.perf_counter() - started) * 1000.0
    except OSError:
        return (None, None) if token.cancelled else (False, None)
    finally:
        token.release(sock)
        close_quietly(sock)


def _check_center(center: DataCenter, connect: Callable, pause: Callable[[float], None]) -> DcResult:
    connected, ms = connect(center.address, center.port)
    if connected is False:
        # Потерянный пакет не должен выглядеть блокировкой.
        pause(RETRY_PAUSE_S)
        connected, ms = connect(center.address, center.port)
        return DcResult(center, connected, ms, attempts=2)
    return DcResult(center, connected, ms)


def check_telegram(
    submit: Callable[..., Future],
    wait: Callable[[Future], object],
    *,
    connect: Callable = connect_once,
    pause: Callable[[float], None] = time.sleep,
) -> tuple[DcResult, ...]:
    """Все дата-центры одновременно; неудачное соединение повторяется после паузы."""
    futures = [submit(_check_center, center, connect, pause) for center in DATA_CENTERS]
    return tuple(wait(future) for future in futures)


def summarize_telegram(servers: tuple[DcResult, ...], *, zapret_running: bool | None = None) -> TelegramReport:
    checked = [item for item in servers if item.connected is not None]
    answered = [item for item in checked if item.connected]
    if not checked:
        return TelegramReport(Level.UNKNOWN, "Дата-центры Telegram проверить не удалось", servers)
    if len(answered) == len(checked):
        return TelegramReport(Level.OK, f"Дата-центры Telegram принимают соединения: {len(answered)} из {len(checked)}", servers)
    if answered:
        # Приложение переключится на отвечающий дата-центр, но часть функций (медиа другого
        # дата-центра) может работать хуже.
        return TelegramReport(
            Level.WARN,
            f"Часть дата-центров Telegram не принимает соединения: отвечают {len(answered)} из {len(checked)}",
            servers,
            ("Приложение Telegram само переключается на доступный дата-центр; если оно не соединяется — "
             "включите встроенный Telegram Proxy.",),
        )
    return TelegramReport(
        Level.FAIL,
        f"Дата-центры Telegram не принимают соединения: ни один из {len(checked)}, каждый проверен дважды",
        servers,
        (
            "Приложение Telegram напрямую не соединится. Включите встроенный Telegram Proxy: он ведёт "
            "приложение другой дорогой.",
        ),
    )
