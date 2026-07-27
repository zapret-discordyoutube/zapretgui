"""Разрешение имён с жёстким дедлайном.

Зачем этот модуль
-----------------
``socket.getaddrinfo`` и ``socket.gethostbyname`` — синхронные вызовы ОС без
таймаута, которые нельзя прервать. ``sock.settimeout()`` их не покрывает:
таймаут сокета начинает действовать только после того, как имя уже разрешено.
На неисправном или «чёрнодырном» DNS (обычная ситуация ровно у наших
пользователей) такой вызов висит десятки секунд.

Обойти это ``future.result(timeout=...)`` + ``future.cancel()`` нельзя:
``cancel()`` не действует на уже запущенную задачу, поток пула остаётся занят
навсегда, а последующий ``pool.shutdown(wait=True)`` его дожидается — таймаут
оказывается фиктивным.

Хуже того, потоки ``ThreadPoolExecutor`` не демонические, и
``concurrent.futures`` вешает atexit-хук, который join-ит их при завершении
интерпретатора. Один залипший ``getaddrinfo`` — и приложение не закрывается.

Решение здесь: резолв уходит в **демонический** поток, вызывающая сторона ждёт
его с дедлайном. Просроченный резолв не удерживает ни пул, ни выход из
процесса — поток тихо доживёт своё в фоне и запишет результат в кэш.
"""

from __future__ import annotations

import socket
import threading
import time

__all__ = [
    "DEFAULT_DNS_TIMEOUT",
    "DNSTimeoutError",
    "clear_cache",
    "connect_tcp",
    "resolve_addrinfo",
    "resolve_ipv4",
    "resolve_ips",
]


DEFAULT_DNS_TIMEOUT = 5.0

# Успешный ответ живёт недолго: в пределах одного прогона BlockCheck один и тот
# же домен резолвится 3–4 раза (DNS-проверка, ping, HTTP :80, TCP :443), и
# переспрашивать ОС каждый раз незачем. Дольше держать нельзя — диагностика
# должна видеть свежее состояние сети.
_SUCCESS_TTL = 30.0
# Отрицательный ответ кэшируем ещё короче: DNS мог просто моргнуть.
_FAILURE_TTL = 5.0
_CACHE_LIMIT = 512


class DNSTimeoutError(OSError):
    """Имя не разрешилось за отведённое время.

    Намеренно НЕ наследует ``socket.gaierror``: вызывающий код трактует
    gaierror как «домена не существует», а это совсем другой диагноз.
    """


class _Pending:
    __slots__ = ("event", "value", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.value: list | None = None
        self.error: BaseException | None = None


_lock = threading.Lock()
_inflight: dict[tuple, _Pending] = {}
# key -> (момент записи, результат, ошибка)
_cache: dict[tuple, tuple[float, list | None, BaseException | None]] = {}


def clear_cache() -> None:
    """Сбрасывает кэш. Вызывать в начале длительной диагностики."""
    with _lock:
        _cache.clear()


def _store(key: tuple, value: list | None, error: BaseException | None) -> None:
    if error is not None:
        # Кэш переживает вызов, а traceback держит кадры стека — отцепляем его,
        # чтобы короткоживущая ошибка резолва не удерживала лишние объекты.
        error.__traceback__ = None
    with _lock:
        if len(_cache) >= _CACHE_LIMIT:
            # Простое ограничение сверху: кэш служебный, точность вытеснения
            # здесь не важна, важно не течь по памяти в долгих сессиях.
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
        _cache[key] = (time.monotonic(), value, error)
        _inflight.pop(key, None)


def _worker(key: tuple, args: tuple, pending: _Pending) -> None:
    try:
        value: list | None = socket.getaddrinfo(*args)
        error: BaseException | None = None
    except BaseException as exc:  # noqa: BLE001 — ошибка передаётся вызывающему
        value, error = None, exc

    _store(key, value, error)
    pending.value = value
    pending.error = error
    pending.event.set()


def _cached(key: tuple) -> tuple[float, list | None, BaseException | None] | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    stamp, value, error = entry
    ttl = _FAILURE_TTL if error is not None else _SUCCESS_TTL
    if time.monotonic() - stamp > ttl:
        _cache.pop(key, None)
        return None
    return entry


def resolve_addrinfo(
    host: str,
    port: int | str | None = None,
    *,
    timeout: float = DEFAULT_DNS_TIMEOUT,
    family: int = socket.AF_UNSPEC,
    socktype: int = socket.SOCK_STREAM,
    proto: int = 0,
    flags: int = 0,
) -> list[tuple]:
    """``socket.getaddrinfo`` с гарантированным дедлайном.

    Raises
    ------
    DNSTimeoutError
        Имя не разрешилось за ``timeout`` секунд.
    socket.gaierror / OSError
        Ошибка разрешения, поднятая самой ОС.
    """
    host = str(host or "").strip()
    if not host:
        raise socket.gaierror(socket.EAI_NONAME, "empty host")

    key = (host, port, family, socktype, proto, flags)
    args = (host, port, family, socktype, proto, flags)

    with _lock:
        entry = _cached(key)
        if entry is not None:
            _stamp, value, error = entry
            if error is not None:
                raise error
            return list(value or [])

        pending = _inflight.get(key)
        if pending is None:
            pending = _Pending()
            _inflight[key] = pending
            thread = threading.Thread(
                target=_worker,
                args=(key, args, pending),
                name=f"dns-resolve-{host[:40]}",
                daemon=True,
            )
            thread.start()

    deadline = max(0.0, float(timeout))
    if not pending.event.wait(deadline):
        raise DNSTimeoutError(f"DNS не ответил за {deadline:.0f}с: {host}")

    if pending.error is not None:
        raise pending.error
    return list(pending.value or [])


def resolve_ips(
    host: str,
    *,
    timeout: float = DEFAULT_DNS_TIMEOUT,
    port: int | str | None = None,
    family: int = socket.AF_UNSPEC,
    socktype: int = socket.SOCK_STREAM,
    proto: int = 0,
) -> tuple[list[str], list[str]]:
    """Возвращает ``(ipv4, ipv6)``. Никогда не бросает исключений."""
    try:
        infos = resolve_addrinfo(
            host,
            port,
            timeout=timeout,
            family=family,
            socktype=socktype,
            proto=proto,
        )
    except Exception:
        return [], []

    ipv4: list[str] = []
    ipv6: list[str] = []
    for info_family, _socktype, _proto, _canonname, sockaddr in infos:
        ip = sockaddr[0] if sockaddr else None
        if not isinstance(ip, str):
            continue
        if info_family == socket.AF_INET:
            if ip not in ipv4:
                ipv4.append(ip)
        elif info_family == socket.AF_INET6:
            if ip not in ipv6:
                ipv6.append(ip)
    return ipv4, ipv6


def resolve_ipv4(host: str, *, timeout: float = DEFAULT_DNS_TIMEOUT) -> str | None:
    """Первый IPv4-адрес хоста или ``None``. Никогда не бросает исключений."""
    ipv4, _ipv6 = resolve_ips(host, family=socket.AF_INET, timeout=timeout)
    return ipv4[0] if ipv4 else None


def connect_tcp(
    host: str,
    port: int,
    *,
    timeout: float,
    dns_timeout: float | None = None,
    resolved_ip: str | None = None,
    family: int = socket.AF_UNSPEC,
) -> tuple[socket.socket, str]:
    """TCP-соединение, у которого ограничены обе фазы: резолв и коннект.

    ``socket.create_connection`` так не умеет — его ``timeout`` относится
    только к коннекту, а разрешение имени внутри висит без ограничения.

    Returns
    -------
    tuple[socket.socket, str]
        Подключённый сокет и IP, к которому подключились.
    """
    if resolved_ip:
        candidates: list[tuple[int, tuple]] = [
            (socket.AF_INET6 if ":" in resolved_ip else socket.AF_INET, (resolved_ip, port))
        ]
    else:
        infos = resolve_addrinfo(
            host,
            port,
            timeout=dns_timeout if dns_timeout is not None else timeout,
            family=family,
            socktype=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        candidates = [(info[0], info[4]) for info in infos]

    if not candidates:
        raise socket.gaierror(socket.EAI_NONAME, f"нет адресов для {host}")

    last_error: Exception | None = None
    for addr_family, sockaddr in candidates:
        sock = socket.socket(addr_family, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            return sock, str(sockaddr[0])
        except Exception as exc:  # noqa: BLE001 — пробуем следующий адрес
            last_error = exc
            sock.close()

    raise last_error if last_error is not None else OSError(f"не удалось подключиться к {host}")
