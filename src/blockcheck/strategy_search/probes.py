"""Сетевые проверки подбора: HTTPS, STUN и игровые UDP-запросы.

Адреса цели определяются один раз до подбора (``resolve_target_addresses``),
и дальше каждая стратегия проверяется на тех же адресах. Так ответ DNS не
меняется от стратегии к стратегии, а таймауты не умножаются на число адресов.

Все функции возвращают ``verdict.ProbeOutcome`` и сами решений не принимают.
"""

from __future__ import annotations

import concurrent.futures
import re
import secrets
import socket
import struct
import time
from collections.abc import Sequence

from blockcheck.strategy_search import verdict
from blockcheck.strategy_search.verdict import ProbeOutcome, UdpProbeSpec
from utils.net_failure import FAILURE_REFUSED, FAILURE_RESET, FAILURE_TIMEOUT, classify

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


# Видеосерверы одного узла YouTube: rr1---sn-xxxx … rr20---sn-xxxx. Плеер берёт видео с любого из них.
_VIDEO_NODE = re.compile(r"^rr(\d+)---(sn-[a-z0-9-]+)\.googlevideo\.com$")
VIDEO_SIBLINGS = 20


def sibling_video_hosts(host: str) -> list[str]:
    """Остальные видеосерверы того же узла (имена, без проверки). Не видеосервер — пусто."""
    match = _VIDEO_NODE.match(str(host or "").strip().lower().rstrip("."))
    if not match:
        return []
    number, node = int(match.group(1)), match.group(2)
    return [f"rr{index}---{node}.googlevideo.com" for index in range(1, VIDEO_SIBLINGS + 1) if index != number]


def stub_addresses(addresses: Sequence[str]) -> list[str]:
    """Адреса-заглушки провайдера или РКН среди ответа DNS."""
    from utils.address_kinds import AddressKind, address_kind

    return [
        address
        for address in addresses
        if address_kind(address) in (AddressKind.BLOCK_STUB, AddressKind.SELF)
    ]


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
    """Сайт открывается так, как его открыл бы Chrome: то же рукопожатие и тот же HTTP/2.

    Стратегия режет и подделывает первый пакет соединения, поэтому проверять её
    нужно на том пакете, который пошлёт браузер (``utils.chrome_https``), а не на
    приветствии Python.
    """
    from utils.chrome_https import chrome_get

    result = chrome_get(
        host,
        address,
        "/",
        timeout=HTTPS_TIMEOUT,
        read_limit=verdict.HTTPS_READ_LIMIT,
        read_timeout=HTTPS_READ_TIMEOUT,
        cancel=cancel,
    )
    return verdict.judge_https(result)


def probe_https_addresses(host: str, addresses: Sequence[str], *, cancel=None) -> list[ProbeOutcome]:
    """Параллельно, чтобы два адреса не удваивали время проверки."""
    if len(addresses) <= 1:
        return [probe_https(host, address, cancel=cancel) for address in addresses]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(addresses)) as pool:
        return list(pool.map(lambda address: probe_https(host, address, cancel=cancel), addresses))


def probe_volume(host: str, address: str, answered: ProbeOutcome, *, cancel) -> ProbeOutcome:
    """Добрать объём по одному соединению к адресу, который ответил коротко.

    Фильтр считает данные по соединению, поэтому подряд уходят запросы, пока
    не наберётся больше окна обрыва (``diagnostics.volume_probe``). Замерло
    в окне дважды — стратегия не работает, хотя главная страница открылась.
    """
    from diagnostics import transfer_probe, volume_probe

    facts = volume_probe.collect(host, address, "/", cancel=cancel)
    return verdict.judge_volume(answered, volume_probe.judge(facts), transfer_probe.judge(facts.first.samples))


# Читаем столько же, сколько у HTTPS: дальше окна обрыва по объёму, иначе
# обрыв на 20–36 КБ остался бы невидимым.
HTTP_READ_LIMIT = verdict.HTTPS_READ_LIMIT


def probe_http(host: str, address: str, *, cancel=None) -> ProbeOutcome:
    """``GET http://host/`` по адресу: ответ, заглушка или обрыв (``verdict.judge_http``)."""
    from utils.net_failure import FAILURE_CANCELLED, FAILURE_CLOSED
    from utils.socket_cancel import SocketCancel, close_quietly

    token = cancel or SocketCancel()
    started = time.perf_counter()
    sock = None
    data = bytearray()
    connected = False
    failure = ""
    try:
        sock = socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM)
        if not token.track(sock):
            return verdict.judge_http(host=host, status=None, failure=FAILURE_CANCELLED)
        sock.settimeout(HTTPS_TIMEOUT)
        sock.connect((address, 80))
        connected = True
        request = (
            f"GET / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
            "Accept: */*\r\nConnection: close\r\n\r\n"
        )
        sock.sendall(request.encode("ascii", errors="ignore"))
        sock.settimeout(HTTPS_READ_TIMEOUT)
        while len(data) < HTTP_READ_LIMIT:
            chunk = sock.recv(HTTP_READ_LIMIT - len(data))
            if not chunk:
                break
            data.extend(chunk)
    except OSError as error:
        failure = classify(error)
    finally:
        if sock is not None:
            token.release(sock)
            close_quietly(sock)
    elapsed = (time.perf_counter() - started) * 1000
    if token.cancelled:
        failure = FAILURE_CANCELLED
    head, _sep, body = bytes(data).partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    parts = lines[0].split(None, 2) if lines and lines[0] else []
    status = int(parts[1]) if len(parts) >= 2 and parts[0].startswith(b"HTTP/") and parts[1].isdigit() else None
    if status is None and connected and not failure:
        failure = FAILURE_CLOSED
    location = ""
    for line in lines[1:]:
        name, _colon, value = line.partition(b":")
        if name.strip().lower() == b"location":
            location = value.strip().decode("latin-1", errors="replace")
    # Заголовок пришёл, а тело сброшено или замерло на полпути — так фильтр
    # режет по объёму. Набранный предел чтения без сбоя — не обрыв: дальше не читали.
    body_cut = (
        status is not None
        and failure in (FAILURE_RESET, FAILURE_TIMEOUT)
        and len(data) < HTTP_READ_LIMIT
        and not http_body_complete(bytes(data))
    )
    return verdict.judge_http(
        host=host,
        status=status,
        location=location,
        body=body,
        failure=failure,
        connected=connected,
        elapsed_ms=elapsed,
        body_cut=body_cut,
        # Весь ответ: без шифрования фильтр считает заголовки вместе с телом.
        body_size=len(data),
    )


def probe_quic(host: str, address: str, *, cancel=None) -> ProbeOutcome:
    """Первый пакет QUIC с именем сайта: ответил ли сервер (``diagnostics.quic_probe``)."""
    from diagnostics.quic_probe import quic_hello
    from utils.net_failure import FAILURE_CANCELLED

    started = time.perf_counter()
    answered_ms = quic_hello(address, host, cancel=cancel)
    if answered_ms is not None:
        return ProbeOutcome(verdict.PROBE_OK, "сервер ответил на QUIC", answered_ms)
    if cancel is not None and cancel.cancelled:
        return ProbeOutcome(verdict.PROBE_CANCELLED, "отменено", 0.0, FAILURE_CANCELLED)
    return ProbeOutcome(
        verdict.PROBE_BLOCKED,
        "пакет QUIC с именем сайта пропадает",
        (time.perf_counter() - started) * 1000,
        FAILURE_TIMEOUT,
    )


def probe_quic_addresses(host: str, addresses: Sequence[str], *, cancel=None) -> list[ProbeOutcome]:
    if len(addresses) <= 1:
        return [probe_quic(host, address, cancel=cancel) for address in addresses]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(addresses)) as pool:
        return list(pool.map(lambda address: probe_quic(host, address, cancel=cancel), addresses))


def quic_blocked_by_name(host: str, address: str, *, cancel) -> bool:
    """QUIC сайта режут по имени: с его именем молчит (дважды), с посторонним или без имени — отвечает."""
    from diagnostics import quic_probe

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        facts = quic_probe.collect(host, address, submit=pool.submit, cancel=cancel)
    found = quic_probe.judge(facts)
    return found is not None and found.code == quic_probe.QUIC_BLOCKED_BY_NAME


def probe_http_addresses(host: str, addresses: Sequence[str], *, cancel=None) -> list[ProbeOutcome]:
    if len(addresses) <= 1:
        return [probe_http(host, address, cancel=cancel) for address in addresses]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(addresses)) as pool:
        return list(pool.map(lambda address: probe_http(host, address, cancel=cancel), addresses))


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
    from utils.chrome_https import chrome_get

    def _one(host: str) -> bool:
        addresses, error = resolve_target_addresses(host, 443)
        if error:
            return False
        result = chrome_get(host, addresses[0], "/", timeout=CONTROL_TIMEOUT, cancel=cancel)
        return result.status is not None

    # Первый открывшийся сайт — ответ: медленные остальные не ждём (раньше выход из
    # «with» дожидался всех и добавлял секунды к каждой проверке связи).
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(CONTROL_HOSTS))
    try:
        futures = [pool.submit(_one, host) for host in CONTROL_HOSTS]
        for future in concurrent.futures.as_completed(futures):
            try:
                if future.result():
                    return True
            except Exception:
                continue
        return False
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


# --- UDP ----------------------------------------------------------------------------


def _udp_exchange(address: str, port: int, request: bytes, accept) -> ProbeOutcome:
    """Отправить запрос и дождаться подходящего ответа, с повтором."""
    started = time.perf_counter()
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    last_reason = "нет ответа (таймаут)"

    def spent() -> float:
        return (time.perf_counter() - started) * 1000

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
            return ProbeOutcome(verdict.PROBE_OK, reason, spent())
        except OSError as error:
            failure = classify(error)
            if failure == FAILURE_TIMEOUT:
                last_reason = "нет ответа (таймаут)"
                continue
            if failure == FAILURE_RESET:
                # У UDP так приходит ICMP «порт закрыт»: сервер есть, но не слушает — это не DPI.
                return ProbeOutcome(verdict.PROBE_UNREACHABLE, "порт закрыт", spent(), FAILURE_REFUSED)
            return ProbeOutcome(verdict.PROBE_UNREACHABLE, str(error), spent(), failure)
        finally:
            try:
                sock.close()
            except OSError:
                pass
    return ProbeOutcome(verdict.PROBE_BLOCKED, last_reason, spent(), FAILURE_TIMEOUT)


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
