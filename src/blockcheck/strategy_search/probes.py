"""Сетевые проверки подбора: HTTPS, STUN и игровые UDP-запросы.

Адреса цели определяются один раз до подбора (``resolve_target_addresses``),
и дальше каждая стратегия проверяется на тех же адресах. Так ответ DNS не
меняется от стратегии к стратегии, а таймауты не умножаются на число адресов.

Все функции возвращают ``verdict.ProbeOutcome`` и сами решений не принимают.
"""

from __future__ import annotations

import concurrent.futures
import secrets
import socket
import struct
import time
from collections.abc import Sequence

from blockcheck.strategy_search import verdict
from blockcheck.strategy_search.verdict import ProbeOutcome, UdpProbeSpec

# Бюджет HTTPS-запроса: подключение + шифрование + первый ответ.
HTTPS_TIMEOUT = 4.0
# Сколько ждать следующего куска ответа: обрыв посередине виден по нему.
HTTPS_READ_TIMEOUT = 2.0
# UDP: сколько ждать ответа на один запрос и сколько раз повторить
# (UDP теряет пакеты, одна потеря — ещё не блокировка).
UDP_REPLY_TIMEOUT = 1.2
UDP_ATTEMPTS = 2
DNS_TIMEOUT = 4.0

# Сайты для проверки «интернет вообще жив». Их не блокируют, и пресет пробы
# их не трогает (он ограничен целью), поэтому они не зависят от стратегии.
CONTROL_HOSTS: tuple[str, ...] = ("ya.ru", "www.microsoft.com", "www.google.com")
CONTROL_TIMEOUT = 3.0


# --- Адреса -----------------------------------------------------------------------


def resolve_target_addresses(host: str, port: int, *, udp: bool = False) -> tuple[list[str], str]:
    """Все адреса цели (IPv4 первыми) через DNS системы, как у браузера.

    Возвращает (адреса, ошибка). Ошибка пустая, если хоть что-то нашлось.
    """
    from utils.net_resolve import resolve_addrinfo

    try:
        infos = resolve_addrinfo(
            host,
            int(port),
            timeout=DNS_TIMEOUT,
            family=socket.AF_UNSPEC,
            socktype=socket.SOCK_DGRAM if udp else socket.SOCK_STREAM,
        )
    except (socket.gaierror, OSError) as error:
        return [], f"адрес {host} не найден ({error})"
    v4: list[str] = []
    v6: list[str] = []
    for family, _socktype, _proto, _canon, sockaddr in infos:
        address = str(sockaddr[0]).strip()
        if not address:
            continue
        bucket = v6 if family == socket.AF_INET6 else v4
        if address not in bucket:
            bucket.append(address)
    addresses = v4 + v6
    if not addresses:
        return [], f"адрес {host} не найден"
    return addresses, ""


def stub_addresses(addresses: Sequence[str]) -> list[str]:
    """Адреса-заглушки провайдера или РКН среди ответа DNS."""
    from blockcheck.config import KNOWN_BLOCK_IPS

    return [address for address in addresses if address in KNOWN_BLOCK_IPS]


# --- HTTPS ------------------------------------------------------------------------


def http_body_complete(response: bytes) -> bool:
    """Ответ HTTP получен целиком (по Content-Length, chunked или без тела).

    Без этого сервер, который после полного ответа не закрывает соединение
    или закрывает его сбросом, выглядел бы как «ответ оборвался», и подбор
    считал бы блокировкой то, что работает.
    """
    head_end = response.find(b"\r\n\r\n")
    if head_end < 0:
        return False
    head = response[:head_end].decode("latin-1", errors="replace").lower()
    body = response[head_end + 4 :]
    status_line = head.split("\r\n", 1)[0]
    parts = status_line.split()
    status = parts[1] if len(parts) > 1 else ""
    if status in ("204", "304") or status.startswith("1"):
        return True
    headers = head.split("\r\n")[1:]
    for line in headers:
        name, _sep, value = line.partition(":")
        name = name.strip()
        value = value.strip()
        if name == "content-length":
            try:
                return len(body) >= int(value)
            except ValueError:
                return False
        if name == "transfer-encoding" and "chunked" in value:
            return body.endswith(b"0\r\n\r\n")
    return False


def probe_https(host: str, address: str, *, cancel=None) -> ProbeOutcome:
    from diagnostics.tls_probe import https_get

    result = https_get(
        host,
        address,
        "/",
        timeout=HTTPS_TIMEOUT,
        read_limit=verdict.HTTPS_READ_LIMIT,
        read_timeout=HTTPS_READ_TIMEOUT,
        body_done=http_body_complete,
        cancel=cancel,
    )
    return verdict.judge_https(result)


def probe_https_addresses(host: str, addresses: Sequence[str], *, cancel=None) -> list[ProbeOutcome]:
    """Параллельно, чтобы два адреса не удваивали время проверки."""
    if len(addresses) <= 1:
        return [probe_https(host, address, cancel=cancel) for address in addresses]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(addresses)) as pool:
        return list(pool.map(lambda address: probe_https(host, address, cancel=cancel), addresses))


def tcp_port_open(address: str, port: int, *, timeout: float = 3.0) -> bool:
    """Принимает ли сервер TCP-подключение к порту (без шифрования и запроса)."""
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect((address, int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def control_alive(*, cancel=None) -> bool:
    """Жив ли интернет: открывается ли хоть один обычный сайт."""
    from diagnostics.tls_probe import https_get

    def _one(host: str) -> bool:
        addresses, error = resolve_target_addresses(host, 443)
        if error:
            return False
        result = https_get(host, addresses[0], "/", timeout=CONTROL_TIMEOUT, cancel=cancel)
        return result.status is not None

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(CONTROL_HOSTS)) as pool:
        futures = [pool.submit(_one, host) for host in CONTROL_HOSTS]
        for future in concurrent.futures.as_completed(futures):
            try:
                if future.result():
                    return True
            except Exception:
                continue
    return False


# --- UDP ----------------------------------------------------------------------------


def _udp_exchange(address: str, port: int, request: bytes, accept) -> ProbeOutcome:
    """Отправить запрос и дождаться подходящего ответа, с повтором."""
    started = time.perf_counter()
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    last_reason = "нет ответа (таймаут)"
    for _attempt in range(UDP_ATTEMPTS):
        sock = socket.socket(family, socket.SOCK_DGRAM)
        try:
            sock.settimeout(UDP_REPLY_TIMEOUT)
            sock.sendto(request() if callable(request) else request, (address, int(port)))
            data, _peer = sock.recvfrom(4096)
            # Для обхода DPI важен сам ответ: UDP до сервера проходит. Сервер
            # мог ответить в другом формате (новая версия протокола), и считать
            # такой ответ блокировкой значило бы подбирать на живом сервере.
            reason = "ответ получен" if accept(data) else "ответ получен (нестандартный)"
            return ProbeOutcome(verdict.PROBE_OK, reason, (time.perf_counter() - started) * 1000)
        except socket.timeout:
            last_reason = "нет ответа (таймаут)"
        except ConnectionResetError:
            # ICMP «порт закрыт»: сервер есть, но не слушает — это не DPI.
            return ProbeOutcome(verdict.PROBE_UNREACHABLE, "порт закрыт", (time.perf_counter() - started) * 1000)
        except OSError as error:
            return ProbeOutcome(verdict.PROBE_UNREACHABLE, str(error), (time.perf_counter() - started) * 1000)
        finally:
            try:
                sock.close()
            except OSError:
                pass
    return ProbeOutcome(verdict.PROBE_BLOCKED, last_reason, (time.perf_counter() - started) * 1000)


def _stun_request() -> bytes:
    from blockcheck.stun_tester import build_stun_request

    return build_stun_request()


def _stun_accept(data: bytes) -> bool:
    from blockcheck.stun_tester import parse_stun_response

    return bool(parse_stun_response(data))


_A2S_QUERY = b"\xff\xff\xff\xffTSource Engine Query\x00"
_BEDROCK_MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")


def _a2s_accept(data: bytes) -> bool:
    return len(data) >= 5 and data[:4] == b"\xff\xff\xff\xff" and data[4] in (0x49, 0x41, 0x44, 0x45)


def _bedrock_request() -> bytes:
    timestamp = int(time.time() * 1000) & 0xFFFFFFFFFFFFFFFF
    return b"\x01" + struct.pack(">Q", timestamp) + _BEDROCK_MAGIC + secrets.token_bytes(8)


def _bedrock_accept(data: bytes) -> bool:
    return bool(data) and data[0] == 0x1C and _BEDROCK_MAGIC in data


def probe_udp(spec: UdpProbeSpec) -> ProbeOutcome:
    if not spec.address:
        return ProbeOutcome(verdict.PROBE_UNREACHABLE, f"адрес {spec.host} не найден")
    if spec.kind == "source_a2s":
        return _udp_exchange(spec.address, spec.port, _A2S_QUERY, _a2s_accept)
    if spec.kind == "bedrock_ping":
        return _udp_exchange(spec.address, spec.port, _bedrock_request, _bedrock_accept)
    return _udp_exchange(spec.address, spec.port, _stun_request, _stun_accept)


def probe_udp_pool(specs: Sequence[UdpProbeSpec]) -> list[tuple[UdpProbeSpec, ProbeOutcome]]:
    if not specs:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(specs))) as pool:
        outcomes = list(pool.map(probe_udp, specs))
    return list(zip(specs, outcomes))
