"""Замедление по имени сайта: сайт открывается, но данные к нему придерживают.

Блокировка — не единственный способ мешать сайту. Фильтр может пропустить
соединение и ограничить его скорость, если увидел в приветствии имя сайта
(так замедляли YouTube). Остальные проверки этого не видят: для них медленный,
но идущий поток означает «открывается».

Скорость самого сайта ничего не доказывает — она зависит от его серверов.
Поэтому здесь сравнение на **одном и том же** постороннем сервере: с него
качается один и тот же файл дважды — с его собственным именем (контроль) и с
именем проверяемого сайта в приветствии. Сервер отдаёт файл в обоих случаях,
дорога одна, сервер один; отличается только имя, которое видит фильтр. Если с
именем сайта скорость в разы ниже — замедляют по имени.

Единичный медленный замер перепроверяется, и контроль в конце повторяется:
вдруг просела сама линия. Проверяются только сайты, которые открылись: у
закрытого сайта имя и так режут.

Здесь нет сети в выводах: загрузка (``download``) отделена от решения
(``judge``), а в сбор (``collect``) она передаётся снаружи.
"""

from __future__ import annotations

import socket
import ssl
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from utils.socket_cancel import SocketCancel, close_quietly

__all__ = [
    "MAX_NAMES",
    "PATH",
    "RUN_OK",
    "SERVER_HOST",
    "SLOW",
    "FINE",
    "UNKNOWN",
    "NameFacts",
    "NameRun",
    "NameVerdict",
    "collect",
    "download",
    "judge",
    "speed_text",
]

# Сервер, который отдаёт файл при любом имени в приветствии, и большой файл на нём.
SERVER_HOST = "mirror.yandex.ru"
PATH = "/ubuntu/ls-lR.gz"
# Столько хватает, чтобы скорость была видна: на быстрой линии это доля секунды.
ENOUGH_BYTES = 600_000
# Дольше одной загрузки не ждём: при замедлении до десятков килобайт в секунду этого хватает.
SAMPLE_SECONDS = 3.0
CONNECT_TIMEOUT_S = 4.0
MAX_NAMES = 6

# Во сколько раз с именем сайта должно быть медленнее, чем с именем сервера.
SLOWER_TIMES = 6.0
# И при этом медленно само по себе: 2 Мбит/с.
SLOW_KBPS = 250.0
# Меньше этого замер ни о чём не говорит: файл не начал идти.
MIN_BYTES = 20_000

RUN_OK = "ok"
RUN_CONNECT = "connect"  # не соединились или не прошло шифрование
RUN_REFUSED = "refused"  # сервер с этим именем файл не отдал
RUN_CANCELLED = "cancelled"

FINE = "ok"
SLOW = "slow"
UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class NameRun:
    name: str
    kind: str
    received: int = 0
    seconds: float = 0.0

    @property
    def kbps(self) -> float | None:
        """Килобайт в секунду. None — замер не получился."""
        if self.kind != RUN_OK or self.seconds <= 0 or self.received < MIN_BYTES:
            # Мало байт за всё отведённое время — это и есть замедление, а не «нет замера».
            if self.kind == RUN_OK and self.seconds >= SAMPLE_SECONDS * 0.9:
                return self.received / 1024 / self.seconds
            return None
        return self.received / 1024 / self.seconds


@dataclass(frozen=True, slots=True)
class NameFacts:
    # Загрузки с собственным именем сервера в начале.
    control: tuple[NameRun, ...]
    # По каждому имени — одна загрузка или две (вторая — перепроверка медленной).
    names: tuple[tuple[NameRun, ...], ...] = ()
    # Контроль в конце — только если какое-то имя шло медленно.
    control_after: tuple[NameRun, ...] = ()


@dataclass(frozen=True, slots=True)
class NameVerdict:
    name: str
    code: str
    text: str
    kbps: float | None = None


def download(
    ip: str,
    name: str,
    path: str = PATH,
    *,
    seconds: float = SAMPLE_SECONDS,
    enough: int = ENOUGH_BYTES,
    cancel: SocketCancel | None = None,
) -> NameRun:
    """Качает файл с адреса ``ip``, назвавшись в приветствии именем ``name``.

    Сертификат не проверяется: сервер заведомо не тот, чьё имя названо, — нужна
    только скорость потока. Время считается от первого байта ответа.
    """
    token = cancel or SocketCancel()
    sock: socket.socket | None = None
    wrapped: ssl.SSLSocket | None = None
    received = 0
    first_at = 0.0
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
        if not token.track(sock):
            return NameRun(name, RUN_CANCELLED)
        sock.settimeout(CONNECT_TIMEOUT_S)
        sock.connect((ip, 443))
        wrapped = context.wrap_socket(sock, server_hostname=name)
        token.track(wrapped)
        request = f"GET {path} HTTP/1.1\r\nHost: {name}\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
        wrapped.sendall(request.encode("ascii", errors="ignore"))
        head = b""
        stop_at = 0.0
        while True:
            wrapped.settimeout(CONNECT_TIMEOUT_S if not first_at else max(0.05, stop_at - time.monotonic()))
            chunk = wrapped.recv(65536)
            if not chunk:
                break
            if not first_at:
                first_at = time.monotonic()
                stop_at = first_at + seconds
                head = chunk[:16]
                if not head.startswith(b"HTTP/1.") or head[9:12] != b"200":
                    return NameRun(name, RUN_REFUSED)
            received += len(chunk)
            if received >= enough or time.monotonic() >= stop_at:
                break
    except (socket.timeout, TimeoutError):
        if not first_at:
            return NameRun(name, RUN_CANCELLED if token.cancelled else RUN_CONNECT)
    except (OSError, ssl.SSLError):
        if token.cancelled:
            return NameRun(name, RUN_CANCELLED)
        if not first_at:
            return NameRun(name, RUN_CONNECT)
    finally:
        for item in (wrapped, sock):
            if item is not None:
                token.release(item)
                close_quietly(item)
    if token.cancelled:
        return NameRun(name, RUN_CANCELLED)
    return NameRun(name, RUN_OK, received, max(time.monotonic() - first_at, 0.001))


def _slow(run: NameRun, control_kbps: float) -> bool:
    kbps = run.kbps
    return kbps is not None and kbps <= SLOW_KBPS and kbps * SLOWER_TIMES <= control_kbps


def collect(
    names: Sequence[str],
    fetch: Callable[[str], NameRun],
    *,
    should_stop: Callable[[], bool] | None = None,
) -> NameFacts:
    """Контроль, затем имена по очереди: замеры не делят между собой линию.

    ``fetch(имя)`` — одна загрузка с этим именем в приветствии.
    """

    def _stopped() -> bool:
        return should_stop is not None and should_stop()

    # Первая загрузка «холодная» и выходит медленнее: контроль меряется дважды, в счёт идёт лучший.
    start = (fetch(SERVER_HOST), fetch(SERVER_HOST))
    known = [run.kbps for run in start if run.kbps is not None]
    if not known or _stopped():
        return NameFacts(start)
    runs: list[tuple[NameRun, ...]] = []
    again = False
    for name in list(dict.fromkeys(names))[:MAX_NAMES]:
        if _stopped():
            break
        run = fetch(name)
        if _slow(run, max(known)) and not _stopped():
            # Одному медленному замеру не верим.
            runs.append((run, fetch(name)))
            again = True
        else:
            runs.append((run,))
    # Что-то шло медленно — контроль ещё раз: вдруг просела сама линия.
    end = (fetch(SERVER_HOST),) if again and not _stopped() else ()
    return NameFacts(start, tuple(runs), end)


def speed_text(kbps: float | None) -> str:
    if kbps is None:
        return "замер не получился"
    mbit = kbps * 8 / 1000
    return f"{mbit:.0f} Мбит/с" if mbit >= 10 else f"{mbit:.1f} Мбит/с"


def judge(facts: NameFacts) -> tuple[float | None, tuple[NameVerdict, ...]]:
    """(скорость контроля, вывод по каждому имени). Скорость None — контроль не получился, выводов нет."""
    known = [run.kbps for run in facts.control if run.kbps is not None]
    if not known:
        return None, ()
    control = max(known)
    after = [run.kbps for run in facts.control_after]
    # Контроль в конце не получился или сам стал медленным — просела линия, медленное имя не улика.
    sagged = any(kbps is None or kbps * SLOWER_TIMES <= control for kbps in after)
    if after and not sagged:
        control = min(control, *[kbps for kbps in after if kbps is not None])
    verdicts: list[NameVerdict] = []
    for runs in facts.names:
        name = runs[0].name
        measured = [run.kbps for run in runs if run.kbps is not None]
        if any(run.kind == RUN_CANCELLED for run in runs):
            continue
        if not measured:
            reason = (
                "сервер с этим именем файл не отдал"
                if runs[0].kind == RUN_REFUSED
                else "соединение с этим именем к постороннему серверу не прошло"
            )
            verdicts.append(NameVerdict(name, UNKNOWN, f"скорость не измерить: {reason}"))
            continue
        best = max(measured)
        if len(runs) > 1 and all(_slow(run, control) for run in runs) and sagged:
            verdicts.append(NameVerdict(name, UNKNOWN, "скорость не сравнить: во время замера просела сама линия", best))
        elif len(runs) > 1 and all(_slow(run, control) for run in runs):
            verdicts.append(
                NameVerdict(
                    name,
                    SLOW,
                    f"{speed_text(best)} против {speed_text(control)} с тем же сервером под его собственным именем — "
                    "соединения с этим именем замедляют",
                    best,
                )
            )
        else:
            verdicts.append(NameVerdict(name, FINE, f"{speed_text(best)} — не медленнее, чем с обычным именем", best))
    return control, tuple(verdicts)
