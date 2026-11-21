"""Сайты, которые стратегия Zapret уже открывала: у такого сайта адрес не закрыт.

«Бан по адресу» — вывод, что сайт закрыт на самом адресе и никакой обход не поможет. Если же
«Подбор стратегии» на этом компьютере уже нашёл для сайта рабочую стратегию, адрес открыт: молчит
фильтр, который стратегия обходит (жалоба 2026-11-17: карточка YouTube говорила «бан по адресу», а адрес
отвечал на пинг и открывался стратегиями).

Источник — то, что подбор запоминает сам (``blockcheck.strategy_search.history``, настройка
``blockcheck.strategy_history``): запись «режим|цель» с непустым списком подтверждённых стратегий.
Подтверждение снимается, когда та же стратегия потом не сработала, так что устаревшее само уходит.
Игровой (UDP) подбор сюда не входит: он про порты игр, а не про сайт.
"""

from __future__ import annotations

__all__ = ["annotate", "covers", "opened_targets"]


def opened_targets() -> frozenset[str]:
    """Цели (домены), для которых подбор подтвердил хотя бы одну рабочую стратегию."""
    from blockcheck.strategy_search.probe_profile import PROTOCOL_UDP_GAMES
    from settings import store

    try:
        history = (store.get_blockcheck_settings().get("strategy_history") or {})
    except Exception:
        return frozenset()
    targets: set[str] = set()
    for key, entry in history.items():
        parts = str(key).split("|")
        if parts[0] == PROTOCOL_UDP_GAMES or not isinstance(entry, dict) or not entry.get("confirmed"):
            continue
        target = parts[-1].strip().lower().rstrip(".")
        if target:
            targets.add(target)
    return frozenset(targets)


def covers(host: str, targets) -> bool:
    """Цель подбора — это сам сайт или его родительский домен (стратегия для youtube.com открывает и www.youtube.com)."""
    name = str(host or "").strip().lower().rstrip(".")
    return bool(name) and any(name == target or name.endswith("." + target) for target in targets)


def annotate(probes) -> None:
    """Отмечает в ``probe.opened_by_strategy`` закрытые по адресу сайты, которые стратегия уже открывала."""
    from diagnostics.verdict import ReachState

    candidates = [probe for probe in probes if probe.reach_state == ReachState.IP_BLOCK]
    if not candidates:
        return
    targets = opened_targets()
    if not targets:
        return
    for probe in candidates:
        probe.opened_by_strategy = covers(probe.host, targets)
