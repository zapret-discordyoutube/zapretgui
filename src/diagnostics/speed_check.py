"""Скорость: отдают ли зарубежные серверы данные заметно медленнее российских.

Блокировка — не единственный способ мешать. Соединение может установиться и
данные идти, но медленно: так выглядит замедление и узкий канал за границу.
Остальные проверки этого не видят — для них медленный, но идущий поток
означает «открывается».

Здесь с нескольких серверов качается по два-три мегабайта (не дольше нескольких
секунд с каждого) и считается скорость. Сама по себе цифра ничего не значит:
она зависит от тарифа. Поэтому зарубежные серверы сравниваются с российскими,
измеренными тут же, тем же способом.

Вывод «заметно медленнее» делается, только когда медленны все измеренные
зарубежные серверы сразу и разница с российскими — в разы. Один медленный
сервер — это сам сервер.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass

from diagnostics.verdict import Level

__all__ = [
    "DOMESTIC",
    "FOREIGN",
    "SpeedReport",
    "SpeedSample",
    "SpeedServer",
    "check_speed",
    "summarize_speed",
]

# Сколько байт качать с сервера и не дольше скольких секунд.
SAMPLE_BYTES = 3_000_000
SAMPLE_SECONDS = 4.0
# Меньше этого — замер не показателен: слишком мало данных, чтобы говорить о скорости.
MIN_BYTES = 200_000
# Во сколько раз зарубежные должны быть медленнее российских, чтобы это назвать.
SLOWER_TIMES = 8.0
# И при этом сами по себе медленные: на быстром тарифе разница в разы ничему не мешает.
SLOW_KBPS = 250.0  # 2 Мбит/с


@dataclass(frozen=True, slots=True)
class SpeedServer:
    name: str
    host: str
    path: str
    domestic: bool = False


FOREIGN: tuple[SpeedServer, ...] = (
    SpeedServer("OVH (Франция)", "proof.ovh.net", "/files/10Mb.dat"),
    SpeedServer("Leaseweb (Нидерланды)", "mirror.leaseweb.com", "/alpine/v3.9/releases/x86_64/alpine-extended-3.9.0-x86_64.iso"),
    SpeedServer("Cloudflare", "speed.cloudflare.com", "/__down?bytes=5000000"),
)
DOMESTIC: tuple[SpeedServer, ...] = (
    SpeedServer("Яндекс", "mirror.yandex.ru", "/ubuntu/ls-lR.gz", domestic=True),
    SpeedServer("Selectel", "speedtest.selectel.ru", "/10MB", domestic=True),
)


@dataclass(frozen=True, slots=True)
class SpeedSample:
    server: SpeedServer
    # Килобайт в секунду. None — замер не получился или данных слишком мало.
    kbps: float | None = None
    received: int = 0
    seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class SpeedReport:
    level: Level
    headline: str
    samples: tuple[SpeedSample, ...]


def check_speed(
    download: Callable[[SpeedServer], tuple[int, float] | None],
    *,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[SpeedSample, ...]:
    """Серверы по очереди, вперемежку российские и зарубежные: замеры не мешают друг другу.

    ``download(сервер)`` возвращает (сколько байт пришло, за сколько секунд) или None.
    """
    order: list[SpeedServer] = []
    for index in range(max(len(FOREIGN), len(DOMESTIC))):
        if index < len(DOMESTIC):
            order.append(DOMESTIC[index])
        if index < len(FOREIGN):
            order.append(FOREIGN[index])
    samples: list[SpeedSample] = []
    for server in order:
        if should_stop is not None and should_stop():
            break
        got = download(server)
        if got is None:
            samples.append(SpeedSample(server))
            continue
        received, seconds = got
        # Мало данных за короткое время — файл кончился, замер не показателен. Мало данных
        # за всё отведённое время — это и есть медленный сервер: выбросить его значило бы
        # не заметить как раз замедление.
        enough = received >= MIN_BYTES or (received > 0 and seconds >= SAMPLE_SECONDS * 0.9)
        kbps = received / 1024 / seconds if enough and seconds > 0 else None
        samples.append(SpeedSample(server, kbps, int(received), float(seconds)))
    return tuple(samples)


def speed_text(kbps: float | None) -> str:
    if kbps is None:
        return "замер не получился"
    mbit = kbps * 8 / 1000
    return f"{mbit:.0f} Мбит/с" if mbit >= 10 else f"{mbit:.1f} Мбит/с"


def summarize_speed(samples: tuple[SpeedSample, ...]) -> SpeedReport:
    domestic = [item.kbps for item in samples if item.server.domestic and item.kbps is not None]
    foreign = [item.kbps for item in samples if not item.server.domestic and item.kbps is not None]
    if not domestic or not foreign:
        missing = "российских" if not domestic else "зарубежных"
        return SpeedReport(Level.UNKNOWN, f"Скорость сравнить не удалось: нет замера {missing} серверов", samples)
    home = max(domestic)
    abroad = statistics.median(foreign)
    # Медленны должны быть все измеренные зарубежные серверы, и их должно быть не меньше двух.
    all_slow = len(foreign) >= 2 and max(foreign) * SLOWER_TIMES <= home and max(foreign) <= SLOW_KBPS
    if all_slow:
        times = home / max(abroad, 0.001)
        return SpeedReport(
            Level.WARN,
            f"Зарубежные серверы отдают данные примерно в {times:.0f} раз медленнее российских: "
            f"{speed_text(abroad)} против {speed_text(home)} — похоже на замедление или узкий канал за границу",
            samples,
        )
    return SpeedReport(
        Level.OK,
        f"Заметной разницы в скорости нет: зарубежные серверы — {speed_text(abroad)}, российские — {speed_text(home)}",
        samples,
    )
