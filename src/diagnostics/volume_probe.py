"""Обрыв после 16 КБ у конкретного сайта: набираем объём по одному соединению.

Общая проверка обрыва (``diagnostics.freeze_check``) качает файлы с нескольких
хостингов и отвечает на вопрос «режет ли провайдер зарубежные серверы вообще».
Про конкретный сайт она ничего не говорит, а основной запрос к сайту обрыв
видит, только если главная страница сама больше 16 КБ. Многие сайты отвечают
коротким перенаправлением — и обрыв остаётся незамеченным: «открывается», хотя
в браузере страница висит.

Фильтр считает данные **по соединению**, а не по запросу. Поэтому здесь по
одному соединению подряд уходит много запросов, пока от сервера не наберётся
``TARGET_BYTES`` (больше окна обрыва). Набралось — обрыва нет. Соединение
замерло в окне «16 КБ» (по счёту фильтра, ``diagnostics.cut_window``) — похоже
на обрыв.

Чтобы не принять за обрыв сервер, который сам перестаёт отвечать на частые
одинаковые запросы, замирание проверяется второй раз другим видом запроса
(``HEAD``: ответы короче, и до того же объёма нужно больше запросов). Вывод
«обрыв» даётся, только если соединение замерло оба раза на том же объёме.

Сбор (``collect``) отделён от вывода (``judge``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from diagnostics import cut_window
from diagnostics.transfer_probe import RequestSample
from utils.socket_cancel import SocketCancel

__all__ = [
    "TARGET_BYTES",
    "VOLUME_CUT",
    "VOLUME_OK",
    "VOLUME_UNKNOWN",
    "RUN_CANCELLED",
    "RUN_CLOSED",
    "RUN_FAILED",
    "RUN_PASSED",
    "RUN_RESET",
    "RUN_STALLED",
    "VolumeFacts",
    "VolumeRun",
    "VolumeVerdict",
    "collect",
    "download",
    "filter_bytes",
    "judge",
]

# Сколько набрать, чтобы уверенно сказать «обрыва нет».
TARGET_BYTES = 48 * 1024
MAX_REQUESTS = 60
TIMEOUT_S = 5.0
# Столько ждём следующих данных: дольше — соединение замерло.
STALL_S = 3.0
# Пауза перед повтором, если подключиться для набора объёма не вышло с первого раза.
RETRY_PAUSE_S = 1.0
# Больше этого одна попытка не длится, сколько бы запросов ни осталось.
BUDGET_S = 6.0
# После стольких ответов видно, наберётся ли объём: если сервер отвечает
# крошечными ответами и за все запросы не выйти даже за окно обрыва,
# попытка прекращается сразу.
ESTIMATE_AFTER = 6

RUN_PASSED = "passed"  # набрали нужный объём
RUN_STALLED = "stalled"  # данные перестали идти
RUN_RESET = "reset"  # соединение сброшено посреди работы
RUN_CLOSED = "closed"  # сервер сам закрыл соединение или объём не набрался
RUN_FAILED = "failed"  # не удалось даже начать
RUN_CANCELLED = "cancelled"

VOLUME_OK = "ok"
VOLUME_CUT = "cut"
VOLUME_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class VolumeRun:
    kind: str
    # Сколько байт ответа (заголовки и тело на проводе) получено по соединению.
    received: int = 0
    # Сколько запросов успели отправить.
    requests: int = 0
    # Замер каждого запроса (время до первого байта, скорость тела): по ним
    # ``diagnostics.transfer_probe`` судит о качестве передачи без новых запросов.
    samples: tuple[RequestSample, ...] = ()
    # Служебные байты TLS от сервера по тому же соединению (рукопожатие); 0 — не измерены.
    handshake: int = 0


@dataclass(frozen=True, slots=True)
class VolumeFacts:
    first: VolumeRun
    # None — второй раз не проверяли: первый не замирал.
    second: VolumeRun | None = None


@dataclass(frozen=True, slots=True)
class VolumeVerdict:
    code: str
    text: str
    # На скольких килобайтах замерло (для вывода «обрыв»).
    cut_kb: int = 0


def download(
    host: str,
    ip: str,
    path: str = "/",
    *,
    method: str = "GET",
    target: int = TARGET_BYTES,
    port: int = 443,
    timeout: float = TIMEOUT_S,
    stall: float = STALL_S,
    budget: float = BUDGET_S,
    cancel: SocketCancel | None = None,
) -> VolumeRun:
    """Шлёт запросы ``method`` подряд по одному соединению «как Chrome» с ``ip``, пока не наберётся ``target`` байт ответа.

    Соединение — как у браузера (``utils.chrome_https.ChromeConnection``): то же
    рукопожатие, HTTP/2, браузерные заголовки, сжатие не распаковывается (считаются
    байты на проводе).
    """
    from utils.chrome_https import ChromeConnection

    token = cancel or SocketCancel()
    started = time.monotonic()
    received = 0
    handshake = 0
    requests = 0
    samples: list[RequestSample] = []

    def _run(kind: str) -> VolumeRun:
        if token.cancelled and kind != RUN_PASSED:
            kind = RUN_CANCELLED
        return VolumeRun(kind, received, requests, tuple(samples), handshake)

    if token.cancelled:
        return VolumeRun(RUN_CANCELLED)
    connection = ChromeConnection(host, ip, timeout=timeout, port=port, read_timeout=stall, cancel=token)
    try:
        while requests < MAX_REQUESTS and time.monotonic() - started < budget:
            before = received
            exchange = connection.request(path, method=method, enough=lambda body: before + len(body) >= target)
            requests += 1
            received += exchange.received
            handshake += exchange.progress.tls_in
            samples.append(
                RequestSample(
                    received=exchange.received,
                    ttfb_ms=exchange.ttfb_ms,
                    elapsed_ms=exchange.elapsed_ms,
                    body_ms=exchange.body_ms,
                    fresh=requests == 1,
                    failed=bool(exchange.error_code) and not exchange.progress.stopped_by_us,
                )
            )
            if exchange.progress.cancelled:
                return _run(RUN_CANCELLED)
            if exchange.reconnected:
                # Прежнее соединение закрыли между запросами: счёт по нему кончился, этот запрос — уже не по нему.
                received -= exchange.received
                handshake -= exchange.progress.tls_in
                requests -= 1
                samples.pop()
                return _run(RUN_CLOSED)
            if exchange.progress.stopped_by_us or received >= target:
                return _run(RUN_PASSED)
            if exchange.error_code:
                if requests == 1 and exchange.progress.status is None and not exchange.progress.secured:
                    return _run(RUN_FAILED)
                if exchange.stalled:
                    return _run(RUN_STALLED)
                return _run(RUN_RESET if exchange.reset else RUN_CLOSED)
            if requests >= ESTIMATE_AFTER and not cut_window.passed_window(cut_window.filter_bytes(received / requests * MAX_REQUESTS)):
                return _run(RUN_CLOSED)
        return _run(RUN_CLOSED)
    finally:
        connection.close()


def filter_bytes(run: VolumeRun) -> int:
    """Счёт фильтра по соединению: к ответам прибавляется рукопожатие TLS (``cut_window``)."""
    return cut_window.filter_bytes(run.received, handshake=run.handshake)


def _froze_in_window(run: VolumeRun) -> bool:
    return run.kind in (RUN_STALLED, RUN_RESET) and cut_window.cut_place(filter_bytes(run)) == cut_window.CUT_WINDOW


def collect(host: str, ip: str, path: str, *, cancel: SocketCancel) -> VolumeFacts:
    """Первая попытка обычными запросами; замерла в окне — вторая, запросами ``HEAD``."""
    first = download(host, ip, path, cancel=cancel)
    if first.kind == RUN_FAILED and not cancel.cancelled:
        # Узел мог не принять подключение сразу после предыдущих проб: один повтор после короткой паузы.
        time.sleep(RETRY_PAUSE_S)
        first = download(host, ip, path, cancel=cancel)
    if not _froze_in_window(first) or cancel.cancelled:
        return VolumeFacts(first=first)
    return VolumeFacts(first=first, second=download(host, ip, path, method="HEAD", cancel=cancel))


def judge(facts: VolumeFacts) -> VolumeVerdict:
    first, second = facts.first, facts.second
    if RUN_CANCELLED in (first.kind, second.kind if second else ""):
        return VolumeVerdict(VOLUME_UNKNOWN, "проверку объёма прервали")
    # Нужный объём мог не набраться, но окно обрыва пройдено без замирания — обрыва нет.
    if first.kind == RUN_PASSED or (first.kind == RUN_CLOSED and cut_window.passed_window(filter_bytes(first))):
        return VolumeVerdict(VOLUME_OK, f"по одному соединению получено {first.received // 1024} КБ без обрыва")
    if _froze_in_window(first):
        # Место обрыва — по счёту фильтра, как и окно: «~16 КБ», а не одни ответы.
        kb = filter_bytes(first) // 1024
        if second is not None and _froze_in_window(second):
            return VolumeVerdict(
                VOLUME_CUT,
                f"соединение замирает после ~{kb} КБ — дважды подряд, на ~{kb} и ~{filter_bytes(second) // 1024} КБ",
                cut_kb=kb,
            )
        if second is not None and second.kind == RUN_PASSED:
            return VolumeVerdict(
                VOLUME_UNKNOWN, f"один раз соединение замерло на ~{kb} КБ, при повторе прошло — случайный сбой"
            )
        return VolumeVerdict(
            VOLUME_UNKNOWN, f"соединение замерло на ~{kb} КБ, но повтор этого не подтвердил — вывода нет"
        )
    if first.kind == RUN_FAILED:
        return VolumeVerdict(VOLUME_UNKNOWN, "не удалось соединиться повторно — объём не проверен")
    if first.kind in (RUN_STALLED, RUN_RESET):
        return VolumeVerdict(
            VOLUME_UNKNOWN,
            f"соединение прервалось на ~{filter_bytes(first) // 1024} КБ — это не похоже на обрыв после 16 КБ",
        )
    return VolumeVerdict(
        VOLUME_UNKNOWN,
        f"по одному соединению удалось набрать только {first.received // 1024} КБ — "
        "на таком объёме обрыв после 16 КБ не проверить",
    )
