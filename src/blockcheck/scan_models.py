"""Strategy scan data models — pure Python, no Qt dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StrategyProbeResult:
    """Итог проверки одной стратегии."""

    strategy_name: str
    strategy_id: str
    strategy_args: str
    target: str
    # Стратегия надёжно работает: прошла все попытки подряд.
    success: bool
    # Время ответа сервера в удачной попытке (не включает запуск winws2).
    time_ms: float
    # Почему не засчитана (пусто, если работает).
    error: str = ""
    scan_protocol: str = "tcp_https"
    # ``blockcheck.strategy_search.verdict.VERDICT_*``.
    verdict: str = ""
    attempts_ok: int = 0
    attempts_total: int = 0
    # Строки, которые проверялись и ровно так же пишутся в пресет «Применить».
    apply_lines: tuple[str, ...] = ()
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyScanReport:
    """Итог подбора целиком."""

    target: str
    total_tested: int
    total_available: int = 0
    working_strategies: list[StrategyProbeResult] = field(default_factory=list)
    failed_strategies: list[StrategyProbeResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    cancelled: bool = False
    # Цель открывалась и без обхода (пользователь решил проверять всё равно
    # или отказался от подбора).
    baseline_accessible: bool = False
    scan_protocol: str = "tcp_https"
    # Причина аварийной остановки подбора (нет интернета, WinDivert недоступен,
    # блокировка по адресу, которую стратегии не снимают); пусто — подбор
    # завершился или отменён пользователем.
    fatal_error: str = ""
    # Вид остановки (``strategy_search.engine.STOP_*``): у панели итога свой
    # вид для «нет интернета», «блокировка по адресу» и т.д.
    stop_kind: str = ""
