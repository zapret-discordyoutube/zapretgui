"""Запрос «как Chrome»: настоящее рукопожатие и HTTP/2 браузера Chrome.

Зачем. Фильтр провайдера различает программы по первому пакету шифрованного
соединения (ClientHello): его размеру, набору и порядку расширений, ключам.
Модуль ``ssl`` Python шлёт приветствие OpenSSL — втрое короче браузерного, в
один пакет вместо двух, с ALPN только ``http/1.1``. По такому почерку
проверку легко отличить от человека. Хуже того, стратегия обхода режет и
подделывает именно этот пакет: проверенная на приветствии Python, она может
не сработать в браузере, и наоборот.

Здесь запрос идёт через ``curl_cffi``: libcurl с BoringSSL — той же
библиотекой шифрования, что у Chrome, — и её отпечатком: перемешанный
порядок расширений, постквантовый ключ, ALPS, сжатие сертификатов, HTTP/2 с
настройками Chrome и его заголовками. Что именно изображается, задаёт
``CHROME_TARGET``: менять вместе с версией ``curl_cffi``.

Исход описывается тем же ``diagnostics.tls_probe.ProbeResult``, что и у
прежней пробы, поэтому выводы диагностики и подбора стратегий не меняются.

Адрес задаётся явно (``CURLOPT_RESOLVE``): диагностика сама решает, к какому
IP идти, а имя сайта уходит в SNI и в заголовок ``:authority``, как у
браузера. Сжатый ответ по умолчанию не распаковывается: размер тела — это
байты на проводе, а по ним судят об обрыве по объёму. Кому нужно само
содержимое страницы, просит ``decode_body=True``.

Отмена: ``SocketCancel`` здесь не держит сокет (им владеет libcurl), поэтому
отмена замечается на каждом куске ответа; подключение и рукопожатие
ограничены сроком ``timeout``.
"""

from __future__ import annotations

import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass

from utils.net_failure import FAILURE_OTHER, FAILURE_RESET, FAILURE_TIMEOUT, classify_code
from utils.socket_cancel import SocketCancel

__all__ = ["CHROME_TARGET", "WINDOWS_HEADERS", "ChromeConnection", "Exchange", "chrome_get"]

# Какой браузер изображать: самая новая версия Chrome, которую знает curl_cffi 0.16.
CHROME_TARGET = "chrome150"
# Заголовки запроса — как у Chrome 150 на Windows с русским языком, в его порядке. Профиль
# curl_cffi снят с Chrome на macOS; запрос «с Mac» с компьютера, который по пакетам Windows,
# сам бы выделялся. Свои заголовки curl_cffi поэтому выключены, а не подменяются поштучно:
# смесь его заголовков со своими роняла libcurl на повторных запросах по одному соединению.
WINDOWS_HEADERS = (
    b'sec-ch-ua: "Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
    b"sec-ch-ua-mobile: ?0",
    b'sec-ch-ua-platform: "Windows"',
    b"Upgrade-Insecure-Requests: 1",
    b"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    b"Chrome/150.0.0.0 Safari/537.36",
    b"Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,"
    b"application/signed-exchange;v=b3;q=0.7",
    b"Sec-Fetch-Site: none",
    b"Sec-Fetch-Mode: navigate",
    b"Sec-Fetch-User: ?1",
    b"Sec-Fetch-Dest: document",
    b"Accept-Encoding: gzip, deflate, br, zstd",
    b"Accept-Language: ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    b"Priority: u=0, i",
)

# Сжатия, которые распаковывает libcurl в любой сборке. Браузерный список с br и zstd годится, пока тело не читают:
# сжатое zstd тело приходило нераспакованным, и в странице нельзя было найти ни одного имени.
_DECODABLE_ENCODING = b"Accept-Encoding: gzip, deflate"


def _headers(decode_body: bool) -> tuple[bytes, ...]:
    """Заголовки Chrome; когда нужно содержимое страницы, просим только те сжатия, что libcurl точно распакует."""
    if not decode_body:
        return WINDOWS_HEADERS
    return tuple(_DECODABLE_ENCODING if line.startswith(b"Accept-Encoding:") else line for line in WINDOWS_HEADERS)


# Коды ошибок libcurl (CURLcode), по которым судим об исходе.
_E_COULDNT_CONNECT = 7
_E_WRITE_ERROR = 23  # мы сами прервали чтение: набрали сколько нужно или «Стоп»
_E_TIMEOUT = 28
_E_SSL_CONNECT = 35
_E_GOT_NOTHING = 52
_E_SEND = 55
_E_RECV = 56
_E_PEER_VERIFY = 60
_E_HTTP2 = 16
_E_HTTP2_STREAM = 92
_E_SSL_CERTPROBLEM = 58
_E_PARTIAL_FILE = 18


# Виды отладочных событий libcurl (curl_infotype), по которым виден этап соединения.
_DEBUG_TEXT = 0
_DEBUG_HEADER_OUT = 2
_DEBUG_SSL_DATA_IN = 5
_DEBUG_SSL_DATA_OUT = 6


@dataclass(slots=True)
class _Progress:
    """Что успело произойти: от этого зависит, чем считать сбой.

    Замеры времени libcurl при сбое обнуляются, поэтому этап берётся из его
    отладочных событий: ушёл первый пакет шифрования — соединение есть и идёт
    рукопожатие; ушёл заголовок запроса — шифрование установлено.
    """

    status: int | None = None
    body: bytearray | None = None
    # Сколько байт заголовков ответа пришло: у HEAD, кроме них, ничего нет.
    header_size: int = 0
    # Байты тела на проводе: ``body`` при распаковке сжатого тела больше их.
    wire_body: int = 0
    # Служебные байты TLS от сервера: рукопожатие с сертификатом (3–5 КБ на win10, 2026-11-01)
    # и заголовки записей. Фильтр считает и их — ``diagnostics.cut_window``.
    tls_in: int = 0
    # Заголовок Date окончательного ответа: по нему проверка сверяет часы компьютера.
    server_date: str = ""
    # Чтение прервали мы сами: набрали сколько нужно (``stopped_by_us``) или нажали «Стоп».
    stopped_by_us: bool = False
    cancelled: bool = False
    tls_started: bool = False
    secured: bool = False
    tls_version: str = ""
    # Время (``time.perf_counter``): запрос ушёл, пришёл первый заголовок, первый и последний
    # кусок тела, запрос кончился. Нули — этого не случилось. По ним видно, как долго сервер
    # молчал после запроса и с какой скоростью шло тело.
    started_at: float = 0.0
    first_header_at: float = 0.0
    first_body_at: float = 0.0
    last_body_at: float = 0.0
    ended_at: float = 0.0

    def on_debug(self, kind: int, data: bytes) -> None:
        if kind == _DEBUG_SSL_DATA_IN:
            self.tls_in += len(data)
        elif kind == _DEBUG_SSL_DATA_OUT:
            self.tls_started = True
        elif kind == _DEBUG_HEADER_OUT:
            self.secured = True
        elif kind == _DEBUG_TEXT and data.startswith(b"SSL connection using "):
            self.secured = True
            self.tls_version = data[len(b"SSL connection using ") :].split(b" ", 1)[0].decode("ascii", "replace")


def _cert_problem(message: str) -> str:
    text = message.lower()
    if "hostname" in text or "subject" in text or "name" in text and "match" in text:
        return "сертификат выдан другому сайту"
    if "expired" in text or "not yet valid" in text:
        return "сертификат просрочен или ещё не действует (проверьте часы Windows)"
    if "self signed" in text or "self-signed" in text:
        return "самодельный сертификат"
    if "issuer" in text or "unable to get local" in text:
        return "сертификат выдан неизвестным центром"
    return "сертификат не прошёл проверку"


@dataclass(frozen=True, slots=True)
class _CurlApi:
    """Нужная часть ``curl_cffi`` одним набором: тесты подменяют её целиком, без сети."""

    Curl: type
    CurlError: type
    CurlOpt: type
    CurlInfo: type
    write_error: int


def _curl_api() -> _CurlApi:
    from curl_cffi import Curl, CurlError
    from curl_cffi.const import CurlInfo, CurlOpt
    from curl_cffi.curl import CURL_WRITEFUNC_ERROR

    return _CurlApi(Curl, CurlError, CurlOpt, CurlInfo, CURL_WRITEFUNC_ERROR)


@dataclass(slots=True)
class Exchange:
    """Чем кончился один запрос по соединению ``ChromeConnection``."""

    progress: _Progress
    # Код libcurl (0 — без ошибки), его текст и вид сбоя по коду ошибки системы.
    error_code: int = 0
    message: str = ""
    os_failure: str = ""
    # Для этого запроса libcurl открыл новое соединение: прежнее закрыли.
    reconnected: bool = False

    @property
    def received(self) -> int:
        """Байты ответа на проводе: заголовки и тело (сжатое — как пришло)."""
        return self.progress.header_size + self.progress.wire_body

    @property
    def ttfb_ms(self) -> float:
        """Сколько прошло от отправки запроса до первого байта ответа; 0 — ответ не начался.

        У первого запроса по новому соединению сюда входят подключение и рукопожатие.
        """
        progress = self.progress
        first = progress.first_header_at or progress.first_body_at
        if not progress.started_at or not first:
            return 0.0
        return max(0.0, (first - progress.started_at) * 1000)

    @property
    def elapsed_ms(self) -> float:
        """Сколько длился весь запрос, включая сбой; 0 — время не снято."""
        progress = self.progress
        if not progress.started_at or not progress.ended_at:
            return 0.0
        return max(0.0, (progress.ended_at - progress.started_at) * 1000)

    @property
    def body_ms(self) -> float:
        """Сколько шло тело: от первого до последнего куска; 0 — кусков меньше двух."""
        progress = self.progress
        if not progress.first_body_at or progress.last_body_at <= progress.first_body_at:
            return 0.0
        return (progress.last_body_at - progress.first_body_at) * 1000

    @property
    def stalled(self) -> bool:
        """Данные перестали идти (или не пошли) дольше срока."""
        return self.error_code == _E_TIMEOUT or (bool(self.error_code) and self.os_failure == FAILURE_TIMEOUT)

    @property
    def reset(self) -> bool:
        """Соединение оборвали."""
        return bool(self.error_code) and (self.os_failure == FAILURE_RESET or self.error_code in _RESET_CODES)

    @property
    def stage(self) -> str:
        from diagnostics import tls_probe

        if self.progress.secured:
            return tls_probe.STAGE_READ
        return tls_probe.STAGE_TLS if self.progress.tls_started else tls_probe.STAGE_CONNECT


class ChromeConnection:
    """Соединение «как Chrome» с сайтом по заданному адресу: запросы подряд идут по нему же.

    Так браузер грузит страницу — несколько запросов по одному соединению (HTTP/2).
    Фильтр считает данные по соединению, поэтому обрыв по объёму видно только так.
    """

    def __init__(
        self,
        host: str,
        ip: str,
        *,
        timeout: float,
        port: int = 443,
        read_timeout: float = 3.0,
        decode_body: bool = False,
        cancel: SocketCancel | None = None,
    ) -> None:
        self.host = host
        self.ip = ip
        self.port = int(port)
        self._decode_body = bool(decode_body)
        self.cancel = cancel or SocketCancel()
        self._api = _curl_api()
        opt = self._api.CurlOpt
        self._curl = self._api.Curl()
        self._requests = 0
        address = f"[{ip}]" if ":" in ip else ip
        curl = self._curl
        curl.impersonate(CHROME_TARGET, default_headers=False)
        curl.setopt(opt.HTTPHEADER, list(_headers(decode_body)))
        curl.setopt(opt.RESOLVE, [f"{host}:{self.port}:{address}".encode()])
        curl.setopt(opt.CAINFO, _ca_bundle().encode())
        # libcurl тело не распаковывает ни в какой сборке: при нужном содержимом его распаковывает
        # ``request`` сам (gzip и deflate), иначе размер — это байты на проводе.
        curl.setopt(opt.HTTP_CONTENT_DECODING, 0)
        curl.setopt(opt.CONNECTTIMEOUT_MS, int(max(0.2, float(timeout)) * 1000))
        # Тело замерло дольше ``read_timeout`` — обрыв (как ``body_cut`` у прежней пробы).
        curl.setopt(opt.LOW_SPEED_LIMIT, 1)
        curl.setopt(opt.LOW_SPEED_TIME, max(1, round(float(read_timeout))))
        curl.setopt(opt.TIMEOUT_MS, int((max(0.2, float(timeout)) + 60.0) * 1000))
        curl.setopt(opt.VERBOSE, 1)
        curl.setopt(opt.NOSIGNAL, 1)

    def request(
        self,
        path: str = "/",
        *,
        method: str = "GET",
        enough: Callable[[bytearray], bool] = lambda _body: False,
    ) -> Exchange:
        """Один запрос. ``enough(тело)`` — читать дальше незачем (чтение прерывается без сбоя)."""
        api = self._api
        opt, info = api.CurlOpt, api.CurlInfo
        progress = _Progress(body=bytearray())
        token = self.cancel
        # Распаковщик тела: заводится по заголовку Content-Encoding, куски отдаются по мере прихода.
        unpack: list = []

        def _on_header(line: bytes) -> int:
            if not progress.first_header_at:
                progress.first_header_at = time.perf_counter()
            progress.header_size += len(line)
            if self._decode_body and line.lower().startswith(b"content-encoding:"):
                kind = line.split(b":", 1)[1].strip().lower()
                if kind in (b"gzip", b"x-gzip"):
                    unpack[:] = [zlib.decompressobj(16 + zlib.MAX_WBITS)]
                elif kind == b"deflate":
                    unpack[:] = [zlib.decompressobj()]
            if line[:5].lower() == b"date:":
                progress.server_date = line[5:].strip().decode("ascii", errors="ignore")
            if line.startswith(b"HTTP/"):
                parts = line.split()
                # Перед окончательным ответом бывают промежуточные (100, 103): ждём настоящий.
                if len(parts) >= 2 and parts[1].isdigit() and not parts[1].startswith(b"1"):
                    progress.status = int(parts[1])
            return len(line)

        def _on_body(chunk: bytes) -> int:
            now = time.perf_counter()
            if not progress.first_body_at:
                progress.first_body_at = now
            progress.last_body_at = now
            progress.wire_body += len(chunk)
            if unpack:
                try:
                    progress.body.extend(unpack[0].decompress(chunk))
                except zlib.error:
                    # Битый или оборванный поток: что распаковалось, то и осталось.
                    unpack.clear()
            else:
                progress.body.extend(chunk)
            if token.cancelled:
                progress.cancelled = True
                return api.write_error
            if enough(progress.body):
                progress.stopped_by_us = True
                return api.write_error
            return len(chunk)

        curl = self._curl
        curl.setopt(opt.URL, f"https://{self.host}:{self.port}{path or '/'}".encode())
        curl.setopt(opt.NOBODY, 1 if method == "HEAD" else 0)
        curl.setopt(opt.HEADERFUNCTION, _on_header)
        curl.setopt(opt.WRITEFUNCTION, _on_body)
        curl.setopt(opt.DEBUGFUNCTION, progress.on_debug)
        exchange = Exchange(progress)
        if token.cancelled:
            progress.cancelled = True
            return exchange
        progress.started_at = time.perf_counter()
        try:
            # Списки заголовков и «имя → адрес» нужны и следующим запросам по этому соединению:
            # по умолчанию curl_cffi освобождает их после запроса, а libcurl продолжает на них
            # ссылаться — следующий запрос читал освобождённую память и ронял процесс.
            # Освобождаются они в ``close``.
            curl.perform(clear_headers=False, clear_resolve=False)
        except api.CurlError as error:
            exchange.error_code = int(getattr(error, "code", 0) or 0)
            exchange.message = str(error)
        progress.ended_at = time.perf_counter()
        if exchange.error_code:
            # Код ошибки системы: отказ, тишина или нет дороги — текст libcurl этого не различает.
            exchange.os_failure = classify_code(int(curl.getinfo(info.OS_ERRNO) or 0))
        elif progress.status is None:
            progress.status = int(curl.getinfo(info.RESPONSE_CODE) or 0) or None
        self._requests += 1
        exchange.reconnected = self._requests > 1 and int(curl.getinfo(info.NUM_CONNECTS) or 0) > 0
        return exchange

    def close(self) -> None:
        self._curl.close()


def chrome_get(
    host: str,
    ip: str,
    path: str = "/",
    *,
    timeout: float,
    port: int = 443,
    read_limit: int = 0,
    read_timeout: float = 3.0,
    body_done: Callable[[bytes], bool] | None = None,
    decode_body: bool = False,
    cancel: SocketCancel | None = None,
):
    """GET ``https://host/path`` по адресу ``ip`` с рукопожатием и заголовками Chrome.

    Исход — ``diagnostics.tls_probe.ProbeResult``: ``timeout`` — подключение,
    шифрование и первый ответ; ``read_limit`` — сколько байт тела читать (0 —
    только заголовок); ``read_timeout`` — сколько ждать следующих данных, замершее
    на середине тело видно как ``body_cut``. ``body_done`` получает тело (без
    заголовков) и говорит, что читать дальше незачем; конец ответа libcurl видит
    сам. ``decode_body`` — распаковать сжатое тело (нужно содержимое страницы);
    размер тогда уже не байты на проводе и для суждения об обрыве по объёму не годится.
    """
    from diagnostics import tls_probe

    token = cancel or SocketCancel()
    started = time.perf_counter()

    def _enough(body: bytearray) -> bool:
        return len(body) >= max(read_limit, 1) or (body_done is not None and body_done(bytes(body)))

    if token.cancelled:
        return tls_probe.ProbeResult(ip=ip, kind=tls_probe.KIND_CANCELLED, stage=tls_probe.STAGE_CONNECT)
    connection = ChromeConnection(
        host, ip, timeout=timeout, port=port, read_timeout=read_timeout, decode_body=decode_body, cancel=token
    )
    try:
        exchange = connection.request(path, enough=_enough)
    finally:
        connection.close()
    progress = exchange.progress

    def _result(kind: str, detail: str = "", **extra):
        if token.cancelled and (kind != tls_probe.KIND_OK or extra.get("body_cut")):
            kind = tls_probe.KIND_CANCELLED
            extra.pop("body_cut", None)
        return tls_probe.ProbeResult(
            ip=ip,
            kind=kind,
            stage=exchange.stage,
            status=progress.status,
            body_size=len(progress.body),
            received_size=exchange.received,
            handshake_size=progress.tls_in,
            tls_version=progress.tls_version,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            detail=detail,
            body=bytes(progress.body),
            server_date=progress.server_date,
            **extra,
        )

    return _classify(exchange.error_code, exchange.os_failure, exchange.message, progress, exchange.stage, _result, tls_probe)


def _classify(error_code: int, os_failure: str, message: str, progress: _Progress, stage: str, result, tls_probe):
    """Исход по коду libcurl, коду ошибки системы и тому, что успело прийти."""
    got_answer = progress.status is not None
    if progress.cancelled:
        return result(tls_probe.KIND_CANCELLED)
    if error_code == 0 or (error_code == _E_WRITE_ERROR and progress.stopped_by_us):
        if not got_answer:
            return result(tls_probe.KIND_RESET, "сервер закрыл соединение без ответа")
        return result(tls_probe.KIND_OK)
    if error_code in (_E_PEER_VERIFY, _E_SSL_CERTPROBLEM):
        return result(tls_probe.KIND_CERT, message, cert_problem=_cert_problem(message))
    if got_answer:
        # Заголовок пришёл, а тело оборвалось: сброс, замирание или обрыв HTTP/2.
        return result(tls_probe.KIND_OK, message, body_cut=True)
    if stage == tls_probe.STAGE_CONNECT:
        if error_code == _E_TIMEOUT:
            return result(tls_probe.KIND_CONNECT, "сервер не отвечает на подключение", connect_fail=FAILURE_TIMEOUT)
        return result(tls_probe.KIND_CONNECT, message, connect_fail=os_failure or FAILURE_OTHER)
    if error_code == _E_TIMEOUT or os_failure == FAILURE_TIMEOUT:
        return result(tls_probe.KIND_TIMEOUT, message)
    if os_failure == FAILURE_RESET or error_code in _RESET_CODES:
        return result(tls_probe.KIND_RESET, message)
    if error_code == _E_SSL_CONNECT:
        return result(tls_probe.KIND_TLS, message)
    return result(tls_probe.KIND_ERROR, message)


# Соединение оборвали: пустой ответ, ошибка приёма или отправки, обрыв потока HTTP/2.
_RESET_CODES = frozenset({_E_RECV, _E_SEND, _E_GOT_NOTHING, _E_HTTP2, _E_HTTP2_STREAM, _E_PARTIAL_FILE})


def _ca_bundle() -> str:
    """Корневые сертификаты: BoringSSL не читает хранилище Windows, берём certifi."""
    import certifi

    return certifi.where()
