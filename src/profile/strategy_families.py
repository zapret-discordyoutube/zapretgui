"""Группы готовых стратегий по способу обхода.

Готовых стратегий сотни, а способов, которыми они обходят блокировку, немного.
Здесь каждая стратегия относится к одной группе с русским названием и
коротким объяснением: список на странице профиля показывает эти группы
заголовками. Способы стратегии берутся из её строк --lua-desync
(profile.strategy_visuals), новых данных в каталоге для этого не нужно.
"""

from __future__ import annotations

from dataclasses import dataclass

from profile.strategy_visuals import strategy_technique_keys


@dataclass(frozen=True)
class StrategyFamily:
    key: str
    title: str
    description: str
    icon_name: str
    color: str


# Порядок кортежа — порядок групп в списке: от самого простого способа к составным.
STRATEGY_FAMILIES: tuple[StrategyFamily, ...] = (
    StrategyFamily(
        "fake",
        "Подмена пакета",
        "перед настоящими данными уходит поддельный пакет",
        "fa5s.magic",
        "#ff6b6b",
    ),
    StrategyFamily(
        "fake_split",
        "Подмена и разделение",
        "поддельный пакет, затем данные по частям",
        "fa5s.layer-group",
        "#6fb8ff",
    ),
    StrategyFamily(
        "fake_disorder",
        "Подмена и перестановка",
        "поддельный пакет, затем части в другом порядке",
        "fa5s.random",
        "#7fd99a",
    ),
    StrategyFamily(
        "split",
        "Разделение",
        "данные уходят несколькими частями",
        "fa5s.columns",
        "#4cc2ff",
    ),
    StrategyFamily(
        "disorder",
        "Перестановка",
        "части данных уходят в другом порядке",
        "fa5s.random",
        "#58d17a",
    ),
    StrategyFamily(
        "host",
        "По имени сайта",
        "имя сайта режется, между частями ставится поддельное",
        "fa5s.user-secret",
        "#ff8f5a",
    ),
    StrategyFamily(
        "fake_udplen",
        "Подмена и изменение длины",
        "поддельный пакет, а у настоящего меняется длина",
        "fa5s.ruler",
        "#b58cff",
    ),
    StrategyFamily(
        "send",
        "С дополнительной отправкой",
        "перед обходом уходят лишние данные",
        "fa5s.paper-plane",
        "#76d0ff",
    ),
    StrategyFamily(
        "other",
        "Прочие",
        "способ не распознан или встречается редко",
        "fa5s.question",
        "#9aa6b2",
    ),
)

_FAMILY_BY_KEY = {family.key: family for family in STRATEGY_FAMILIES}
_FAMILY_RANK = {family.key: rank for rank, family in enumerate(STRATEGY_FAMILIES)}

_PRELUDE_TECHNIQUES = frozenset({"send", "syndata"})
_SPLIT_TECHNIQUES = frozenset({"split", "multisplit", "tcpseg"})
_DISORDER_TECHNIQUES = frozenset({"disorder", "multidisorder"})
# Эти способы сами несут поддельные части, отдельный fake им не нужен.
_FAKED_SPLIT_TECHNIQUES = frozenset({"fakedsplit", "fakemultisplit"})
_FAKED_DISORDER_TECHNIQUES = frozenset({"fakeddisorder", "fakemultidisorder"})


def strategy_family(key: str) -> StrategyFamily:
    return _FAMILY_BY_KEY.get(str(key or ""), _FAMILY_BY_KEY["other"])


def strategy_family_rank(key: str) -> int:
    return _FAMILY_RANK.get(str(key or ""), len(_FAMILY_RANK))


def strategy_family_key(technique_keys) -> str:
    """Группа стратегии по её способам (ключи из profile.strategy_visuals)."""
    keys = tuple(str(key or "") for key in tuple(technique_keys or ()))
    key_set = set(keys)
    if key_set & _PRELUDE_TECHNIQUES:
        return "send"
    if "hostfakesplit" in key_set:
        return "host"
    has_fake = "fake" in key_set
    # Группу задаёт первый по порядку способ разделения или перестановки.
    for key in keys:
        if key in _FAKED_SPLIT_TECHNIQUES:
            return "fake_split"
        if key in _FAKED_DISORDER_TECHNIQUES:
            return "fake_disorder"
        if key in _SPLIT_TECHNIQUES:
            return "fake_split" if has_fake else "split"
        if key in _DISORDER_TECHNIQUES:
            return "fake_disorder" if has_fake else "disorder"
    if has_fake:
        return "fake_udplen" if "udplen" in key_set else "fake"
    return "other"


def strategy_family_key_for_entry(entry) -> str:
    visual = getattr(entry, "visual", None)
    technique_keys = getattr(visual, "technique_keys", None)
    if technique_keys is None:
        technique_keys = strategy_technique_keys(str(getattr(entry, "args", "") or ""))
    return strategy_family_key(technique_keys)


# Способ, которым в каталоге пользуются одна-две стратегии, своего заголовка
# не получает: такие стратегии уходят в «Прочие».
_MIN_FAMILY_SIZE = 3


def strategy_family_keys(entries) -> dict[str, str]:
    """Группа каждой стратегии каталога: {id стратегии: ключ группы}.

    Считается по всему каталогу, а не по найденному поиском: иначе стратегия
    переезжала бы из группы в группу, пока пользователь печатает запрос.
    """
    entries = dict(entries or {})
    raw = {strategy_id: strategy_family_key_for_entry(entry) for strategy_id, entry in entries.items()}
    counts: dict[str, int] = {}
    for family_key in raw.values():
        counts[family_key] = counts.get(family_key, 0) + 1
    return {
        strategy_id: family_key if counts[family_key] >= _MIN_FAMILY_SIZE else "other"
        for strategy_id, family_key in raw.items()
    }


def strategy_sort_key(family_key: str, entry, state) -> tuple[int, bool, str]:
    """Порядок строк списка: по группам, внутри группы избранные первыми, затем по имени."""
    return (
        strategy_family_rank(family_key),
        not bool(getattr(state, "favorite", False)),
        str(getattr(entry, "name", "") or "").lower(),
    )


def strategy_count_text(count: int) -> str:
    count = max(0, int(count or 0))
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} стратегия"
    if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        return f"{count} стратегии"
    return f"{count} стратегий"


__all__ = [
    "STRATEGY_FAMILIES",
    "StrategyFamily",
    "strategy_count_text",
    "strategy_family",
    "strategy_family_key",
    "strategy_family_key_for_entry",
    "strategy_family_keys",
    "strategy_family_rank",
    "strategy_sort_key",
]
