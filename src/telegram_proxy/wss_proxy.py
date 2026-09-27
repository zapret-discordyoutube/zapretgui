# telegram_proxy/wss_proxy.py
"""Локальный прокси Telegram: приём соединений Desktop и выбор маршрута.

Цепочка:

    Telegram Desktop
      ├ SOCKS5 (proxy/socks5.py) → 64 байта заголовка → obfs.parse_plain_client
      └ MTProxy (+ Fake TLS, proxy/fake_tls.py)       → obfs.parse_secret_client
            ↓
      proxy/session.TelegramSession: план маршрутов (proxy/routes.py),
      повтор пакетов на следующий маршрут, пока сервер молчит, пересылка.

Главный путь — WSS (релей kwsN, фронты Cloudflare, туннель через воркер),
запасной — внешний SOCKS5. Не-Telegram трафик и HTTP-транспорт (порт 80) идут
обычным TCP: WSS для них не подходит.
"""

import asyncio
import errno
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from telegram_proxy.proxy import socks5
from telegram_proxy.proxy.cloudflare import CloudflareFallbackConfig
from telegram_proxy.proxy.dc_map import IP_TO_DC, dc_to_tcp_endpoint, ip_to_dc, is_telegram_ip
from telegram_proxy.proxy.fake_tls import normalize_fake_tls_domain, read_mtproxy_client_init
from telegram_proxy.proxy.health import RouteHealth
from telegram_proxy.proxy.mtproxy import normalize_secret
from telegram_proxy.proxy.obfs import (
    HEADER_LEN,
    is_http_transport,
    parse_plain_client,
    parse_secret_client,
)
from telegram_proxy.proxy.pool import WsSparePool
from telegram_proxy.proxy.route_catalog import CDN_FRONTS
from telegram_proxy.proxy.routes import PlanInput
from telegram_proxy.proxy.routing import (
    UpstreamProxyConfig,
    check_relay_reachable,
    should_route_upstream,
)
from telegram_proxy.proxy.session import TelegramSession
from telegram_proxy.proxy.stats import ProxyStats
from telegram_proxy.proxy.upstream_controller import UpstreamRuntimeSnapshot, endpoint_display_name
from telegram_proxy.proxy.upstream_runtime import (
    FULL_CONNECT_TIMEOUT,
    OpenedUpstream,
    UpstreamBusyError,
    UpstreamConnectError,
    UpstreamConnectionExecutor,
    UpstreamStaleError,
    UpstreamTargetRejectedError,
    UpstreamUnavailableError,
)
from telegram_proxy.proxy.ws import apply_socket_options

log = logging.getLogger("tg_proxy")

# Не-Telegram трафик и HTTP-транспорт: обычный TCP.
CONNECT_TIMEOUT = 10.0
HTTP_DIRECT_CONNECT_TIMEOUT = 3.0
INIT_READ_TIMEOUT = 15.0
RAW_READ_CHUNK = 128 * 1024

UPSTREAM_CONNECT_TIMEOUT = FULL_CONNECT_TIMEOUT

_KNOWN_DCS = frozenset({1, 2, 3, 4, 5, 203})

# Коды «адрес уже используется» для повторного bind после рестарта.
_ADDRESS_IN_USE_WINERRORS = frozenset({10048, 10013})


def _is_address_in_use_error(exc: OSError) -> bool:
    if isinstance(exc, OSError) and exc.errno == errno.EADDRINUSE:
        return True
    return getattr(exc, "winerror", None) in _ADDRESS_IN_USE_WINERRORS


def _is_domain(host: str) -> bool:
    if ":" in host:
        return False
    return not all(c.isdigit() or c == "." for c in host)


def _error_text(exc: BaseException) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


class _Socks5UdpRelayProtocol(asyncio.DatagramProtocol):
    def __init__(self, *, label: str, side: str, log_callback: Callable[[str], None]):
        self.label = label
        self.side = side
        self.log_callback = log_callback
        self.transport: asyncio.DatagramTransport | None = None
        self.peer: "_Socks5UdpRelayProtocol | None" = None
        self.fixed_target: tuple[str, int] | None = None
        self.client_addr: tuple[str, int] | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        peer = self.peer
        if peer is None or peer.transport is None:
            return
        if self.side == "client":
            self.client_addr = addr
            try:
                packet = socks5.parse_udp_packet(data)
                self.log_callback(
                    f"[{self.label}] UDP -> {packet.target_host}:{packet.target_port} via upstream SOCKS5"
                )
            except Exception as exc:
                self.log_callback(f"[{self.label}] UDP packet rejected: {type(exc).__name__}: {exc}")
                return
            target = peer.fixed_target
            if target is not None:
                peer.transport.sendto(data, target)
            return

        client_addr = peer.client_addr
        if client_addr is not None:
            peer.transport.sendto(data, client_addr)


class _Socks5UdpRelay:
    def __init__(
        self,
        *,
        local_transport,
        upstream_transport,
        upstream_session: socks5.UdpAssociateSession,
        local_host: str,
        local_port: int,
    ):
        self.local_transport = local_transport
        self.upstream_transport = upstream_transport
        self.upstream_session = upstream_session
        self.local_host = local_host
        self.local_port = local_port

    def close(self) -> None:
        for closer in (
            self.local_transport.close,
            self.upstream_transport.close,
            self.upstream_session.writer.close,
        ):
            try:
                closer()
            except Exception:
                pass


class TelegramWSProxy:
    """Асинхронный сервер: SOCKS5 или MTProxy на входе, WSS/SOCKS5/TCP на выходе."""

    def __init__(
        self,
        port: int = 1353,
        mode: str = "socks5",
        on_log: Optional[Callable[[str], None]] = None,
        on_upstream_state: Optional[Callable[[UpstreamRuntimeSnapshot], None]] = None,
        host: str = "127.0.0.1",
        upstream_config: Optional[UpstreamProxyConfig] = None,
        cloudflare_config: Optional[CloudflareFallbackConfig] = None,
        mtproxy_secret: str = "",
        dc_endpoint_overrides: Optional[dict[int, str]] = None,
        pool_size: int = 4,
        buffer_kb: int = 256,
        fake_tls_domain: str = "",
        proxy_protocol: bool = False,
        route_health_path: Path | str | None = None,
    ):
        self._port = port
        self._mode = mode
        self._host = host
        self._on_log = on_log
        self._on_upstream_state = on_upstream_state
        self._upstream = upstream_config or UpstreamProxyConfig()
        self._cloudflare = cloudflare_config or CloudflareFallbackConfig()
        self._mtproxy_secret = normalize_secret(mtproxy_secret)
        self._dc_endpoint_overrides = dict(dc_endpoint_overrides or {})
        self._pool_size = max(0, min(32, int(pool_size if pool_size is not None else 4)))
        self.buffer_size = max(4, min(4096, int(buffer_kb or 256))) * 1024
        self._fake_tls_domain = normalize_fake_tls_domain(fake_tls_domain)
        self._proxy_protocol = bool(proxy_protocol)
        self._route_health_path = route_health_path
        self._servers: list[asyncio.Server] = []
        self._tasks: set[asyncio.Task] = set()
        self._running = False
        self.stats = ProxyStats()
        self.route_health = RouteHealth(path=route_health_path, front_count=len(CDN_FRONTS))
        self.ws_pool = WsSparePool(self.stats, enabled=self._pool_size > 0, buffer_size=self.buffer_size)
        self._upstream_runtime = self._new_upstream_runtime()
        self._mtproxy_invalid_init_log_marks = {1, 5, 20, 50}
        self._mtproxy_bad_handshake_log_marks = {1, 5, 20, 50}

    def _new_upstream_runtime(self) -> UpstreamConnectionExecutor:
        return UpstreamConnectionExecutor(
            self._upstream,
            connect_limit=self._pool_size or 4,
            on_snapshot=self._on_upstream_state,
            on_log=self.log,
        )

    # ---- то, что нужно сессиям (session.SessionHost) ----

    @property
    def mode_label(self) -> str:
        return "MTProxy" if self._mode == "mtproxy" else "SOCKS5"

    def log(self, msg: str) -> None:
        log.info(msg)
        if self._on_log:
            try:
                self._on_log(msg)
            except Exception:
                pass

    def record_route(self, *, dc: int, is_media: bool, route: str, status: str, reason: str = "") -> None:
        self.stats.record_route_event(dc=dc, is_media=is_media, route=route, status=status, reason=reason)

    def log_route_detail(
        self,
        label: str,
        *,
        route: str,
        dc: int,
        is_media: bool,
        target: str = "",
        result: str,
        reason: str = "",
        next_step: str = "",
        elapsed: float | None = None,
    ) -> None:
        parts = [
            f"[{label}] route={route}",
            f"mode={self.mode_label}",
            f"dc={int(dc)}",
            f"media={'yes' if is_media else 'no'}",
        ]
        if target:
            parts.append(f"target={target}")
        parts.append(f"result={result}")
        if reason:
            parts.append(f"reason={reason}")
        if next_step:
            parts.append(f"next={next_step}")
        if elapsed is not None:
            parts.append(f"elapsed={elapsed:.1f}s")
        self.log(" ".join(parts))

    def plan_input(self, *, dc: int, is_media: bool, target_host: str, target_port: int) -> PlanInput:
        return PlanInput(
            dc=dc,
            is_media=is_media,
            target_host=target_host,
            target_port=target_port,
            upstream=self._upstream,
            cloudflare=self._cloudflare,
        )

    async def open_upstream(
        self,
        *,
        target_host: str,
        target_port: int,
        label: str,
        dc: int,
        is_media: bool,
    ) -> OpenedUpstream | None:
        media_tag = " media" if is_media else ""
        try:
            opened = await self._upstream_runtime.open_connection(target_host, target_port)
        except (UpstreamBusyError, UpstreamUnavailableError) as exc:
            self.log(f"[{label}] DC{dc}{media_tag} upstream skipped: {exc}")
            self.record_route(dc=dc, is_media=is_media, route="внешний SOCKS5", status="пропуск", reason=str(exc))
            return None
        except (UpstreamTargetRejectedError, UpstreamConnectError, UpstreamStaleError) as exc:
            rejected = isinstance(exc, UpstreamTargetRejectedError)
            endpoint_name = endpoint_display_name(getattr(exc, "endpoint", None)) or "текущий сервер"
            self.stats.failed_connections += 1
            self.log_route_detail(
                label,
                route="upstream SOCKS5",
                dc=dc,
                is_media=is_media,
                target=f"{target_host}:{target_port} via {endpoint_name}",
                result="error",
                reason=_error_text(exc),
                next_step=(
                    "текущий сервер сохранён; проверяются правила доступа к адресу"
                    if rejected
                    else "следующее соединение использует общий активный сервер"
                ),
            )
            self.record_route(
                dc=dc, is_media=is_media, route="внешний SOCKS5", status="ошибка", reason=_error_text(exc)
            )
            return None

        apply_socket_options(opened.writer.transport, self.buffer_size)
        self.log(
            f"[{label}] DC{dc}{media_tag} upstream connected via "
            f"{endpoint_display_name(opened.endpoint)} ({opened.elapsed:.1f}s)"
        )
        self.stats.upstream_connections += 1
        return opened

    def spawn_background(self, coro) -> None:
        """Фоновая задача сессии, которая может пережить соединение клиента."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def upstream_recv_ok(self, opened: OpenedUpstream) -> None:
        self._upstream_runtime.record_recv_ok(opened)

    def upstream_zero_recv(self, opened: OpenedUpstream) -> None:
        self._upstream_runtime.record_zero_recv(opened)

    def upstream_release(self, opened: OpenedUpstream) -> None:
        self._upstream_runtime.release(opened)

    # ---- жизненный цикл ----

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def upstream_state(self) -> UpstreamRuntimeSnapshot:
        return self._upstream_runtime.snapshot()

    async def start(self) -> None:
        if self._running:
            return

        self.stats = ProxyStats()
        self.ws_pool = WsSparePool(self.stats, enabled=self._pool_size > 0, buffer_size=self.buffer_size)
        self._upstream_runtime = self._new_upstream_runtime()

        handler = self._handle_mtproxy_client if self._mode == "mtproxy" else self._handle_socks5_client
        # После рестарта предыдущий сокет может освобождаться с задержкой —
        # повторяем bind вместо мгновенного падения.
        bind_deadline = time.monotonic() + 3.0
        while True:
            try:
                server = await asyncio.start_server(handler, self._host, self._port, start_serving=False)
                break
            except OSError as exc:
                if not _is_address_in_use_error(exc) or time.monotonic() >= bind_deadline:
                    raise
                self.log(f"Port {self._host}:{self._port} busy ({exc}), retrying bind...")
                await asyncio.sleep(0.25)
        self._servers.append(server)
        for srv in self._servers:
            await srv.start_serving()

        self._running = True
        self.log(f"{self.mode_label} proxy started on {self._host}:{self._port}")
        self._upstream_runtime.emit_snapshot(force=True)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self.log("Stopping proxy...")

        # Сначала освобождаем порт: рестарт ждёт stop с коротким таймаутом.
        # close() сразу перестаёт слушать, а wait_closed() с Python 3.12 ждёт
        # ещё и все живые соединения — поэтому их задачи отменяются раньше.
        for srv in self._servers:
            srv.close()

        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        await self.ws_pool.close_all()
        await self._upstream_runtime.close()

        for srv in self._servers:
            try:
                await asyncio.wait_for(srv.wait_closed(), timeout=2.0)
            except TimeoutError:
                self.log("Proxy stop: some client sockets are still closing")
        self._servers.clear()
        self.log("Proxy stopped")

    def apply_upstream_config(self, upstream_config: Optional[UpstreamProxyConfig]) -> None:
        """Горячая замена внешнего SOCKS без рестарта (из потока event loop прокси)."""
        self._upstream = upstream_config or UpstreamProxyConfig()
        self._upstream_runtime.apply_config(self._upstream)
        target = str(self._upstream.preset_name or self._upstream.host or "").strip() or "manual"
        self.log(f"Upstream config applied without restart: {target}")

    # ---- UDP (звонки) через внешний SOCKS5 ----

    async def _open_udp_relay(self, label: str) -> _Socks5UdpRelay:
        if not self._upstream.enabled or not self._upstream.udp_enabled:
            raise socks5.Socks5Error("SOCKS5 UDP relay is disabled")

        endpoint = self._upstream_runtime.current_endpoint()
        if endpoint is None:
            raise socks5.Socks5Error("No upstream SOCKS5 proxy for UDP relay")

        self.log(f"[{label}] UDP ASSOCIATE -> upstream proxy {endpoint.host}:{endpoint.port}")
        upstream_session = await socks5.open_udp_associate_via_socks5(
            endpoint.host,
            endpoint.port,
            username=endpoint.username,
            password=endpoint.password,
            timeout=UPSTREAM_CONNECT_TIMEOUT,
            tls=endpoint.tls,
            tls_server_name=endpoint.tls_server_name,
            tls_verify=endpoint.tls_verify,
        )

        loop = asyncio.get_running_loop()
        local_protocol = _Socks5UdpRelayProtocol(label=label, side="client", log_callback=self.log)
        upstream_protocol = _Socks5UdpRelayProtocol(label=label, side="upstream", log_callback=self.log)
        local_protocol.peer = upstream_protocol
        upstream_protocol.peer = local_protocol
        upstream_protocol.fixed_target = (upstream_session.relay_host, upstream_session.relay_port)

        local_bind_host = self._host if self._host != "0.0.0.0" else "127.0.0.1"
        local_transport = None
        try:
            local_transport, _ = await loop.create_datagram_endpoint(
                lambda: local_protocol,
                local_addr=(local_bind_host, 0),
            )
            upstream_transport, _ = await loop.create_datagram_endpoint(
                lambda: upstream_protocol,
                local_addr=("0.0.0.0", 0),
            )
        except Exception:
            try:
                upstream_session.writer.close()
            except Exception:
                pass
            if local_transport is not None:
                local_transport.close()
            raise
        local_sock = local_transport.get_extra_info("sockname") or (local_bind_host, 0)
        self.log(
            f"[{label}] UDP relay ready: local {local_sock[0]}:{local_sock[1]} "
            f"-> {upstream_session.relay_host}:{upstream_session.relay_port}"
        )
        return _Socks5UdpRelay(
            local_transport=local_transport,
            upstream_transport=upstream_transport,
            upstream_session=upstream_session,
            local_host=str(local_sock[0]),
            local_port=int(local_sock[1]),
        )

    # ---- обычный TCP: не-Telegram, HTTP-транспорт, непонятный поток ----

    async def _relay_raw(self, client_reader, client_writer, remote_reader, remote_writer, label: str) -> int:
        sent_total = 0
        recv_total = 0
        started = time.monotonic()

        async def forward(src, dst, *, outgoing: bool) -> None:
            nonlocal sent_total, recv_total
            while True:
                data = await src.read(RAW_READ_CHUNK)
                if not data:
                    return
                if outgoing:
                    sent_total += len(data)
                    self.stats.bytes_sent += len(data)
                else:
                    recv_total += len(data)
                    self.stats.bytes_received += len(data)
                dst.write(data)
                await dst.drain()

        tasks = [
            asyncio.create_task(forward(client_reader, remote_writer, outgoing=True)),
            asyncio.create_task(forward(remote_reader, client_writer, outgoing=False)),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except BaseException:
                    pass
            try:
                remote_writer.close()
            except Exception:
                pass
            if label:
                self.log(
                    f"[{label}] tcp relay done: sent={sent_total} recv={recv_total} "
                    f"({time.monotonic() - started:.1f}s)"
                )
        return recv_total

    async def _raw_via_upstream(self, reader, writer, first: bytes, target_host: str, target_port: int, label: str) -> bool:
        opened = await self.open_upstream(
            target_host=target_host, target_port=target_port, label=label, dc=0, is_media=False
        )
        if opened is None:
            return False
        try:
            opened.writer.write(first)
            await opened.writer.drain()
            recv_total = await self._relay_raw(reader, writer, opened.reader, opened.writer, label)
            if recv_total > 0:
                self.upstream_recv_ok(opened)
            else:
                self.upstream_zero_recv(opened)
        finally:
            self.upstream_release(opened)
        return True

    async def _raw_tcp_session(self, reader, writer, first: bytes, target_host: str, target_port: int, label: str) -> None:
        """HTTP-транспорт Telegram (порт 80) и потоки без obfuscated2: WSS не подходит."""
        if should_route_upstream(self._upstream, mode="always"):
            self.log(f"[{label}] HTTP transport -> upstream (always mode)")
            await self._raw_via_upstream(reader, writer, first, target_host, target_port, label)
            return

        self.stats.passthrough_connections += 1
        started = time.monotonic()
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(target_host, target_port),
                timeout=HTTP_DIRECT_CONNECT_TIMEOUT,
            )
        except Exception as exc:
            fallback = self._upstream.enabled
            self.log_route_detail(
                label,
                route="HTTP direct TCP",
                dc=0,
                is_media=False,
                target=f"{target_host}:{target_port}",
                result="error",
                reason=_error_text(exc),
                next_step="upstream SOCKS5 fallback" if fallback else "none; HTTP transport cannot use WSS",
                elapsed=time.monotonic() - started,
            )
            self.record_route(dc=0, is_media=False, route="HTTP direct TCP", status="ошибка", reason=_error_text(exc))
            if fallback:
                await self._raw_via_upstream(reader, writer, first, target_host, target_port, label)
            return
        apply_socket_options(remote_writer.transport, self.buffer_size)
        self.log_route_detail(
            label,
            route="HTTP direct TCP",
            dc=0,
            is_media=False,
            target=f"{target_host}:{target_port}",
            result="connected",
            reason="HTTP transport cannot use WSS",
            elapsed=time.monotonic() - started,
        )
        remote_writer.write(first)
        await remote_writer.drain()
        await self._relay_raw(reader, writer, remote_reader, remote_writer, label)

    # ---- входы ----

    def _track_task(self) -> asyncio.Task | None:
        task = asyncio.current_task()
        if task:
            self._tasks.add(task)
        self.stats.total_connections += 1
        self.stats.active_connections += 1
        return task

    async def _finish_connection(self, task: asyncio.Task | None, writer) -> None:
        self.stats.active_connections -= 1
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        if task:
            self._tasks.discard(task)

    async def _handle_socks5_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = self._track_task()
        peer = writer.get_extra_info("peername", ("?", 0))
        label = f"{peer[0]}:{peer[1]}"
        udp_relay: _Socks5UdpRelay | None = None

        try:
            async def create_udp_relay() -> tuple[str, int]:
                nonlocal udp_relay
                try:
                    udp_relay = await self._open_udp_relay(label)
                    return udp_relay.local_host, udp_relay.local_port
                except Exception as exc:
                    self.log(f"[{label}] UDP relay failed: {type(exc).__name__}: {exc}")
                    raise

            result = await socks5.handshake(
                reader,
                writer,
                allow_udp_associate=bool(self._upstream.enabled and self._upstream.udp_enabled),
                on_udp_associate=create_udp_relay,
            )
            if result is None:
                return

            if isinstance(result, socks5.UdpAssociateRequest):
                self.log(f"[{label}] UDP ASSOCIATE accepted; client hint {result.client_host}:{result.client_port}")
                await reader.read()
                return

            target_host, target_port = result
            if _is_domain(target_host) or not is_telegram_ip(target_host):
                await self._passthrough(reader, writer, target_host, target_port, label)
                return

            self.log(f"[{label}] -> {target_host}:{target_port}")
            try:
                init = await asyncio.wait_for(reader.readexactly(HEADER_LEN), timeout=INIT_READ_TIMEOUT)
            except (asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
                self.log(f"[{label}] no init packet: {type(exc).__name__}")
                return

            client = None if is_http_transport(init) else parse_plain_client(init)
            if client is None:
                kind = "HTTP transport" if is_http_transport(init) else "not obfuscated2"
                self.log(f"[{label}] {kind} -> plain TCP")
                await self._raw_tcp_session(reader, writer, init, target_host, target_port, label)
                return

            dc, is_media = client.dc, client.is_media
            if dc not in _KNOWN_DCS:
                entry = IP_TO_DC.get(target_host)
                dc, is_media = entry if entry is not None else (ip_to_dc(target_host), False)
                self.log(f"[{label}] DC from IP table: DC{dc}")
            if ":" in target_host:
                # IPv6 Telegram часто недоступен; маршрутам нужен IPv4 того же DC.
                target_host, target_port = dc_to_tcp_endpoint(dc, self._dc_endpoint_overrides, is_media=is_media)
            self.log(f"[{label}] DC{dc}{' media' if is_media else ''} ({target_host}:{target_port})")

            await TelegramSession(
                self,
                reader=reader,
                writer=writer,
                client=client,
                dc=dc,
                is_media=is_media,
                target_host=target_host,
                target_port=target_port,
                label=label,
            ).run()
        except (asyncio.CancelledError, ConnectionError, OSError):
            pass
        except Exception:
            self.stats.failed_connections += 1
            log.exception("[%s] SOCKS5 handler error", label)
        finally:
            if udp_relay is not None:
                udp_relay.close()
            await self._finish_connection(task, writer)

    async def _passthrough(self, reader, writer, target_host: str, target_port: int, label: str) -> None:
        self.stats.passthrough_connections += 1
        log.debug("[%s] passthrough -> %s:%d", label, target_host, target_port)
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(target_host, target_port),
                timeout=CONNECT_TIMEOUT,
            )
        except Exception as exc:
            log.warning("[%s] passthrough connect failed: %s", label, exc)
            return
        apply_socket_options(remote_writer.transport, self.buffer_size)
        await self._relay_raw(reader, writer, remote_reader, remote_writer, "")

    @staticmethod
    def _should_log_mtproxy_problem(count: int, marks: set[int]) -> bool:
        return int(count) in marks or (int(count) > 0 and int(count) % 100 == 0)

    def _record_mtproxy_invalid_init(self, label: str) -> None:
        self.stats.mtproxy_invalid_init_count += 1
        count = int(self.stats.mtproxy_invalid_init_count)
        self.stats.mtproxy_last_problem = (
            "нет MTProxy init packet: проверьте тип прокси, старые записи, "
            "Fake TLS/secret или авто-проверку Telegram"
        )
        if not self._should_log_mtproxy_problem(count, self._mtproxy_invalid_init_log_marks):
            return
        self.log(
            f"[{label}] MTProxy init packet не получен; повторов: {count}. "
            "Что это значит: Telegram подключился к MTProxy-порту, но не прислал "
            "первый MTProxy-пакет. Что делать: проверьте тип прокси в Telegram, "
            "удалите старые записи 127.0.0.1, проверьте secret/Fake TLS; "
            "одиночные проверки клиента могут закрываться сами."
        )

    def _record_mtproxy_bad_handshake(self, label: str) -> None:
        self.stats.mtproxy_bad_handshake_count += 1
        count = int(self.stats.mtproxy_bad_handshake_count)
        self.stats.mtproxy_last_problem = "init есть, но secret или тип secret dd/ee не подошёл"
        if not self._should_log_mtproxy_problem(count, self._mtproxy_bad_handshake_log_marks):
            return
        self.log(
            f"[{label}] MTProxy init получен, но не расшифровался; повторов: {count}. "
            "Чаще всего это неверный secret, старый прокси в Telegram или mismatch "
            "типа secret dd/ee."
        )

    async def _handle_mtproxy_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = self._track_task()
        peer = writer.get_extra_info("peername", ("?", 0))
        label = f"{peer[0]}:{peer[1]}"
        client_writer = writer

        try:
            if not self._mtproxy_secret:
                self.stats.failed_connections += 1
                self.log(f"[{label}] MTProxy secret is not configured")
                return

            incoming = await read_mtproxy_client_init(
                reader,
                writer,
                self._mtproxy_secret,
                label,
                fake_tls_domain=self._fake_tls_domain,
                proxy_protocol=self._proxy_protocol,
            )
            if incoming is None:
                self._record_mtproxy_invalid_init(label)
                return
            client_writer = incoming.writer
            label = incoming.label

            client = parse_secret_client(incoming.init, self._mtproxy_secret)
            if client is None or client.dc <= 0:
                self.stats.failed_connections += 1
                self._record_mtproxy_bad_handshake(label)
                return

            target_host, target_port = dc_to_tcp_endpoint(
                client.dc, self._dc_endpoint_overrides, is_media=client.is_media
            )
            media_tag = " media" if client.is_media else ""
            self.log(f"[{label}] MTProxy DC{client.dc}{media_tag} -> {target_host}:{target_port}")

            await TelegramSession(
                self,
                reader=incoming.reader,
                writer=incoming.writer,
                client=client,
                dc=client.dc,
                is_media=client.is_media,
                target_host=target_host,
                target_port=target_port,
                label=label,
            ).run()
        except (asyncio.CancelledError, ConnectionError, OSError):
            pass
        except Exception:
            self.stats.failed_connections += 1
            log.exception("[%s] MTProxy handler error", label)
        finally:
            await self._finish_connection(task, client_writer)


__all__ = [
    "CloudflareFallbackConfig",
    "ProxyStats",
    "TelegramWSProxy",
    "UpstreamProxyConfig",
    "check_relay_reachable",
]
