"""Шлюз проб — единственная дверь, через которую проверки сети уходят в сеть.

Зачем он. Оборудование провайдера (ТСПУ) видит не только отдельные пакеты, но
и узор: пачку соединений к разным адресам в одну секунду, одинаковый всплеск
при каждом запуске программы, обход соседних подсетей. Обычный человек так в
сеть не ходит, и по такому узору можно узнать саму программу. Поэтому каждая
проверка, прежде чем стучаться в сеть, спрашивает шлюз — а не решает сама,
когда и сколько.

Правила (одни на всю программу):

1. **По кнопке** (``Why.USER``). Человек сам захотел проверить — BlockCheck,
   подбор стратегий, «Проверка домена», проверка DNS-серверов, переключение
   сервиса в hosts. Такая проверка берёт пропуск на весь прогон
   (``user_run``); пока пропуск у кого-то есть, фоновые проверки не идут,
   чтобы их соединения не смешивались с чужим прогоном.
2. **По расписанию** (``Why.SCHEDULE``). Без повода трогается только то, чем
   человек пользуется сейчас (у hosts — первый записанный адрес имени), и
   не чаще интервала своего вида. Время прошлой плановой проверки хранится в
   настройках, поэтому перезапуск программы не повторяет её. Фоновая
   проверка идёт одна за раз (``try_background``).
3. **По сигналу беды** (``Why.SIGNAL``). Плановая проверка увидела, что
   текущее не работает, — только тогда разрешён поиск замены: свежие адреса
   у DNS, запас прошлых находок, обход соседних адресов (``may_search``).

Новая проверка, которая ходит в сеть сама, без кнопки, обязана пройти через
``try_background`` со своим ``BackgroundKind``; поиск — только при
``may_search``. Это проверяет ``tests/test_probe_gate.py``.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "HOSTS_ADDRESSES",
    "ZAPRET_DNS_ADDRESS",
    "ZAPRET_DNS_CHECK_IN",
    "BackgroundKind",
    "BackgroundPermit",
    "Why",
    "begin_user_run",
    "end_user_run",
    "may_search",
    "try_background",
    "user_run",
    "user_run_active",
]


class Why(StrEnum):
    """Повод проверки: от него зависит, что ей разрешено."""

    USER = "user"  # человек нажал кнопку или переключил сервис и ждёт результата
    SCHEDULE = "schedule"  # плановая проверка без повода
    SIGNAL = "signal"  # плановая проверка увидела беду: текущее не отвечает


@dataclass(frozen=True, slots=True)
class BackgroundKind:
    """Вид фоновой проверки. ``key`` — имя в настройках, ``min_interval_s`` — не чаще."""

    key: str
    min_interval_s: float


# Адреса включённых сервисов hosts (hosts.address_engine): программа живёт в трее
# сутками, а адрес может перестать отвечать в любой день.
HOSTS_ADDRESSES = BackgroundKind("hosts_addresses", 6 * 3600.0)

# Адрес своего сервера Zapret DNS по его имени (dns.zapret_dns_server.refresh): сервер
# переезжает редко, поэтому — раз в сутки.
ZAPRET_DNS_ADDRESS = BackgroundKind("zapret_dns_address", 24 * 3600.0)

# Отметка у посредника Zapret DNS, пока включён профиль hosts «Zapret DNS»
# (dns.zapret_dns_server.check_in): сервер помнит адрес 6 часов — отмечаемся втрое чаще.
ZAPRET_DNS_CHECK_IN = BackgroundKind("zapret_dns_check_in", 2 * 3600.0)


def may_search(why: Why) -> bool:
    """Можно ли искать замену (DNS, запас, соседние адреса), а не только проверить текущее."""
    return why in (Why.USER, Why.SIGNAL)


# --- Пропуск на прогон по кнопке ------------------------------------------------------
#
# Рядом живёт ``core.runtime.long_tasks`` — отметка «не перезапускать программу ради
# обновления». Это другое правило: пропуск здесь — про сеть, отметка там — про перезапуск.

_LOCK = threading.Lock()
_ids = itertools.count(1)
_user_runs: dict[int, str] = {}


def begin_user_run(name: str) -> int:
    """Начался прогон по кнопке ``name``; возвращает номер пропуска для ``end_user_run``."""
    token = next(_ids)
    with _LOCK:
        _user_runs[token] = str(name)
    return token


def end_user_run(token: int) -> None:
    """Прогон закончился; повторный вызов безвреден."""
    with _LOCK:
        _user_runs.pop(token, None)


@contextmanager
def user_run(name: str) -> Iterator[None]:
    """То же одним блоком ``with``."""
    token = begin_user_run(name)
    try:
        yield
    finally:
        end_user_run(token)


def user_run_active() -> bool:
    with _LOCK:
        return bool(_user_runs)


# --- Фоновые проверки -----------------------------------------------------------------

# Одна фоновая проверка за раз: две сразу — это уже пачка соединений.
_BACKGROUND = threading.Lock()


def _stored_last_run(key: str) -> float:
    from settings.store import get_probe_gate_last_run

    return get_probe_gate_last_run(key)


def _store_last_run(key: str, when: float) -> None:
    from settings.store import set_probe_gate_last_run

    set_probe_gate_last_run(key, when)


class BackgroundPermit:
    """Разрешение на одну фоновую проверку. Закрывается через ``with`` или ``release()``."""

    def __init__(self, kind: BackgroundKind, why: Why) -> None:
        self.kind = kind
        self.why = why
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            _BACKGROUND.release()

    def __enter__(self) -> BackgroundPermit:
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


def try_background(
    kind: BackgroundKind,
    *,
    clock: Callable[[], float] = time.time,
    last_run: Callable[[str], float] = _stored_last_run,
    store_last_run: Callable[[str, float], None] = _store_last_run,
) -> BackgroundPermit | None:
    """Разрешение на плановую проверку вида ``kind`` или None — сейчас нельзя.

    Нельзя, когда идёт прогон по кнопке, когда уже идёт другая фоновая
    проверка и когда прошлая плановая была меньше ``min_interval_s`` назад.
    Время проверки записывается сразу при выдаче разрешения: даже если она
    оборвётся, перезапуск программы не устроит её заново.
    """
    if user_run_active():
        return None
    now = clock()
    previous = last_run(kind.key)
    if 0 < now - previous < kind.min_interval_s:
        return None
    if not _BACKGROUND.acquire(blocking=False):
        return None
    if user_run_active():
        _BACKGROUND.release()
        return None
    try:
        store_last_run(kind.key, now)
    except Exception:
        _BACKGROUND.release()
        raise
    return BackgroundPermit(kind, Why.SCHEDULE)
