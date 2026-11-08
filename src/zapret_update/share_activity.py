"""Журнал раздачи: кто к нам обращался и к кому ходили мы.

Раздача по сети и по интернету работает без окон, и человек иначе не видит, что
происходит на его компьютере. Журнал хранит последние ``LIMIT`` событий только в
памяти, пока программа запущена: адреса чужих компьютеров на диск не пишутся.

Поля события:

- ``direction`` — ``in``: обратились к нам; ``out``: ходили мы;
- ``via`` — ``lan`` (локальная сеть) или ``internet``;
- ``peer`` — адрес другого компьютера (порт не пишем: он случайный и ничего не значит);
- ``what`` — ``search`` (вопрос «у кого есть версия?» в сети), ``file``, ``peers``
  (обмен адресами), ``manifest`` (подписанный список выпуска), ``request`` (чужой запрос);
- ``detail`` — имя файла из подписанного списка, если это файл;
- ``size`` — сколько байт передано;
- ``result`` — ``ok``, ``interrupted``, ``refused``, ``busy``, ``not_found``, ``failed``.

Только стандартная библиотека.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

LIMIT = 500


@dataclass(frozen=True, slots=True)
class Event:
    when: float
    direction: str
    via: str
    peer: str
    what: str
    detail: str
    size: int
    result: str


class ActivityLog:
    def __init__(self, limit: int = LIMIT, *, now: Callable[[], float] = time.time) -> None:
        self._events: deque[Event] = deque(maxlen=max(int(limit), 1))
        self._now = now
        self._lock = threading.Lock()

    def add(
        self, direction: str, via: str, peer: str, what: str, detail: str = "", size: int = 0, result: str = "ok"
    ) -> None:
        event = Event(
            self._now(), str(direction), str(via), str(peer)[:64], str(what), str(detail)[:200], max(int(size), 0), str(result)
        )
        with self._lock:
            self._events.append(event)

    def snapshot(self) -> list[Event]:
        """События от новых к старым."""
        with self._lock:
            return list(reversed(self._events))

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def summary(self) -> dict[str, int]:
        events = self.snapshot()
        visitors = {e.peer for e in events if e.direction == "in" and e.what != "search"}
        return {
            "events": len(events),
            "visitors": len(visitors),
            "given_bytes": sum(e.size for e in events if e.direction == "in" and e.result in ("ok", "interrupted")),
            "taken_bytes": sum(e.size for e in events if e.direction == "out" and e.result == "ok"),
            "internet_events": sum(1 for e in events if e.via == "internet"),
        }


__all__ = ["ActivityLog", "Event", "LIMIT"]
