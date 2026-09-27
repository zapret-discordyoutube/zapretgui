"""Здоровье маршрутов WSS: какие отказывают, на сколько их отключить, какой фронт рабочий.

Правила перенесены из ZaStoGram (jni/tgnet/wss/WssSocket.cpp):

* маршрут подавляется после нескольких отказов подряд (релей, адрес релея и
  туннель — 3, фронты — 6: около 40% попыток к фронтам получают 503);
* отказы в пределах 2 с — одна пачка стартовых соединений, это один отказ;
* потерянный SYN не считается, если к этому адресу кто-то подключился за
  последние 30 с: провайдер съел один поток, соседний пройдёт;
* срок подавления удваивается: 2 → 4 → 8 → 16 → 30 минут. Счётчик подавлений
  сбрасывают только настоящие данные от сервера;
* подавления сохраняются между запусками, но после загрузки держатся не
  дольше 10 минут, чтобы старая сеть не мешала новой.

Ключи: домен релея (kws2.web.telegram.org), адрес релея (addr-149.154.167.220),
все фронты одного DC (cdn-kws2), туннель (имя хоста воркера).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


log = logging.getLogger("tg_proxy")

FAILURES_BEFORE_SUPPRESS = 3
CDN_FAILURES_BEFORE_SUPPRESS = 6
FAILURE_COALESCE_SECONDS = 2.0
RECENT_TCP_SECONDS = 30.0
SUPPRESS_BASE_SECONDS = 120.0
SUPPRESS_MAX_SECONDS = 30 * 60.0
RESTORED_SUPPRESS_MAX_SECONDS = 10 * 60.0
# Рабочий адрес фронтов забывается после стольких отказов TCP подряд на нём.
FRONT_FAMILY_FORGET_FAILURES = 2


def address_key(ip: str) -> str:
    return f"addr-{ip}"


def cdn_key(dc: int) -> str:
    return f"cdn-kws{int(dc)}"


@dataclass(slots=True)
class _Entry:
    failures: int = 0
    suppressions: int = 0
    suppressed_until: float = 0.0
    last_failure_at: float = -1e9


class RouteHealth:
    def __init__(
        self,
        *,
        path: Path | str | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        front_count: int = 0,
    ) -> None:
        self._path = Path(path) if path else None
        self._clock = clock
        self._wall = wall_clock
        self._entries: dict[str, _Entry] = {}
        self._tcp_ok_at: dict[str, float] = {}
        self._front_slots = max(0, int(front_count)) * 2
        self._front_cursor = int.from_bytes(os.urandom(2), "little") % self._front_slots if self._front_slots else 0
        self._front_family: int | None = None
        self._front_family_failures = 0
        self._load()

    # ---- подавление ----

    def is_suppressed(self, key: str) -> bool:
        entry = self._entries.get(key)
        return entry is not None and entry.suppressed_until > self._clock()

    def suppressed_for(self, key: str) -> float:
        entry = self._entries.get(key)
        if entry is None:
            return 0.0
        return max(0.0, entry.suppressed_until - self._clock())

    def note_failure(
        self,
        key: str,
        *,
        threshold: int = FAILURES_BEFORE_SUPPRESS,
        tcp_reached: bool = True,
        address: str = "",
    ) -> bool:
        """Учесть отказ. Возвращает True, если маршрут только что подавлен."""
        now = self._clock()
        if not tcp_reached and address and self.tcp_recently_connected(address):
            return False
        entry = self._entries.setdefault(key, _Entry())
        if now - entry.last_failure_at < FAILURE_COALESCE_SECONDS:
            return False
        entry.last_failure_at = now
        entry.failures += 1
        if entry.failures < max(1, int(threshold)):
            return False
        duration = min(SUPPRESS_BASE_SECONDS * (1 << min(entry.suppressions, 4)), SUPPRESS_MAX_SECONDS)
        entry.suppressed_until = now + duration
        entry.suppressions += 1
        entry.failures = 0
        self._save()
        return True

    def note_answer(self, key: str) -> None:
        """Сервер ответил: серия отказов прервана, но подавления ещё помнятся."""
        entry = self._entries.get(key)
        if entry is not None:
            entry.failures = 0

    def note_proven(self, key: str) -> None:
        """Маршрут передал настоящий объём данных: забыть все подавления."""
        entry = self._entries.pop(key, None)
        if entry is not None and entry.suppressions:
            self._save()

    # ---- недавний TCP ----

    def note_tcp_connected(self, address: str) -> None:
        if address:
            self._tcp_ok_at[address] = self._clock()

    def tcp_recently_connected(self, address: str) -> bool:
        at = self._tcp_ok_at.get(address)
        return at is not None and self._clock() - at < RECENT_TCP_SECONDS

    # ---- фронты ----

    def next_front_slots(self, count: int) -> list[tuple[int, int]]:
        """Следующие (номер фронта, адрес 0/1) начиная с курсора.

        Если известен рабочий адрес, каждый следующий слот — следующий фронт на
        том же адресе; иначе соседние слоты чередуют адреса одного фронта.
        """
        if not self._front_slots:
            return []
        fronts = self._front_slots // 2
        result: list[tuple[int, int]] = []
        for step in range(max(0, int(count))):
            slot = (self._front_cursor + step) % self._front_slots
            if self._front_family is not None:
                result.append(((self._front_cursor // 2 + step) % fronts, self._front_family))
            else:
                result.append((slot // 2, slot % 2))
        return result

    def note_front_result(self, front_index: int, family: int, *, tcp_ok: bool, answered: bool) -> None:
        if tcp_ok:
            self._front_family = family
            self._front_family_failures = 0
        elif self._front_family == family:
            self._front_family_failures += 1
            if self._front_family_failures >= FRONT_FAMILY_FORGET_FAILURES:
                self._front_family = None
                self._front_family_failures = 0
        if not self._front_slots:
            return
        slot = int(front_index) * 2 + int(family)
        if answered:
            self._front_cursor = slot % self._front_slots
        elif self._front_family is not None:
            fronts = self._front_slots // 2
            self._front_cursor = ((int(front_index) + 1) % fronts) * 2 + self._front_family
        else:
            self._front_cursor = (slot + 1) % self._front_slots
        self._save()

    # ---- сохранение ----

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as exc:
            log.debug("route health load failed: %s", exc)
            return
        now = self._clock()
        wall = self._wall()
        for key, item in dict(data.get("routes") or {}).items():
            try:
                suppressions = int(item.get("suppressions", 0))
                remaining = float(item.get("until", 0)) - wall
            except (AttributeError, TypeError, ValueError):
                continue
            if suppressions <= 0:
                continue
            entry = _Entry(suppressions=suppressions)
            if remaining > 0:
                entry.suppressed_until = now + min(remaining, RESTORED_SUPPRESS_MAX_SECONDS)
            self._entries[str(key)] = entry
        front = data.get("front") or {}
        try:
            family = front.get("family")
            self._front_family = int(family) if family in (0, 1) else None
            cursor = int(front.get("cursor", self._front_cursor))
            if self._front_slots:
                self._front_cursor = cursor % self._front_slots
        except (AttributeError, TypeError, ValueError):
            pass

    def _save(self) -> None:
        if self._path is None:
            return
        now = self._clock()
        wall = self._wall()
        routes = {
            key: {
                "suppressions": entry.suppressions,
                "until": round(wall + max(0.0, entry.suppressed_until - now), 1),
            }
            for key, entry in self._entries.items()
            if entry.suppressions > 0
        }
        payload = {
            "routes": routes,
            "front": {"family": self._front_family, "cursor": self._front_cursor},
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._path)
        except Exception as exc:
            log.debug("route health save failed: %s", exc)


__all__ = [
    "CDN_FAILURES_BEFORE_SUPPRESS",
    "FAILURES_BEFORE_SUPPRESS",
    "RouteHealth",
    "address_key",
    "cdn_key",
]
