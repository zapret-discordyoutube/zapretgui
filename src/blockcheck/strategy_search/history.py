"""Что подбор помнит между запусками: какие стратегии помогли и какие нет.

Хранится в обычной базе настроек (``settings.sqlite3``, раздел ``blockcheck``,
ключ ``strategy_history``). Ключ записи — режим и цель: у разных сайтов и
режимов свои рабочие стратегии.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Сколько записей держать, чтобы раздел настроек не рос бесконечно.
MAX_TARGETS = 50
MAX_CONFIRMED = 20
MAX_FAILED = 1000


def history_key(scan_protocol: str, target: str, udp_games_scope: str = "all") -> str:
    protocol = str(scan_protocol or "tcp_https").strip().lower() or "tcp_https"
    normalized = str(target or "").strip().lower()
    if not normalized:
        return ""
    if protocol == "udp_games":
        return f"{protocol}|{udp_games_scope or 'all'}|{normalized}"
    return f"{protocol}|{normalized}"


@dataclass
class TargetHistory:
    confirmed: list[str] = field(default_factory=list)
    failed: dict[str, float] = field(default_factory=dict)
    # Как ведёт себя фильтр по последней проверке BlockCheck (код из ``diagnostics.filter_habits``);
    # пусто — проверки не было, она устарела или вывода в ней нет.
    habit: str = ""


def recent_filter_habit(runs, *, now: float) -> str:
    """Вывод «Как работает фильтр» из истории проверок, если он свежий.

    ``runs`` — записи ``blockcheck.check_history`` от старых к новым. Берётся
    последняя запись с выводом: он пишется только когда проверка шла без Zapret
    и VPN, так что описывает сам фильтр провайдера.
    """
    from datetime import datetime

    from blockcheck.strategy_search.ordering import FAILED_MEMORY_SECONDS

    for run in reversed(list(runs or ())):
        habit = str(run.get("habit") or "")
        if not habit:
            continue
        try:
            age = now - datetime.fromisoformat(str(run.get("time") or "")).timestamp()
        except ValueError:
            return ""
        return habit if age < FAILED_MEMORY_SECONDS else ""
    return ""


def load_target_history(key: str, *, now: float = 0.0) -> TargetHistory:
    """``now`` — текущее время: с ним в историю попадает и свежий вывод о фильтре."""
    from settings import store as settings_store

    if not key:
        return TargetHistory()
    try:
        section = settings_store.get_blockcheck_settings()
    except Exception:
        return TargetHistory()
    entry = (section.get("strategy_history") or {}).get(key) or {}
    return TargetHistory(
        confirmed=[str(item) for item in entry.get("confirmed") or [] if str(item)],
        failed={str(k): float(v) for k, v in (entry.get("failed") or {}).items() if str(k)},
        habit=recent_filter_habit(section.get("check_history"), now=now) if now else "",
    )


def count_recent_failures(key: str, *, now: float) -> int:
    """Сколько стратегий недавно не сработало на этой цели.

    Именно они уходят в конец очереди, поэтому по этому числу окно решает,
    есть ли что продолжать: 0 — подбор пойдёт с начала и спрашивать нечего.
    """
    from blockcheck.strategy_search.ordering import FAILED_MEMORY_SECONDS

    failed = load_target_history(key).failed
    return sum(1 for failed_time in failed.values() if failed_time and now - failed_time < FAILED_MEMORY_SECONDS)


def record_results(key: str, *, confirmed: list[str], failed: list[str], now: float) -> None:
    """Дописать итоги подбора одной транзакцией.

    Подтверждённая стратегия поднимается в начало списка и снимается с
    «не сработала»; провал запоминается со временем.
    """
    from settings import store as settings_store

    if not key or (not confirmed and not failed):
        return

    def _mutate(section: dict) -> None:
        history = section.setdefault("strategy_history", {})
        entry = history.pop(key, None) or {"confirmed": [], "failed": {}}
        confirmed_list = [item for item in entry.get("confirmed", []) if item not in confirmed]
        entry["confirmed"] = [*confirmed, *confirmed_list][:MAX_CONFIRMED]
        failed_map = dict(entry.get("failed", {}))
        for strategy_id in confirmed:
            failed_map.pop(strategy_id, None)
        for strategy_id in failed:
            failed_map[strategy_id] = float(now)
        if len(failed_map) > MAX_FAILED:
            newest = sorted(failed_map.items(), key=lambda item: item[1], reverse=True)[:MAX_FAILED]
            failed_map = dict(newest)
        entry["failed"] = failed_map
        # Свежая запись — последней: при переполнении уходят самые старые цели.
        history[key] = entry
        while len(history) > MAX_TARGETS:
            history.pop(next(iter(history)))

    settings_store.update_blockcheck_settings(_mutate)
