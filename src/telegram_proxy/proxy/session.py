"""Одно соединение Telegram Desktop через прокси.

Цепочка:

1. Вход (SOCKS5 или MTProxy) уже разобрал заголовок клиента: формат пакетов,
   DC, шифры клиента (obfs.ClientSide).
2. Насос клиента непрерывно читает поток Desktop, расшифровывает и режет его на
   MTProto-пакеты.
3. План маршрутов (routes.build_plan) перебирается по очереди. Для каждого
   маршрута собирается новый заголовок obfuscated2, и все накопленные пакеты
   уходят заново: один пакет — один WS-кадр, заголовок — в кадре с первым.
4. Маршрут считается рабочим только после первого ответа сервера. Пока ответа
   нет, клиенту не уходит ни одного байта, поэтому молчащий маршрут можно
   незаметно сменить на следующий: Desktop не видит сбоев и не выключает
   MTProxy как «неправильно настроенный».
5. После первого ответа — обычная пересылка в обе стороны с перекодировкой
   пакетов сервера в формат клиента.
"""

from __future__ import annotations

import asyncio
import ssl
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from telegram_proxy.proxy import ws as ws_transport
from telegram_proxy.proxy.obfs import (
    ABRIDGED,
    ClientSide,
    ObfsProtocolError,
    PacketReader,
    StreamCipher,
    build_server_header,
    dc_field_value,
    encode_packet,
)
from telegram_proxy.proxy.routes import (
    KIND_DIRECT,
    KIND_FRONT,
    KIND_RELAY,
    KIND_TUNNEL,
    KIND_UPSTREAM,
    KIND_USER_DOMAIN,
    KIND_USER_WORKER,
    PlanInput,
    Route,
    build_plan,
)

if TYPE_CHECKING:
    from telegram_proxy.proxy.upstream_runtime import OpenedUpstream


# Сторож первого ответа: после последнего отправленного пакета сервер должен
# ответить за это время. Медиа-соединения отвечают медленнее (ZaStoGram).
WATCHDOG_SECONDS = 5.5
MEDIA_WATCHDOG_SECONDS = 20.0
FIRST_PACKET_TIMEOUT = 15.0
DIRECT_CONNECT_TIMEOUT = 3.0
READ_CHUNK = 128 * 1024
# Пока маршрут не выбран, пакеты клиента копятся для повтора. Больше этого
# чтение клиента приостанавливается.
MAX_REPLAY_BYTES = 4 * 1024 * 1024


class SessionHost(Protocol):
    """То, что сессии нужно от TelegramWSProxy."""

    stats: object
    route_health: object
    ws_pool: object
    mode_label: str
    buffer_size: int

    def log(self, message: str) -> None: ...

    def record_route(self, *, dc: int, is_media: bool, route: str, status: str, reason: str = "") -> None: ...

    def log_route_detail(self, label: str, **kwargs) -> None: ...

    async def open_upstream(
        self, *, target_host: str, target_port: int, label: str, dc: int, is_media: bool
    ) -> "OpenedUpstream | None": ...

    def upstream_recv_ok(self, opened: "OpenedUpstream") -> None: ...

    def upstream_zero_recv(self, opened: "OpenedUpstream") -> None: ...

    def upstream_release(self, opened: "OpenedUpstream") -> None: ...

    def plan_input(self, *, dc: int, is_media: bool, target_host: str, target_port: int) -> PlanInput: ...

    def spawn_background(self, coro) -> None: ...


class _RouteFailed(Exception):
    def __init__(self, reason: str, *, tcp_reached: bool = True, counted: bool = True, silent: bool = False):
        self.tcp_reached = tcp_reached
        self.counted = counted
        self.silent = silent
        super().__init__(reason)


class _ClientGone(Exception):
    """Desktop закрыл соединение раньше ответа сервера.

    deadline — до какого момента ещё идёт срок сторожа для уже отправленных
    пакетов (None — к серверу ничего не ушло).
    """

    def __init__(self, deadline: float | None = None):
        self.deadline = deadline
        super().__init__("client closed before answer")


class _ServerConn:
    """Открытое соединение к Telegram по одному маршруту."""

    def __init__(self, *, ws: ws_transport.WebSocket | None = None, reader=None, writer=None, opened=None):
        self.pooled = False
        self.ws = ws
        self.reader = reader
        self.writer = writer
        self.opened = opened

    async def send(self, chunks: list[bytes]) -> None:
        if self.ws is not None:
            await self.ws.send_many(chunks)
            return
        self.writer.write(b"".join(chunks))
        await self.writer.drain()

    @property
    def raw_reader(self):
        return self.ws.reader if self.ws is not None else self.reader

    async def recv(self) -> bytes | None:
        if self.ws is not None:
            return await self.ws.recv()
        data = await self.reader.read(READ_CHUNK)
        return data or None

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
            return
        try:
            self.writer.close()
            await asyncio.wait_for(self.writer.wait_closed(), timeout=1.0)
        except BaseException:
            pass


@dataclass(slots=True)
class _Committed:
    route: Route
    conn: _ServerConn
    cipher: StreamCipher
    first_data: bytes
    sent_index: int
    elapsed: float


class TelegramSession:
    def __init__(
        self,
        host: SessionHost,
        *,
        reader,
        writer,
        client: ClientSide,
        dc: int,
        is_media: bool,
        target_host: str,
        target_port: int,
        label: str,
    ) -> None:
        self._host = host
        self._reader = reader
        self._writer = writer
        self._client = client
        self._dc = int(dc)
        self._is_media = bool(is_media)
        self._target_host = target_host
        self._target_port = int(target_port or 443)
        self._label = label
        self._packets: list[bytes] = []
        self._buffered = 0
        self._packet_event = asyncio.Event()
        self._client_eof = False
        self._committed = False
        self._client_reader = PacketReader(client.framing)
        self._server_reader = PacketReader(ABRIDGED)
        self._sent_total = 0
        self._recv_total = 0

    @property
    def _watchdog(self) -> float:
        return MEDIA_WATCHDOG_SECONDS if self._is_media else WATCHDOG_SECONDS

    @property
    def _media_tag(self) -> str:
        return " media" if self._is_media else ""

    # ---- насос клиента ----

    async def _pump_client(self) -> None:
        try:
            while True:
                while not self._committed and self._buffered > MAX_REPLAY_BYTES:
                    await asyncio.sleep(0.05)
                data = await self._reader.read(READ_CHUNK)
                if not data:
                    break
                self._sent_total += len(data)
                self._host.stats.bytes_sent += len(data)
                packets = self._client_reader.feed(self._client.cipher.decryptor.update(data))
                if packets:
                    self._packets.extend(packets)
                    self._buffered += sum(len(item) for item in packets)
                    self._packet_event.set()
        except ObfsProtocolError as exc:
            self._host.log(f"[{self._label}] client stream error: {exc}")
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self._client_eof = True
            self._packet_event.set()

    # ---- основной ход ----

    async def run(self) -> None:
        pump = asyncio.create_task(self._pump_client())
        try:
            if not await self._wait_first_packet():
                return
            request = self._host.plan_input(
                dc=self._dc,
                is_media=self._is_media,
                target_host=self._target_host,
                target_port=self._target_port,
            )
            plan = build_plan(request, self._host.route_health)
            for index, route in enumerate(plan):
                next_route = plan[index + 1] if index + 1 < len(plan) else None
                try:
                    committed = await self._attempt(route, next_route)
                except _ClientGone:
                    return
                if committed is not None:
                    await self._relay(committed)
                    return
            self._host.stats.failed_connections += 1
            self._host.log(
                f"[{self._label}] DC{self._dc}{self._media_tag}: all routes failed "
                f"({len(plan)} tried)"
            )
        finally:
            pump.cancel()
            try:
                await pump
            except BaseException:
                pass

    async def _wait_first_packet(self) -> bool:
        deadline = time.monotonic() + FIRST_PACKET_TIMEOUT
        while not self._packets:
            if self._client_eof:
                return False
            self._packet_event.clear()
            if self._packets or self._client_eof:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._host.log(f"[{self._label}] no MTProto packet from client")
                return False
            try:
                await asyncio.wait_for(self._packet_event.wait(), timeout=remaining)
            except TimeoutError:
                pass
        return True

    # ---- попытка маршрута ----

    async def _open(self, route: Route, *, use_pool: bool = True) -> _ServerConn:
        host = self._host
        if route.is_ws:
            pooled = host.ws_pool.take(route) if (use_pool and route.pool_key) else None
            if pooled is not None:
                conn = _ServerConn(ws=pooled)
                conn.pooled = True
                return conn
            target = ws_transport.WsTarget(connect_host=route.connect_host, sni=route.sni, path=route.path)
            try:
                ws = await ws_transport.connect(target, buffer_size=host.buffer_size)
            except ws_transport.WsConnectError as exc:
                raise _RouteFailed(f"{exc.stage}: {exc}", tcp_reached=exc.tcp_reached) from exc
            if route.pool_key:
                host.ws_pool.remember(route)
            return _ServerConn(ws=ws)

        if route.kind == KIND_UPSTREAM:
            opened = await host.open_upstream(
                target_host=route.target_host,
                target_port=route.target_port,
                label=self._label,
                dc=self._dc,
                is_media=self._is_media,
            )
            if opened is None:
                # Причину уже записал контроллер внешнего SOCKS.
                raise _RouteFailed("внешний SOCKS5 недоступен", counted=False)
            return _ServerConn(reader=opened.reader, writer=opened.writer, opened=opened)

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(route.target_host, route.target_port),
                timeout=DIRECT_CONNECT_TIMEOUT,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _RouteFailed(_error_text(exc), tcp_reached=False) from exc
        ws_transport.apply_socket_options(writer.transport, host.buffer_size)
        return _ServerConn(reader=reader, writer=writer)

    def _encrypt_packets(self, cipher: StreamCipher, packets: list[bytes]) -> list[bytes]:
        return [cipher.encryptor.update(encode_packet(ABRIDGED, packet)) for packet in packets]

    async def _attempt(self, route: Route, next_route: Route | None) -> _Committed | None:
        started = time.monotonic()
        conn: _ServerConn | None = None
        try:
            conn = await self._open(route)
            if conn.pooled:
                try:
                    return await self._await_answer(route, conn, started)
                except _RouteFailed as exc:
                    if exc.silent:
                        # Сервер молчал весь срок сторожа: это отказ маршрута,
                        # а не устаревший сокет. Повтор только удвоил бы ожидание.
                        raise
                    # Запасной сокет из пула мог устареть: не винить маршрут,
                    # а один раз открыть свежее соединение.
                    self._host.log(f"[{self._label}] pooled {route.describe()} failed: {exc}; retry fresh")
                    await self._abandon(conn)
                    conn = None
                    conn = await self._open(route, use_pool=False)
            return await self._await_answer(route, conn, started)
        except _ClientGone as gone:
            if conn is not None:
                if gone.deadline is not None:
                    # Desktop ждёт ответа всего 1–2 с на первых попытках и уходит
                    # раньше нашего сторожа. Маршрут всё равно надо оценить,
                    # иначе молчащий релей навсегда останется первым.
                    self._host.spawn_background(self._judge_abandoned(route, conn, gone.deadline, started))
                else:
                    await self._abandon(conn)
            raise
        except _RouteFailed as exc:
            if conn is not None:
                await self._abandon(conn, zero_recv=True)
            self._note_failure(route, exc, started, next_route)
            return None
        except asyncio.CancelledError:
            if conn is not None:
                await self._abandon(conn)
            raise
        except Exception as exc:
            if conn is not None:
                await self._abandon(conn, zero_recv=True)
            self._note_failure(route, _RouteFailed(_error_text(exc)), started, next_route)
            return None

    async def _await_answer(self, route: Route, conn: _ServerConn, started: float) -> _Committed:
        header, cipher = build_server_header(dc_field_value(route.dc_field, self._dc, self._is_media))
        sent = 0
        first = True
        deadline = time.monotonic() + self._watchdog
        recv_task = asyncio.create_task(conn.recv())
        try:
            while True:
                self._packet_event.clear()
                if sent < len(self._packets):
                    chunks = self._encrypt_packets(cipher, self._packets[sent:])
                    if first:
                        chunks[0] = header + chunks[0]
                        first = False
                    await conn.send(chunks)
                    sent = len(self._packets)
                    deadline = time.monotonic() + self._watchdog
                if self._client_eof and not recv_task.done():
                    raise _ClientGone(deadline if sent else None)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _RouteFailed(f"no answer in {self._watchdog:.1f}s (recv=0)", silent=True)
                wake = asyncio.create_task(self._packet_event.wait())
                try:
                    done, _ = await asyncio.wait(
                        {recv_task, wake},
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    wake.cancel()
                if recv_task not in done:
                    continue
                try:
                    data = recv_task.result()
                except ws_transport.WsProtocolError as exc:
                    raise _RouteFailed(f"WebSocket: {exc}") from exc
                except (ConnectionError, OSError, asyncio.IncompleteReadError) as exc:
                    raise _RouteFailed(f"closed before answer: {_error_text(exc)}") from exc
                if not data:
                    raise _RouteFailed("closed before answer (recv=0)")
                return _Committed(
                    route=route,
                    conn=conn,
                    cipher=cipher,
                    first_data=data,
                    sent_index=sent,
                    elapsed=time.monotonic() - started,
                )
        except BaseException:
            if not recv_task.done():
                recv_task.cancel()
                try:
                    await recv_task
                except BaseException:
                    pass
            raise

    async def _judge_abandoned(self, route: Route, conn: _ServerConn, deadline: float, started: float) -> None:
        """Досмотреть маршрут, от которого Desktop ушёл раньше срока сторожа."""
        answered = False
        reason = f"no answer in {self._watchdog:.1f}s (recv=0)"
        try:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                try:
                    data = await asyncio.wait_for(conn.raw_reader.read(1), timeout=remaining)
                    answered = bool(data)
                    if not data:
                        reason = "closed before answer (recv=0)"
                except TimeoutError:
                    pass
                except (ConnectionError, OSError, ssl.SSLError) as exc:
                    reason = f"closed before answer: {_error_text(exc)}"
            if answered:
                self._note_route_ok(route, conn)
                self._host.log(f"[{self._label}] client left early; {route.describe()} answered later")
            else:
                self._note_failure(
                    route,
                    _RouteFailed(f"{reason}; client left early", silent=True),
                    started,
                    None,
                )
        finally:
            await self._abandon(conn, zero_recv=not answered)

    async def _abandon(self, conn: _ServerConn, *, zero_recv: bool = False) -> None:
        if conn.opened is not None:
            if zero_recv:
                self._host.upstream_zero_recv(conn.opened)
            self._host.upstream_release(conn.opened)
        await conn.close()

    def _note_failure(self, route: Route, exc: _RouteFailed, started: float, next_route: Route | None) -> None:
        host = self._host
        health = host.route_health
        reason = str(exc)
        if exc.counted and route.kind != KIND_UPSTREAM:
            if exc.tcp_reached and route.connect_host:
                health.note_tcp_connected(route.connect_host)
            if route.health_key:
                suppressed = health.note_failure(
                    route.health_key,
                    threshold=route.health_threshold,
                    tcp_reached=exc.tcp_reached,
                    address=route.connect_host,
                )
                if suppressed:
                    host.log(
                        f"[{self._label}] route {route.health_key} suppressed for "
                        f"{health.suppressed_for(route.health_key):.0f}s"
                    )
            if route.address_health_key and not exc.tcp_reached:
                health.note_failure(route.address_health_key, tcp_reached=False, address=route.connect_host)
            if route.kind == KIND_FRONT:
                health.note_front_result(
                    route.front_index,
                    route.front_family,
                    tcp_ok=exc.tcp_reached,
                    answered=False,
                )
        if exc.counted and "recv=0" in reason:
            host.stats.recv_zero_count += 1
            host.stats.recv_zero_per_dc[self._dc] = host.stats.recv_zero_per_dc.get(self._dc, 0) + 1
        if route.kind in (KIND_FRONT, KIND_USER_DOMAIN):
            host.stats.cloudflare_failures += 1
        if route.kind == KIND_UPSTREAM and not exc.counted:
            return
        next_step = next_route.label if next_route is not None else "none"
        host.log_route_detail(
            self._label,
            route=route.label,
            dc=self._dc,
            is_media=self._is_media,
            target=route.describe(),
            result="error",
            reason=reason,
            next_step=next_step,
            elapsed=time.monotonic() - started,
        )
        host.record_route(
            dc=self._dc,
            is_media=self._is_media,
            route=route.label,
            status="ошибка",
            reason=reason,
        )

    def _note_route_ok(self, route: Route, conn: _ServerConn) -> None:
        health = self._host.route_health
        if route.connect_host:
            health.note_tcp_connected(route.connect_host)
        if route.health_key:
            health.note_answer(route.health_key)
        if route.kind == KIND_FRONT:
            health.note_front_result(route.front_index, route.front_family, tcp_ok=True, answered=True)
        if conn.opened is not None:
            self._host.upstream_recv_ok(conn.opened)

    def _note_answer(self, committed: _Committed) -> None:
        route = committed.route
        host = self._host
        self._note_route_ok(route, committed.conn)
        counter = {
            KIND_RELAY: "wss_connections",
            KIND_FRONT: "cloudflare_connections",
            KIND_USER_DOMAIN: "cloudflare_connections",
            KIND_TUNNEL: "cloudflare_worker_connections",
            KIND_USER_WORKER: "cloudflare_worker_connections",
            KIND_DIRECT: "tcp_fallback_connections",
        }.get(route.kind)
        if counter:
            setattr(host.stats, counter, int(getattr(host.stats, counter, 0)) + 1)
        host.log_route_detail(
            self._label,
            route=route.label,
            dc=self._dc,
            is_media=self._is_media,
            target=route.describe(),
            result="connected",
            elapsed=committed.elapsed,
        )
        host.record_route(dc=self._dc, is_media=self._is_media, route=route.label, status="OK")

    # ---- пересылка ----

    async def _deliver_to_client(self, cipher: StreamCipher, data: bytes) -> None:
        self._recv_total += len(data)
        self._host.stats.bytes_received += len(data)
        packets = self._server_reader.feed(cipher.decryptor.update(data))
        if not packets:
            return
        out = b"".join(encode_packet(self._client.framing, packet) for packet in packets)
        self._writer.write(self._client.cipher.encryptor.update(out))
        await self._writer.drain()

    async def _relay(self, committed: _Committed) -> None:
        self._committed = True
        conn = committed.conn
        cipher = committed.cipher
        route = committed.route
        self._note_answer(committed)
        del self._packets[: committed.sent_index]
        self._buffered = sum(len(item) for item in self._packets)
        started = time.monotonic()

        async def client_to_server() -> None:
            while True:
                self._packet_event.clear()
                if self._packets:
                    batch = self._packets[:]
                    self._packets.clear()
                    self._buffered = 0
                    await conn.send(self._encrypt_packets(cipher, batch))
                    continue
                if self._client_eof:
                    return
                await self._packet_event.wait()

        async def server_to_client() -> None:
            await self._deliver_to_client(cipher, committed.first_data)
            while True:
                data = await conn.recv()
                if not data:
                    return
                await self._deliver_to_client(cipher, data)

        tasks = [asyncio.create_task(client_to_server()), asyncio.create_task(server_to_client())]
        error = ""
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, (ConnectionError, OSError, asyncio.IncompleteReadError)):
                    error = _error_text(exc)
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except BaseException:
                    pass
            if conn.opened is not None:
                self._host.upstream_release(conn.opened)
            await conn.close()
            if route.health_key and self._recv_total >= route.proof_bytes:
                self._host.route_health.note_proven(route.health_key)
                if route.address_health_key:
                    self._host.route_health.note_proven(route.address_health_key)
            elapsed = time.monotonic() - started
            suffix = f" error={error}" if error else ""
            self._host.log(
                f"[{self._label}] relay done: route={route.label} sent={self._sent_total} "
                f"recv={self._recv_total} ({elapsed:.1f}s){suffix}"
            )


def _error_text(exc: BaseException) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


__all__ = ["TelegramSession", "WATCHDOG_SECONDS", "MEDIA_WATCHDOG_SECONDS"]
