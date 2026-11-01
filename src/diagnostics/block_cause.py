"""Почему сайт не открылся: уточнение причины дополнительными пробами.

Основная проверка (``diagnostics.tls_probe``) говорит, на какой ступени
оборвалось соединение. Этого мало, чтобы понять, **как** блокируют: по имени
сайта или по адресу. Здесь делаются три дешёвые пробы к тому же адресу.

1. **Тот же адрес, другое имя.** В начале шифрованного соединения браузер
   открытым текстом называет сайт, к которому идёт (это поле зовётся SNI).
   Если с именем сайта соединение рвётся, а с посторонним именем или вовсе
   без имени тот же сервер отвечает — режут по имени. Ответом считается и
   отказ сервера («не знаю такого имени»): раз он дошёл, дорога открыта.
   Отдельно пробуется то же имя другими буквами (``googlevideo.COM``): сервер
   регистр не различает, а фильтр, который ищет имя буква в букву, его пропустит.
2. **Обычный HTTP, порт 80.** Часть провайдеров вместо сайта показывает
   страницу о блокировке или отдаёт код 451.
3. **Пинг** — когда адрес не принимает соединение вовсе.
4. **Окно приёма в 1 байт** — когда шифрование не проходит ни с каким именем.
   Часть фильтров (ТСПУ, октябрь 2026) режет любой TLS с адреса, узнавая его по
   первым трём байтам ответа сервера, но читает ответ только в пределах окна
   приёма, которое объявил компьютер. Если с окном в 1 байт начало ответа
   приходит, а с окном в 1 КБ — нет, закрыт не адрес, а TLS с него, и это
   обходит приём ``wssize``. Окно в 1 КБ — контроль: фильтр его читает и должен
   оборвать. Проходит и оно — дело не в окне, а в размере пакетов (потери
   больших пакетов на пути), в медленной линии или в фильтре «через раз».

Сбор (``collect``) отделён от вывода (``judge``): вывод — чистая функция над
собранными фактами.
"""

from __future__ import annotations

import socket
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from diagnostics.tls_probe import (
    KIND_CONNECT,
    KIND_RESET,
    KIND_TIMEOUT,
    KIND_TLS,
    STAGE_CONNECT,
    STAGE_TLS,
    ProbeResult,
)
from utils.socket_cancel import SocketCancel, close_quietly

__all__ = [
    "CAUSE_ADDRESS_CLOSED",
    "CAUSE_ADDRESS_SILENT",
    "CAUSE_BY_ADDRESS",
    "CAUSE_BY_NAME",
    "CAUSE_REPLY_WINDOW",
    "CAUSE_STUB_PAGE",
    "HELLO_ALERT",
    "HELLO_CANCELLED",
    "HELLO_CONNECT",
    "HELLO_GARBAGE",
    "HELLO_OK",
    "TLS_1_2",
    "TLS_1_3",
    "case_variant",
    "HELLO_RESET",
    "HELLO_TIMEOUT",
    "NEUTRAL_NAME",
    "Cause",
    "CauseFacts",
    "HelloResult",
    "HttpFacts",
    "collect",
    "http_probe",
    "judge",
    "needs_refining",
    "stub_reason",
    "reply_window_proven",
    "small_window_twice",
    "tls_hello",
    "window_facts",
    "window_hello",
]

# Постороннее имя: существует, ни в одном списке блокировок его нет.
NEUTRAL_NAME = "example.com"
# Имя, которое провайдеры пропускают всегда: оно есть в «белых списках».
ALLOWED_NAME = "vk.com"
# Имена, которые пропускают и в режиме «белых списков»: у разных операторов список
# свой, поэтому пробуется несколько — хватит одного ответившего.
ALLOWED_NAMES = ("max.ru", "ya.ru", ALLOWED_NAME)
# Шаг между стартами приветствий к одному адресу: пачка одновременных соединений
# к одному сайту сама бывает поводом для фильтра придержать их все.
STAGGER_S = 0.25

HELLO_OK = "ok"  # шифрование установилось
HELLO_ALERT = "alert"  # сервер отказал сам — но ответил
HELLO_RESET = "reset"
HELLO_TIMEOUT = "timeout"
HELLO_CONNECT = "connect"  # не удалось даже соединиться
HELLO_GARBAGE = "garbage"  # пришло не шифрование: ответил не сервер
HELLO_CANCELLED = "cancelled"
HELLO_ERROR = "error"  # ошибка, которую не удалось истолковать

CAUSE_STUB_PAGE = "stub_page"
CAUSE_BY_NAME = "by_name"
# К адресу пускают только имена из разрешённого списка.
CAUSE_NAME_WHITELIST = "name_whitelist"
CAUSE_BY_ADDRESS = "by_address"
# Ни одно имя не прошло, но часть проб сброшена: адрес это или сам сервер — не ясно.
CAUSE_NO_NAME_WORKS = "no_name_works"
CAUSE_ADDRESS_CLOSED = "address_closed"
# Шифрование не проходит ни с каким именем, а с окном приёма в 1 байт ответ сервера приходит:
# фильтр режет TLS с адреса по ответу сервера и читает этот ответ только в пределах окна.
CAUSE_REPLY_WINDOW = "reply_window"
CAUSE_ADDRESS_SILENT = "address_silent"

HELLO_TIMEOUT_S = 4.0
# Окно приёма пробы «малого окна». ``SO_RCVBUF`` до соединения Windows ставит в первый пакет как
# есть и без множителя (замерено на win10: SYN «win 1, wscale 0»), и сервер отдаёт ответ по байту.
SMALL_WINDOW_BYTES = 1
# Сколько байт ответа ждать: тип записи TLS (0x16 — рукопожатие, 0x15 — отказ) и старший байт
# версии (0x03). Подпись TLS фильтр узнаёт по трём байтам; двух ему мало, а нам хватает.
_WINDOW_READ = 2
# Окно контрольной пробы: меньше одного пакета (сервер шлёт порции не больше окна, и потеря
# больших пакетов на пути их не задевает), но в нём фильтр видит подпись TLS целиком.
CONTROL_WINDOW_BYTES = 1024
_RECORD_HANDSHAKE = 0x16
_RECORD_ALERT = 0x15
HTTP_TIMEOUT_S = 4.0
_HTTP_READ_LIMIT = 4096
_STATUS_UNAVAILABLE_FOR_LEGAL_REASONS = 451

# Страницы-заглушки операторов: куда уводит перенаправление.
_STUB_HOSTS = (
    "warning.rt.ru",
    "block.mts.ru",
    "blocked.beeline.ru",
    "block.megafon.ru",
    "rkn.gov.ru",
)
# Признаки в тексте страницы. Только такие, которых на обычном сайте не бывает:
# слова вроде «заблокирован» встречаются и на обычных страницах.
_STUB_MARKERS = ("eais.rkn.gov.ru", "blocklist.rkn.gov.ru", "nap.rkn.gov.ru")
# Номер закона встречается и на обычных страницах (новости, справки), поэтому
# сам по себе признаком не считается — только вместе со словами об ограничении доступа.
_LAW_MARKERS = ("149-фз", "149-fz")
_RESTRICTED_MARKERS = (
    "доступ ограничен",
    "доступ к ресурсу ограничен",
    "доступ к информационному ресурсу ограничен",
    "доступ закрыт",
)


@dataclass(frozen=True, slots=True)
class HelloResult:
    kind: str
    detail: str = ""
    # За сколько установилось шифрование. None — не установилось.
    ms: float | None = None

    @property
    def answered(self) -> bool:
        """Сервер по этому адресу ответил — значит, дорога до него открыта."""
        return self.kind in (HELLO_OK, HELLO_ALERT)


@dataclass(frozen=True, slots=True)
class HttpFacts:
    """Что ответил порт 80. ``status`` None — ответа не было."""

    status: int | None = None
    location: str = ""
    body: bytes = b""


@dataclass(frozen=True, slots=True)
class CauseFacts:
    """Всё, что удалось узнать дополнительно. Выводов здесь нет."""

    host: str
    result: ProbeResult
    neutral: HelloResult | None = None
    nameless: HelloResult | None = None
    # То же, но с именем, которое провайдеры заведомо пропускают (``ALLOWED_NAME``).
    allowed: HelloResult | None = None
    # С каким из разрешённых имён получен ``allowed``.
    allowed_name: str = ALLOWED_NAME
    # Настоящее имя ещё раз, на том же адресе. None — не проверяли.
    real_again: HelloResult | None = None
    http: HttpFacts | None = None
    # None — пинг не делали или он недоступен в системе.
    ping_ok: bool | None = None
    # То же имя другими буквами (``case_variant``) и его исход. None — не проверяли.
    recased: HelloResult | None = None
    recased_name: str = ""
    # Сервер заведомо принимает любое имя (видеосервер Google): тогда отказ на всех именах —
    # это не его привередливость, а закрытый адрес.
    any_name: bool = False
    # Постороннее имя с окном приёма в 1 байт (``window_hello``), когда не прошло ни одно имя.
    # «Ответил» записывается, только если начало ответа пришло дважды подряд. None — не проверяли.
    small_window: HelloResult | None = None
    # Контроль после ответа малого окна: то же с окном в ``CONTROL_WINDOW_BYTES``. Фильтр по ответу
    # его режет; ответ здесь значит, что дело не в окне. None — не проверяли.
    window_control: HelloResult | None = None


@dataclass(frozen=True, slots=True)
class Cause:
    code: str
    text: str
    # Уверенный вывод опирается на прямое противоречие, а не на отсутствие ответа.
    confident: bool = False


TLS_1_2 = "1.2"
TLS_1_3 = "1.3"
_TLS_VERSIONS = {TLS_1_2: ssl.TLSVersion.TLSv1_2, TLS_1_3: ssl.TLSVersion.TLSv1_3}

_context_lock = threading.Lock()
# Версия шифрования ("" — любая) → готовый клиент.
_contexts: dict[str, ssl.SSLContext] = {}


def _hello_context(version: str = "") -> ssl.SSLContext:
    """Тот же клиент, что у основной проверки, но без проверки сертификата.

    Сертификат здесь не важен: с посторонним именем сервер и должен
    предъявить «не тот». Важно одно — ответил ли он вообще. ``version`` —
    разрешить только одну версию шифрования (``TLS_1_2`` или ``TLS_1_3``).
    """
    with _context_lock:
        context = _contexts.get(version)
        if context is None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            if version in _TLS_VERSIONS:
                context.minimum_version = context.maximum_version = _TLS_VERSIONS[version]
            try:
                context.set_alpn_protocols(["http/1.1"])
            except (NotImplementedError, ssl.SSLError):
                pass
            _contexts[version] = context
        return context


def _hello_failure(error: ssl.SSLError) -> HelloResult:
    reason = str(getattr(error, "reason", "") or "")
    text = f"{reason} {error}".upper()
    if isinstance(error, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
        return HelloResult(HELLO_RESET, "соединение закрыто на приветствии")
    if "WRONG_VERSION_NUMBER" in text or "UNKNOWN_PROTOCOL" in text or "RECORD_LAYER" in text:
        return HelloResult(HELLO_GARBAGE, reason)
    if "ALERT" in text or "UNRECOGNIZED_NAME" in text or "HANDSHAKE_FAILURE" in text:
        return HelloResult(HELLO_ALERT, reason)
    # Незнакомая ошибка шифрования — это «не поняли», а не «ответил чужой сервер».
    return HelloResult(HELLO_ERROR, reason or str(error))


def tls_hello(
    ip: str,
    name: str | None,
    *,
    port: int = 443,
    timeout: float = HELLO_TIMEOUT_S,
    cancel: SocketCancel | None = None,
    version: str = "",
) -> HelloResult:
    """Начинает шифрованное соединение с ``ip``, называя имя ``name`` (None — без имени).

    ``version`` — только этой версией шифрования (``TLS_1_2`` / ``TLS_1_3``).
    """
    token = cancel or SocketCancel()
    # Клиент готовится до отсчёта времени: его создание бывает небыстрым.
    context = _hello_context(version)
    sock: socket.socket | None = None
    wrapped: ssl.SSLSocket | None = None
    connected = False
    try:
        sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
        if not token.track(sock):
            return HelloResult(HELLO_CANCELLED)
        sock.settimeout(timeout)
        sock.connect((ip, int(port)))
        connected = True
        started = time.perf_counter()
        wrapped = context.wrap_socket(sock, server_hostname=name, do_handshake_on_connect=False)
        token.track(wrapped)
        wrapped.settimeout(timeout)
        wrapped.do_handshake()
        return HelloResult(HELLO_OK, ms=(time.perf_counter() - started) * 1000.0)
    except (socket.timeout, TimeoutError):
        if token.cancelled:
            return HelloResult(HELLO_CANCELLED)
        return HelloResult(HELLO_TIMEOUT if connected else HELLO_CONNECT)
    except ssl.SSLError as error:
        return HelloResult(HELLO_CANCELLED) if token.cancelled else _hello_failure(error)
    except OSError as error:
        if token.cancelled:
            return HelloResult(HELLO_CANCELLED)
        return HelloResult(HELLO_RESET if connected else HELLO_CONNECT, str(error))
    finally:
        for item in (wrapped, sock):
            if item is not None:
                token.release(item)
                close_quietly(item)


def _client_hello_bytes(name: str | None) -> bytes:
    """Первый пакет шифрованного соединения — тот же, что шлёт ``tls_hello``, но байтами."""
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = _hello_context().wrap_bio(incoming, outgoing, server_hostname=name)
    try:
        tls.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


def window_hello(
    ip: str,
    name: str | None,
    *,
    port: int = 443,
    timeout: float = HELLO_TIMEOUT_S,
    cancel: SocketCancel | None = None,
    window: int = SMALL_WINDOW_BYTES,
) -> HelloResult:
    """Приветствие с окном приёма в ``window`` байт: дошло ли начало ответа сервера.

    Окно — сколько байт компьютер готов принять, пока не подтвердит полученное; сервер не
    шлёт больше. Шифрование до конца здесь не доводится (по байту оно шло бы минутами):
    ``HELLO_OK`` значит «пришло начало записи рукопожатия», ``HELLO_ALERT`` — «начало отказа».
    """
    token = cancel or SocketCancel()
    hello = _client_hello_bytes(name)
    sock: socket.socket | None = None
    connected = False
    data = bytearray()
    started = 0.0
    try:
        sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
        if not token.track(sock):
            return HelloResult(HELLO_CANCELLED)
        # До соединения: окно и его множитель объявляются в первом пакете и потом не меняются.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, int(window))
        sock.settimeout(timeout)
        sock.connect((ip, int(port)))
        connected = True
        started = time.perf_counter()
        sock.sendall(hello)
        while len(data) < _WINDOW_READ:
            chunk = sock.recv(_WINDOW_READ - len(data))
            if not chunk:
                break
            data.extend(chunk)
    except (socket.timeout, TimeoutError):
        if token.cancelled:
            return HelloResult(HELLO_CANCELLED)
        # Пришла часть начала ответа и замерла — это не «ответил», а «ответа нет».
        return HelloResult(HELLO_TIMEOUT if connected else HELLO_CONNECT, data.hex())
    except OSError as error:
        if token.cancelled:
            return HelloResult(HELLO_CANCELLED)
        return HelloResult(HELLO_RESET if connected else HELLO_CONNECT, str(error))
    finally:
        if sock is not None:
            token.release(sock)
            close_quietly(sock)
    if token.cancelled:
        return HelloResult(HELLO_CANCELLED)
    if not data:
        return HelloResult(HELLO_RESET, "соединение закрыто на приветствии")
    if len(data) < _WINDOW_READ or data[1] != 0x03 or data[0] not in (_RECORD_HANDSHAKE, _RECORD_ALERT):
        return HelloResult(HELLO_GARBAGE, data.hex())
    kind = HELLO_OK if data[0] == _RECORD_HANDSHAKE else HELLO_ALERT
    return HelloResult(kind, "начало ответа сервера пришло", ms=(time.perf_counter() - started) * 1000.0)


def http_probe(
    host: str,
    ip: str,
    *,
    port: int = 80,
    timeout: float = HTTP_TIMEOUT_S,
    cancel: SocketCancel | None = None,
) -> HttpFacts:
    """``GET http://host/`` по адресу ``ip``: код ответа, куда перенаправили и начало страницы."""
    token = cancel or SocketCancel()
    sock: socket.socket | None = None
    data = bytearray()
    try:
        sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
        if not token.track(sock):
            return HttpFacts()
        sock.settimeout(timeout)
        sock.connect((ip, int(port)))
        request = f"GET / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Mozilla/5.0\r\nAccept: */*\r\nConnection: close\r\n\r\n"
        sock.sendall(request.encode("ascii", errors="ignore"))
        while len(data) < _HTTP_READ_LIMIT:
            chunk = sock.recv(_HTTP_READ_LIMIT - len(data))
            if not chunk:
                break
            data.extend(chunk)
    except OSError:
        pass
    finally:
        if sock is not None:
            token.release(sock)
            close_quietly(sock)
    if token.cancelled:
        return HttpFacts()

    head, _separator, body = bytes(data).partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    parts = lines[0].split(None, 2) if lines else []
    if len(parts) < 2 or not parts[0].startswith(b"HTTP/") or not parts[1].isdigit():
        return HttpFacts()
    location = ""
    for line in lines[1:]:
        name, _colon, value = line.partition(b":")
        if name.strip().lower() == b"location":
            location = value.strip().decode("latin-1", errors="replace")
    return HttpFacts(status=int(parts[1]), location=location, body=body)


def _same_site(host: str, other: str) -> bool:
    host, other = host.lower().rstrip("."), other.lower().rstrip(".")
    return other == host or other.endswith("." + host) or host.endswith("." + other)


def stub_reason(host: str, facts: HttpFacts | None) -> str:
    """Почему ответ порта 80 — страница о блокировке. Пусто, если это обычный ответ."""
    if facts is None or facts.status is None:
        return ""
    if facts.status == _STATUS_UNAVAILABLE_FOR_LEGAL_REASONS:
        return "код 451 «недоступно по юридическим причинам»"
    target = (urlsplit(facts.location).hostname or "").lower() if facts.location else ""
    if target and not _same_site(host, target):
        for stub in _STUB_HOSTS:
            if target == stub or target.endswith("." + stub):
                return f"перенаправление на страницу о блокировке {target}"
    # Страницы провайдеров бывают в обеих кодировках, а регистр букв — любой.
    for encoding in ("utf-8", "cp1251"):
        text = facts.body.decode(encoding, errors="ignore").lower()
        if any(marker in text for marker in _STUB_MARKERS):
            return "страница со ссылкой на реестр блокировок"
        if any(marker in text for marker in _LAW_MARKERS) and any(marker in text for marker in _RESTRICTED_MARKERS):
            return "страница об ограничении доступа по закону 149-ФЗ"
    return ""


def case_variant(name: str) -> str:
    """То же имя другими буквами: последняя часть заглавными («googlevideo.COM»). Пусто — менять нечего.

    Имена сайтов регистр не различают, и сервер ответит на оба написания одинаково. Если одно
    проходит, а другое нет, их различает фильтр на пути: он ищет имя буква в букву.
    """
    head, dot, tail = str(name or "").rpartition(".")
    if not dot or not head or not tail.isascii() or not any(char.isalpha() for char in tail):
        return ""
    flipped = tail.upper() if tail != tail.upper() else tail.lower()
    return f"{head}.{flipped}"


def needs_refining(result: ProbeResult | None) -> bool:
    """Есть ли что уточнять: соединение не состоялось или оборвалось на шифровании."""
    if result is None or not result.ip:
        return False
    if result.kind == KIND_CONNECT:
        return True
    return result.kind in (KIND_RESET, KIND_TIMEOUT, KIND_TLS) and result.stage == STAGE_TLS


def later(delay: float, token: SocketCancel, call: Callable, *args, **kwargs):
    """Вызов через ``delay`` секунд; «Стоп» ждать не заставляет."""
    token.sleep(delay)
    return call(*args, **kwargs)


def collect(
    host: str,
    result: ProbeResult,
    *,
    submit: Callable,
    cancel: SocketCancel,
    ping: Callable[[str], bool | None] | None = None,
    any_name: bool = False,
) -> CauseFacts:
    """Дополнительные пробы к адресу, на котором сайт не открылся. Идут одновременно.

    ``any_name`` — сервер заведомо принимает любое имя (видеосервер Google).
    """
    ip = result.ip
    http = submit(http_probe, host, ip, cancel=cancel)
    if result.kind == KIND_CONNECT:
        ping_future = submit(ping, ip) if ping is not None else None
        return CauseFacts(
            host=host,
            result=result,
            http=http.result(),
            ping_ok=ping_future.result() if ping_future is not None else None,
            any_name=any_name,
        )
    neutral = submit(tls_hello, ip, NEUTRAL_NAME, cancel=cancel)
    nameless = submit(later, STAGGER_S, cancel, tls_hello, ip, None, cancel=cancel)
    allowed = [
        (name, submit(later, (2 + index) * STAGGER_S, cancel, tls_hello, ip, name, cancel=cancel))
        for index, name in enumerate(ALLOWED_NAMES)
    ]
    # Настоящее имя ещё раз: один сбой — не сравнение. Вывод «по имени» уверенный,
    # только если оно оборвалось и теперь, пока постороннее имя проходит.
    real_again = submit(later, (2 + len(ALLOWED_NAMES)) * STAGGER_S, cancel, tls_hello, ip, host, cancel=cancel)
    recased_name = case_variant(host)
    recased = (
        submit(later, (3 + len(ALLOWED_NAMES)) * STAGGER_S, cancel, tls_hello, ip, recased_name, cancel=cancel)
        if recased_name
        else None
    )
    answers = [(name, future.result()) for name, future in allowed]
    allowed_name, allowed_result = next((item for item in answers if item[1].answered), answers[-1])
    hellos = (neutral.result(), nameless.result(), allowed_result, real_again.result(), *([recased.result()] if recased is not None else []))
    small_window = window_control = None
    if result.kind in (KIND_RESET, KIND_TIMEOUT) and not any(item.answered for item in hellos):
        small_window, window_control = window_facts(ip, cancel)
    return CauseFacts(
        host=host,
        result=result,
        neutral=neutral.result(),
        nameless=nameless.result(),
        allowed=allowed_result,
        allowed_name=allowed_name,
        real_again=real_again.result(),
        http=http.result(),
        recased=recased.result() if recased is not None else None,
        recased_name=recased_name,
        any_name=any_name,
        small_window=small_window,
        window_control=window_control,
    )


def small_window_twice(ip: str, cancel: SocketCancel, *, timeout: float = HELLO_TIMEOUT_S) -> HelloResult:
    """Постороннее имя с окном в 1 байт; ответ засчитывается, только если пришёл и при повторе.

    Имя постороннее: проверяется адрес, а не имя сайта, которое фильтр может резать отдельно.
    Один ответ против нескольких обрывов мог бы оказаться случайностью фильтра «через раз».
    Проба общая для BlockCheck и «Подбора стратегии» (``strategy_search.environment``).
    """
    first = window_hello(ip, NEUTRAL_NAME, cancel=cancel, timeout=timeout)
    if not first.answered:
        return first
    again = later(STAGGER_S, cancel, window_hello, ip, NEUTRAL_NAME, cancel=cancel, timeout=timeout)
    return first if again.answered else again


def window_facts(ip: str, cancel: SocketCancel, *, timeout: float = HELLO_TIMEOUT_S) -> tuple[HelloResult, HelloResult | None]:
    """(малое окно дважды, контроль окном в 1 КБ). Контроль идёт только после ответа малого окна и
    последним: он же поздняя проверка, что адрес не стал отвечать сам. Общая проба BlockCheck и подбора."""
    small = small_window_twice(ip, cancel, timeout=timeout)
    if not small.answered:
        return small, None
    control = later(STAGGER_S, cancel, window_hello, ip, NEUTRAL_NAME, cancel=cancel, timeout=timeout, window=CONTROL_WINDOW_BYTES)
    return small, control


def reply_window_proven(small: HelloResult | None, control: HelloResult | None) -> bool:
    """Малое окно ответило дважды, а окно в 1 КБ оборвали или заглушили — фильтр по ответу в пределах окна."""
    return (
        small is not None
        and small.answered
        and control is not None
        and control.kind in (HELLO_RESET, HELLO_TIMEOUT)
    )


def _how(result: ProbeResult) -> str:
    return "молча пропадает" if result.kind == KIND_TIMEOUT else "обрывается"


def judge(facts: CauseFacts) -> Cause | None:
    """Вывод о способе блокировки или None, если фактов для него не хватает."""
    stub = stub_reason(facts.host, facts.http)
    if stub:
        return Cause(CAUSE_STUB_PAGE, f"провайдер показывает страницу о блокировке: {stub}", confident=True)

    result = facts.result
    if result.kind == KIND_CONNECT and result.stage == STAGE_CONNECT:
        if facts.http is not None and facts.http.status is not None:
            # Тот же адрес ответил по обычному порту: сервер жив, дорога до него есть.
            # Наблюдение, а не вывод: так выглядит и закрытый порт, и чужой адрес из hosts.
            return Cause(
                CAUSE_ADDRESS_CLOSED,
                "тот же адрес отвечает по обычному порту 80, а соединение с портом 443 не устанавливается",
            )
        if facts.ping_ok:
            return Cause(
                CAUSE_ADDRESS_CLOSED,
                "адрес отвечает на пинг, но соединение с портом 443 не устанавливается",
            )
        if facts.ping_ok is False:
            return Cause(CAUSE_ADDRESS_SILENT, "адрес не отвечает совсем — ни на соединение, ни на пинг")
        return None

    recased = facts.recased
    again = facts.real_again
    if (
        recased is not None
        and recased.kind == HELLO_OK
        and result.kind != KIND_TLS
        and not (again is not None and again.kind == HELLO_OK)
    ):
        # То же имя, только другими буквами, проходит: сервер их не различает, различает фильтр.
        return Cause(
            CAUSE_BY_NAME,
            f"блокировка по имени сайта: с именем {facts.host} соединение {_how(result)}, а то же имя другими "
            f"буквами ({facts.recased_name}) тот же сервер принимает — фильтр на пути ищет имя буква в букву",
            confident=True,
        )
    hellos = [item for item in (facts.neutral, facts.nameless) if item is not None and item.kind != HELLO_CANCELLED]
    if not hellos:
        return None
    # Сервер сам отказал на настоящее имя — тогда его отказ на постороннее ничего не
    # доказывает: он просто так отвечает. Сравнение честно, только когда с
    # настоящим именем соединение рвётся или молчит, а с другим — есть ответ.
    refused_by_server = result.kind == KIND_TLS
    passed = [item for item in hellos if item.kind == HELLO_OK or (item.answered and not refused_by_server)]
    if passed:
        if again is not None and again.kind == HELLO_OK:
            # Со второго раза настоящее имя прошло: первый обрыв был сбоем, сравнивать нечего.
            return None
        neutral_passed = facts.neutral is not None and facts.neutral in passed
        other = "с посторонним именем" if neutral_passed else "без имени"
        repeated = again is not None and again.kind in (HELLO_RESET, HELLO_TIMEOUT)
        return Cause(
            CAUSE_BY_NAME,
            f"блокировка по имени сайта: с именем {facts.host} соединение {_how(result)}"
            + (" (дважды подряд)" if repeated else "")
            + f", а {other} тот же сервер отвечает",
            # Не проверяли повторно (старые данные) — верим первому сравнению; повтор не дал ясного ответа — нет.
            confident=again is None or repeated,
        )
    if facts.real_again is not None and facts.real_again.kind == HELLO_OK:
        # Простое приветствие с настоящим именем проходит: ни имя, ни адрес не закрыты. Рвут само
        # соединение основной проверки — например, по почерку браузера (строка «Как Chrome»).
        return None
    allowed = facts.allowed
    if allowed is not None and allowed.answered:
        # Постороннее имя не прошло, а разрешённое прошло: к этому адресу пускают только
        # имена из списка. Это тоже блокировка по имени, а не закрытый адрес.
        return Cause(
            CAUSE_NAME_WHITELIST,
            f"к этому адресу проходят только разрешённые имена: с именем {facts.allowed_name} сервер отвечает, "
            f"а с именем {facts.host} и с посторонним — нет",
            confident=True,
        )
    probes = [
        *hellos,
        *([allowed] if allowed is not None and allowed.kind != HELLO_CANCELLED else []),
        *([recased] if recased is not None and recased.kind != HELLO_CANCELLED else []),
    ]
    checked = "ни с каким именем, даже с разрешённым" if allowed is not None and not allowed.answered else "ни с каким именем"
    if reply_window_proven(facts.small_window, facts.window_control) and all(
        item.kind in (HELLO_RESET, HELLO_TIMEOUT) for item in probes
    ):
        # Прямое сравнение на одном адресе: окно в 1 байт — ответ есть, окно в 1 КБ и обычное — нет.
        return Cause(
            CAUSE_REPLY_WINDOW,
            f"соединение с адресом устанавливается, а шифрование не проходит {checked}; но когда программа "
            "говорит серверу, что может принять всего 1 байт, начало его ответа приходит (дважды подряд), а с "
            "окном в 1 КБ — снова нет. Фильтр на пути режет любое шифрованное соединение с этого адреса, узнавая "
            "его по первым байтам ответа сервера, и читает этот ответ только в пределах окна приёма",
            confident=True,
        )
    if all(item.kind == HELLO_TIMEOUT for item in probes):
        # Тишина на любое имя — так молчит фильтр. Сервер, который сам не принимает
        # чужие имена, отвечает отказом или сбросом, а не молчанием.
        return Cause(
            CAUSE_BY_ADDRESS,
            f"соединение с адресом устанавливается, а шифрование — {checked}: ответа нет вовсе. Похоже, закрыт сам "
            "адрес или вся его сеть (так выглядит и режим «белых списков» адресов), а не имя сайта",
        )
    if facts.any_name and all(item.kind in (HELLO_RESET, HELLO_TIMEOUT) for item in probes):
        # Этот сервер принимает любое имя, даже чужое: раз не проходит ни одно, закрыт адрес, а не имя.
        return Cause(
            CAUSE_BY_ADDRESS,
            f"соединение с адресом устанавливается, а шифрование не проходит {checked}"
            + (", даже без имени" if facts.nameless in probes else "")
            + ". "
            "Этот сервер принимает любое имя, поэтому закрыт сам адрес для шифрованных соединений, а не имя сайта",
            confident=True,
        )
    if all(item.kind in (HELLO_RESET, HELLO_TIMEOUT) for item in probes):
        return Cause(
            CAUSE_NO_NAME_WORKS,
            f"шифрование не устанавливается {checked}: закрыт адрес или сервер сам сбрасывает "
            "соединения с чужими именами — по этим пробам не отличить",
        )
    return None
