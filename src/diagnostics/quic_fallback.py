"""Цена отката с QUIC на TCP: сколько браузер теряет, когда UDP 443 закрыт, а сайт открывается по TCP.

Браузер открывает сайт по QUIC (UDP 443) и, если ответа нет, уходит на TCP. Когда провайдер режет QUIC молча,
этот откат стоит секунды при каждом первом открытии сайта, хотя «сайт открывается». Проба QUIC
(``quic_probe``) говорит только, режут ли QUIC. Цену откат показывает лишь настоящий браузер: один и тот же сайт
открывается им без окна с QUIC и без него (``--disable-quic``), и сравнивается время.

Сбор (запуск браузера) и вывод (``judge``) разделены, вывод проверяется без браузера.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["FALLBACK_BROKEN", "FALLBACK_COST", "FALLBACK_NONE", "FALLBACK_UNKNOWN", "FallbackFacts", "FallbackVerdict", "collect", "judge"]

FALLBACK_NONE = "none"
FALLBACK_COST = "cost"
FALLBACK_BROKEN = "broken"
FALLBACK_UNKNOWN = "unknown"

# Разница меньше этого — шум запуска браузера, а не откат.
COST_MS = 400.0
# Сколько раз открывать сайт каждым способом: запуск браузера колеблется, берётся медиана.
ROUNDS = 2


@dataclass(frozen=True, slots=True)
class FallbackFacts:
    host: str
    # Итоги открытий (``blockcheck.browser_check.BrowserVisit``) с QUIC и без него.
    with_quic: tuple = ()
    without_quic: tuple = ()


@dataclass(frozen=True, slots=True)
class FallbackVerdict:
    code: str
    text: str
    host: str = ""
    quic_ms: float = 0.0
    tcp_ms: float = 0.0
    penalty_ms: float = 0.0

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "text": self.text,
            "host": self.host,
            "quic_ms": round(self.quic_ms),
            "tcp_ms": round(self.tcp_ms),
            "penalty_ms": round(self.penalty_ms),
        }


def collect(host: str, *, visit: Callable[..., object], cancelled: Callable[[], bool] = lambda: False, rounds: int = ROUNDS) -> FallbackFacts:
    """Открывает сайт браузером поочерёдно с QUIC и без него. ``visit`` — ``browser_check.open_in_browser``."""
    with_quic: list = []
    without: list = []
    for _ in range(max(1, rounds)):
        for quic, store in ((True, with_quic), (False, without)):
            if cancelled():
                return FallbackFacts(host, tuple(with_quic), tuple(without))
            store.append(visit(host, quic=quic, cancelled=cancelled))
    return FallbackFacts(host, tuple(with_quic), tuple(without))


def _times(visits) -> list[float]:
    return [visit.elapsed_ms for visit in visits if visit.ok and visit.elapsed_ms > 0]


def judge(facts: FallbackFacts) -> FallbackVerdict:
    """Вывод по открытиям браузером. Новых запусков не делает."""
    host = facts.host
    visits = (*facts.with_quic, *facts.without_quic)
    if not visits or any(getattr(visit, "state", "") == "missing" for visit in visits):
        return FallbackVerdict(FALLBACK_UNKNOWN, "на компьютере нет Edge или Chrome: цену отката не измерить", host)
    tcp = _times(facts.without_quic)
    if not tcp:
        return FallbackVerdict(FALLBACK_UNKNOWN, "без QUIC браузер сайт не открыл — про откат сказать нечего", host)
    quic = _times(facts.with_quic)
    tcp_ms = statistics.median(tcp)
    if not quic and facts.with_quic and all(getattr(visit, "state", "") == "no_answer" for visit in facts.with_quic):
        return FallbackVerdict(FALLBACK_UNKNOWN, "с QUIC браузер ничего не сказал о сайте — про откат сказать нечего", host)
    if not quic:
        reason = next((visit.text for visit in facts.with_quic if visit.text), "не открылась")
        return FallbackVerdict(
            FALLBACK_BROKEN, f"с QUIC браузер страницу не открывает ({reason}), а без него открывает за {tcp_ms / 1000:.1f} с", host, 0.0, tcp_ms
        )
    quic_ms = statistics.median(quic)
    penalty = quic_ms - tcp_ms
    if penalty >= COST_MS:
        return FallbackVerdict(
            FALLBACK_COST,
            f"откат с QUIC на TCP стоит около {penalty / 1000:.1f} с при каждом первом открытии: с QUIC {quic_ms / 1000:.1f} с, без него {tcp_ms / 1000:.1f} с",
            host,
            quic_ms,
            tcp_ms,
            penalty,
        )
    return FallbackVerdict(
        FALLBACK_NONE, f"откат почти ничего не стоит: с QUIC {quic_ms / 1000:.1f} с, без него {tcp_ms / 1000:.1f} с", host, quic_ms, tcp_ms, max(0.0, penalty)
    )
