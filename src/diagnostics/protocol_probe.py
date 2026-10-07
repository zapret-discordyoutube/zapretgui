"""Сайт по четырём дорогам отдельно: TLS 1.2, TLS 1.3, «как Chrome» и обычный HTTP.

Основная проверка отвечает на вопрос «открывается ли сайт». Здесь тот же
адрес пробуется несколькими способами по отдельности:

- **TLS 1.2** и **TLS 1.3** — шифрованное соединение только этой версией.
  Фильтр иногда режет лишь одну из них: по этому видно, какая стратегия нужна.
- **Как Chrome** — приветствие с тем же составом, что у браузера
  (``diagnostics.browser_hello``). Фильтр умеет узнавать программу по этому
  «почерку»: если обычное приветствие к адресу проходит, а браузерное — нет
  (дважды подряд), в самом браузере сайт может не открываться.
- **HTTP** (порт 80, без шифрования) — отвечает ли сайт и не подставляет ли
  провайдер страницу о блокировке.

Выводы осторожные. Отказ самого сервера («такой версии у меня нет») — не
блокировка, а справка. Порт 80 может быть закрыт у самого сайта, поэтому
молчание на нём — «не ответил», а не «заблокирован».
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from diagnostics.block_cause import (
    HELLO_ALERT,
    HELLO_CANCELLED,
    HELLO_CONNECT,
    HELLO_ERROR,
    HELLO_OK,
    HELLO_RESET,
    HELLO_TIMEOUT,
    TLS_1_2,
    TLS_1_3,
    HelloResult,
    HttpFacts,
    http_probe,
    stub_reason,
    tls_hello,
)
from diagnostics import browser_hello
from utils.socket_cancel import SocketCancel

__all__ = [
    "CODE_BROWSER_ONLY",
    "CODE_FINGERPRINT",
    "PROTO_BROWSER",
    "PROTO_HTTP",
    "PROTO_TLS12",
    "PROTO_TLS13",
    "STATE_FAIL",
    "STATE_INFO",
    "STATE_OK",
    "STATE_UNKNOWN",
    "ProtocolFacts",
    "ProtocolLine",
    "collect",
    "judge",
]

PROTO_TLS12 = "tls12"
PROTO_TLS13 = "tls13"
PROTO_BROWSER = "browser"
PROTO_HTTP = "http"

# Обычное приветствие проходит, а браузерное — нет: режут по «почерку» браузера.
CODE_FINGERPRINT = "fingerprint"
# Наоборот: браузерное проходит, обычное — нет. В браузере сайт, возможно, открывается.
CODE_BROWSER_ONLY = "browser_only"

# Пауза перед повтором браузерного приветствия: один сбой — ещё не вывод.
BROWSER_RETRY_PAUSE_S = 0.5
# Шаг между стартами проб к одному имени.
STAGGER_S = 0.25

STATE_OK = "ok"
STATE_FAIL = "fail"
# Не блокировка и не успех: сервер сам так устроен.
STATE_INFO = "info"
STATE_UNKNOWN = "unknown"

_TITLES = {PROTO_TLS12: "TLS 1.2", PROTO_TLS13: "TLS 1.3", PROTO_BROWSER: "Как Chrome", PROTO_HTTP: "HTTP"}


@dataclass(frozen=True, slots=True)
class ProtocolFacts:
    host: str
    ip: str
    tls12: HelloResult | None = None
    tls13: HelloResult | None = None
    http: HttpFacts | None = None
    # За сколько ответил порт 80. None — не ответил.
    http_ms: float | None = None
    cancelled: bool = False
    # Приветствие с составом Chrome. При сбое — итог повторной попытки.
    browser: HelloResult | None = None


@dataclass(frozen=True, slots=True)
class ProtocolLine:
    key: str
    title: str
    state: str
    # Одно-два слова для метки на карточке: «работает», «сброс», «заглушка».
    word: str
    # Полная фраза для подробностей.
    text: str
    ms: float | None = None
    # Особый вывод строки (``CODE_FINGERPRINT`` / ``CODE_BROWSER_ONLY``). Пусто — обычная строка.
    code: str = ""


def _timed_http(host: str, ip: str, cancel: SocketCancel) -> tuple[HttpFacts, float | None]:
    started = time.perf_counter()
    facts = http_probe(host, ip, cancel=cancel)
    return facts, ((time.perf_counter() - started) * 1000.0 if facts.status is not None else None)


def _later(delay: float, token: SocketCancel, call: Callable, *args, **kwargs):
    """Вызов через ``delay`` секунд; «Стоп» ждать не заставляет."""
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline and not token.cancelled:
        time.sleep(0.02)
    return call(*args, **kwargs)


def _browser_hello(host: str, ip: str, cancel: SocketCancel) -> HelloResult:
    """Приветствие Chrome; сбой перепроверяется один раз после паузы."""
    result = browser_hello.send_hello(ip, host, cancel=cancel)
    if result.kind not in (HELLO_RESET, HELLO_TIMEOUT):
        return result
    deadline = time.monotonic() + BROWSER_RETRY_PAUSE_S
    while time.monotonic() < deadline:
        if cancel.cancelled:
            return HelloResult(HELLO_CANCELLED)
        time.sleep(0.05)
    return browser_hello.send_hello(ip, host, cancel=cancel)


def collect(host: str, ip: str, *, submit: Callable, cancel: SocketCancel) -> ProtocolFacts:
    """Четыре пробы к одному адресу, одновременно."""
    # Три шифрованных соединения с одним именем уходят не разом, а с шагом: пачка одновременных
    # соединений к одному сайту сама бывает поводом для фильтра придержать их все.
    tls12 = submit(tls_hello, ip, host, cancel=cancel, version=TLS_1_2)
    tls13 = submit(_later, STAGGER_S, cancel, tls_hello, ip, host, cancel=cancel, version=TLS_1_3)
    browser = submit(_later, 2 * STAGGER_S, cancel, _browser_hello, host, ip, cancel)
    http = submit(_timed_http, host, ip, cancel)
    http_facts, http_ms = http.result()
    return ProtocolFacts(
        host=host,
        ip=ip,
        tls12=tls12.result(),
        tls13=tls13.result(),
        browser=browser.result(),
        http=http_facts,
        http_ms=http_ms,
        cancelled=cancel.cancelled,
    )


def _ms(value: float | None) -> str:
    if value is None:
        return ""
    return "меньше 1 мс" if value < 1 else f"{round(value)} мс"


def _tls_line(key: str, result: HelloResult | None) -> ProtocolLine | None:
    title = _TITLES[key]
    if result is None or result.kind == HELLO_CANCELLED:
        return None
    if result.kind == HELLO_OK:
        return ProtocolLine(key, title, STATE_OK, "работает", f"шифрование установилось за {_ms(result.ms)}", result.ms)
    if result.kind == HELLO_ALERT:
        return ProtocolLine(
            key, title, STATE_INFO, "нет у сервера", "сервер ответил отказом: этой версией он не работает — это не блокировка"
        )
    if result.kind == HELLO_RESET:
        return ProtocolLine(key, title, STATE_FAIL, "сброс", "соединение сброшено на приветствии")
    if result.kind == HELLO_TIMEOUT:
        return ProtocolLine(key, title, STATE_FAIL, "молчит", "после приветствия ответа нет")
    if result.kind == HELLO_CONNECT:
        return ProtocolLine(key, title, STATE_UNKNOWN, "нет соединения", "не удалось соединиться с адресом")
    if result.kind == HELLO_ERROR:
        return ProtocolLine(key, title, STATE_UNKNOWN, "не проверено", "шифрование не установилось, причину определить не удалось")
    return ProtocolLine(key, title, STATE_FAIL, "чужой ответ", "вместо шифрования пришло что-то другое: ответил не сервер")


def _browser_line(facts: ProtocolFacts) -> ProtocolLine | None:
    """Строка «Как Chrome». Вывод о «почерке» — только из сравнения с обычным приветствием."""
    title = _TITLES[PROTO_BROWSER]
    result = facts.browser
    if result is None or result.kind == HELLO_CANCELLED:
        return None
    plain = [item.kind for item in (facts.tls12, facts.tls13) if item is not None]
    plain_passes = HELLO_OK in plain
    plain_cut = bool(plain) and all(kind in (HELLO_RESET, HELLO_TIMEOUT) for kind in plain)
    # Отказ сервера — тоже ответ сервера: приветствие до него дошло.
    if result.kind in (HELLO_OK, HELLO_ALERT):
        if plain_cut:
            return ProtocolLine(
                PROTO_BROWSER,
                title,
                STATE_OK,
                "проходит",
                "приветствие, какое шлёт Chrome, до сервера доходит, а обычное — нет: "
                "в самом браузере сайт, возможно, открывается",
                result.ms,
                CODE_BROWSER_ONLY,
            )
        return ProtocolLine(
            PROTO_BROWSER, title, STATE_OK, "проходит", f"приветствие, какое шлёт Chrome, до сервера доходит (ответ за {_ms(result.ms)})", result.ms
        )
    if result.kind == HELLO_CONNECT:
        return ProtocolLine(PROTO_BROWSER, title, STATE_UNKNOWN, "нет соединения", "не удалось соединиться с адресом")
    if result.kind in (HELLO_RESET, HELLO_TIMEOUT):
        how = "соединение сбрасывают" if result.kind == HELLO_RESET else "ответа нет"
        if plain_passes:
            return ProtocolLine(
                PROTO_BROWSER,
                title,
                STATE_FAIL,
                "режется",
                f"обычное приветствие к этому адресу проходит, а такое, как у Chrome, — нет ({how}, проверено дважды). "
                "Похоже на блокировку по «почерку» браузера: в Chrome сайт может не открываться, "
                "а в Firefox или в свежем Chrome (у него почерк уже другой) — открываться",
                code=CODE_FINGERPRINT,
            )
        return ProtocolLine(
            PROTO_BROWSER,
            title,
            STATE_FAIL,
            "сброс" if result.kind == HELLO_RESET else "молчит",
            f"на приветствие, какое шлёт Chrome, {how} — как и на обычное",
        )
    return ProtocolLine(PROTO_BROWSER, title, STATE_FAIL, "чужой ответ", "вместо шифрования пришло что-то другое: ответил не сервер")


def _http_line(facts: ProtocolFacts) -> ProtocolLine | None:
    title = _TITLES[PROTO_HTTP]
    http = facts.http
    if http is None:
        return None
    if http.status is None:
        # Порт 80 у многих сайтов закрыт самим сайтом.
        return ProtocolLine(PROTO_HTTP, title, STATE_UNKNOWN, "не ответил", "порт 80 не ответил — у сайта он может быть закрыт")
    stub = stub_reason(facts.host, http)
    if stub:
        return ProtocolLine(PROTO_HTTP, title, STATE_FAIL, "заглушка", f"страница провайдера о блокировке: {stub}", facts.http_ms)
    took = _ms(facts.http_ms)
    if 300 <= http.status < 400:
        return ProtocolLine(
            PROTO_HTTP, title, STATE_OK, "переход", f"сайт отвечает переходом (код {http.status}) за {took}", facts.http_ms
        )
    return ProtocolLine(PROTO_HTTP, title, STATE_OK, f"код {http.status}", f"сайт отвечает, код {http.status}, за {took}", facts.http_ms)


def judge(facts: ProtocolFacts | None) -> tuple[ProtocolLine, ...]:
    """Строки по каждой дороге. Пусто — проверку сняли или не делали."""
    if facts is None or facts.cancelled:
        return ()
    lines = (
        _tls_line(PROTO_TLS12, facts.tls12),
        _tls_line(PROTO_TLS13, facts.tls13),
        _browser_line(facts),
        _http_line(facts),
    )
    return tuple(line for line in lines if line is not None)
