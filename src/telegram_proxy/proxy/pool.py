"""Пул запасных WebSocket: сокеты, уже прошедшие TCP, TLS и upgrade.

Запасной сокет ещё не отправил заголовок obfuscated2, поэтому релей его ни к
чему не привязал, и любое новое соединение того же маршрута может его взять.
Правила из ZaStoGram (jni/tgnet/wss/WssPool.cpp):

* прогреваются только маршруты, которые реально запрашивались, и только
  2 минуты после последнего запроса;
* по одному запасному на маршрут; если за 3 с после взятия пришёл ещё запрос
  (загрузка открывает два соединения), 30 с держатся два запасных;
* первый запасной открывается через 3 с после первого запроса: на старте все
  соединения Telegram открываются разом, и лишние сокеты теряют SYN;
* запасной живёт не дольше 70 с: релеи kws закрывают молчащий сокет через
  ~100 с;
* пауза после неудачи удваивается от 2 до 60 с;
* все фронты одного DC — один маршрут пула: подходит любой готовый фронт.

Отказы запасных сокетов не влияют на здоровье маршрутов.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from telegram_proxy.proxy import ws as ws_transport


log = logging.getLogger("tg_proxy")

DEMAND_TTL = 120.0
FIRST_OPEN_DELAY = 3.0
MAX_IDLE = 70.0
BURST_WINDOW = 3.0
BURST_HOLD = 30.0
RETRY_MIN = 2.0
RETRY_MAX = 60.0


class WsSparePool:
    def __init__(
        self,
        stats,
        *,
        enabled: bool = True,
        buffer_size: int = 256 * 1024,
        connect: Callable[..., Awaitable[ws_transport.WebSocket]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stats = stats
        self._enabled = bool(enabled)
        self._buffer_size = int(buffer_size)
        self._connect = connect or ws_transport.connect
        self._clock = clock
        self._spares: dict[str, list[ws_transport.WebSocket]] = {}
        self._targets: dict[str, ws_transport.WsTarget] = {}
        self._demand_at: dict[str, float] = {}
        self._taken_at: dict[str, float] = {}
        self._burst_until: dict[str, float] = {}
        self._wake: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._closed = False

    @staticmethod
    def _target_of(route) -> ws_transport.WsTarget:
        return ws_transport.WsTarget(connect_host=route.connect_host, sni=route.sni, path=route.path)

    def _usable(self, ws: ws_transport.WebSocket) -> bool:
        if ws.is_closing or ws.has_unread_data():
            return False
        return self._clock() - ws.opened_at < MAX_IDLE

    def take(self, route) -> ws_transport.WebSocket | None:
        """Взять готовый запасной сокет маршрута или None (промах)."""
        key = str(getattr(route, "pool_key", "") or "")
        if not self._enabled or self._closed or not key:
            return None
        now = self._clock()
        self._demand_at[key] = now
        spares = self._spares.get(key) or []
        taken = None
        while spares:
            candidate = spares.pop(0)
            if self._usable(candidate):
                taken = candidate
                break
            asyncio.ensure_future(candidate.close())
        if taken is None:
            self._stats.pool_misses += 1
            return None
        self._stats.pool_hits += 1
        last = self._taken_at.get(key)
        if last is not None and now - last < BURST_WINDOW:
            self._burst_until[key] = now + BURST_HOLD
        self._taken_at[key] = now
        self._ensure_refill(key, first=False)
        return taken

    def remember(self, route) -> None:
        """Маршрут открылся вживую: держать для него запасной."""
        key = str(getattr(route, "pool_key", "") or "")
        if not self._enabled or self._closed or not key:
            return
        self._targets[key] = self._target_of(route)
        first = key not in self._demand_at
        self._demand_at[key] = self._clock()
        self._ensure_refill(key, first=first)

    def _ensure_refill(self, key: str, *, first: bool) -> None:
        event = self._wake.setdefault(key, asyncio.Event())
        event.set()
        task = self._tasks.get(key)
        if task is not None and not task.done():
            return
        if key not in self._targets:
            return
        self._tasks[key] = asyncio.create_task(self._refill(key, first=first))

    async def _sleep_or_wake(self, key: str, seconds: float) -> None:
        event = self._wake.setdefault(key, asyncio.Event())
        event.clear()
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.05, seconds))
        except TimeoutError:
            pass

    async def _refill(self, key: str, *, first: bool) -> None:
        backoff = 0.0
        try:
            if first:
                await asyncio.sleep(FIRST_OPEN_DELAY)
            while not self._closed:
                now = self._clock()
                if now - self._demand_at.get(key, -1e9) > DEMAND_TTL:
                    break
                spares = self._spares.setdefault(key, [])
                for spare in list(spares):
                    if not self._usable(spare):
                        spares.remove(spare)
                        await spare.close()
                want = 2 if now < self._burst_until.get(key, 0.0) else 1
                if len(spares) >= want:
                    oldest = min(spare.opened_at for spare in spares)
                    await self._sleep_or_wake(key, MAX_IDLE - (now - oldest) + 0.1)
                    continue
                target = self._targets.get(key)
                if target is None:
                    break
                try:
                    spare = await self._connect(target, buffer_size=self._buffer_size)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    backoff = min(RETRY_MAX, max(RETRY_MIN, backoff * 2))
                    log.debug("pool %s: spare failed (%s), retry in %.0fs", key, exc, backoff)
                    await self._sleep_or_wake(key, backoff)
                    continue
                backoff = 0.0
                if self._closed:
                    await spare.close()
                    break
                spares.append(spare)
        except asyncio.CancelledError:
            pass
        finally:
            if self._tasks.get(key) is asyncio.current_task():
                self._tasks.pop(key, None)

    async def close_all(self) -> None:
        self._closed = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        for spares in self._spares.values():
            for spare in spares:
                await spare.close()
        self._spares.clear()


__all__ = ["WsSparePool"]
