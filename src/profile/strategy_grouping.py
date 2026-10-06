"""Как список готовых стратегий раскладывается по группам.

Группировок три, человек переключает их над списком:
- по способу обхода (profile.strategy_families) — так список открывается;
- по серии — первому слову названия («General», «Flowseal»);
- по источнику — уточнению «· из …» в названии («из Steam»).

Внутри большой группы стратегии дополнительно делятся подзаголовками: в
группах способа и источника — по сериям, в группах серии — по способам.
Всё считается по названию и строкам --lua-desync стратегии, новых данных в
каталоге для этого не нужно.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from profile.strategy_families import (
    STRATEGY_FAMILIES,
    strategy_family,
    strategy_family_keys,
    strategy_family_rank,
)


GROUPING_METHOD = "method"
GROUPING_SERIES = "series"
GROUPING_SOURCE = "source"

# Порядок кортежа — порядок пунктов в переключателе над списком.
STRATEGY_GROUPINGS: tuple[tuple[str, str], ...] = (
    (GROUPING_METHOD, "По способу обхода"),
    (GROUPING_SERIES, "По серии"),
    (GROUPING_SOURCE, "По источнику"),
)
_GROUPING_KEYS = frozenset(key for key, _title in STRATEGY_GROUPINGS)

NAME_DETAIL_SEPARATOR = " · "

SERIES_OTHER_KEY = "s_other"
SOURCE_NONE_KEY = "o_none"

# Серия из одной-двух стратегий своего заголовка не получает.
_MIN_SERIES_GROUP_SIZE = 3
_MIN_SOURCE_GROUP_SIZE = 2
_MIN_SUBGROUP_SIZE = 2
# В маленькой группе подзаголовки только мешают: её и так видно целиком.
_MIN_GROUP_SIZE_FOR_SUBGROUPS = 9

_SOURCE_PREFIX = "из "
_SOURCE_NUMBER = re.compile(r"\s*№\s*\d+$")
_NEUTRAL_COLOR = "#9aa6b2"


@dataclass(frozen=True)
class StrategyGroupInfo:
    """Заголовок группы: что на нём написано и нарисовано."""

    key: str
    title: str
    description: str
    icon_name: str
    color: str


@dataclass(frozen=True)
class StrategyPlacement:
    """Место стратегии в списке: группа и подзаголовок внутри неё."""

    group_key: str
    # Пусто — группа показывается без подзаголовков.
    subgroup_key: str = ""
    subgroup_title: str = ""


@dataclass(frozen=True)
class StrategyGroupingLayout:
    grouping: str
    placements: dict[str, StrategyPlacement]
    groups: dict[str, StrategyGroupInfo]
    group_rank: dict[str, int]
    subgroup_rank: dict[str, int]

    def placement(self, strategy_id: str) -> StrategyPlacement:
        return self.placements.get(str(strategy_id or ""), _NO_PLACEMENT)

    def group_key(self, strategy_id: str) -> str:
        return self.placement(strategy_id).group_key

    def group_info(self, group_key: str) -> StrategyGroupInfo:
        info = self.groups.get(str(group_key or ""))
        if info is not None:
            return info
        family = strategy_family(group_key)
        return StrategyGroupInfo(family.key, family.title, family.description, family.icon_name, family.color)

    def sort_key(self, strategy_id: str, entry, state) -> tuple[int, int, bool, str]:
        """Порядок строк: группа, подзаголовок, избранные первыми, затем по имени."""
        placement = self.placement(strategy_id)
        return (
            self.group_rank.get(placement.group_key, len(self.group_rank)),
            self.subgroup_rank.get(placement.subgroup_key, 0),
            not bool(getattr(state, "favorite", False)),
            str(getattr(entry, "name", "") or "").lower(),
        )


_NO_PLACEMENT = StrategyPlacement(group_key="other")


def is_method_group_key(group_key: str) -> bool:
    """Ключ группы по способу обхода, а не серии («s_…») или источника («o_…»)."""
    return not str(group_key or "").startswith(("s_", "o_"))


def normalize_strategy_grouping(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in _GROUPING_KEYS else GROUPING_METHOD


def split_strategy_name(name: str) -> tuple[str, str]:
    """Название стратегии и уточнение после « · » («General 1.9.9», «из Steam»)."""
    base, separator, detail = str(name or "").partition(NAME_DETAIL_SEPARATOR)
    if not separator or not base.strip():
        return str(name or "").strip(), ""
    return base.strip(), detail.strip()


def strategy_series(name: str) -> str:
    """Серия стратегии — первое слово названия («General», «Flowseal»)."""
    words = split_strategy_name(name)[0].split()
    return words[0].rstrip(":,") if words else ""


def strategy_source(name: str) -> str:
    """Откуда взята стратегия: «Steam» из уточнения «из Steam»; пусто — не указано."""
    for part in str(name or "").split(NAME_DETAIL_SEPARATOR)[1:]:
        part = part.strip()
        if not part.startswith(_SOURCE_PREFIX):
            continue
        source = _SOURCE_NUMBER.sub("", part[len(_SOURCE_PREFIX):]).strip()
        return source.strip("«»").strip()
    return ""


def strategy_detail_without_source(name: str) -> str:
    """Уточнение названия без слов «из …»: под заголовком источника они лишние."""
    parts = [part.strip() for part in str(name or "").split(NAME_DETAIL_SEPARATOR)[1:]]
    return NAME_DETAIL_SEPARATOR.join(part for part in parts if part and not part.startswith(_SOURCE_PREFIX))


def _hashed_key(prefix: str, title: str) -> str:
    # Ключ открытой группы хранится в настройках и обязан быть коротким
    # латинским словом (settings.normalize), поэтому название заменено отпечатком.
    digest = hashlib.sha1(str(title or "").strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _entry_name(strategy_id: str, entry) -> str:
    return str(getattr(entry, "name", "") or strategy_id)


def _rank_by_size(counts: dict[str, int], titles: dict[str, str], last_key: str) -> dict[str, int]:
    """Крупные группы первыми, одинаковые по размеру — по алфавиту, last_key в конце."""
    ordered = sorted(
        counts,
        key=lambda key: (key == last_key, -counts[key], str(titles.get(key, "")).lower()),
    )
    return {key: rank for rank, key in enumerate(ordered)}


def _series_groups(entries: dict) -> tuple[dict[str, str], dict[str, StrategyGroupInfo], dict[str, int]]:
    series_by_id = {strategy_id: strategy_series(_entry_name(strategy_id, entry)) for strategy_id, entry in entries.items()}
    sizes: dict[str, int] = {}
    for series in series_by_id.values():
        sizes[series.lower()] = sizes.get(series.lower(), 0) + 1
    group_by_id: dict[str, str] = {}
    titles: dict[str, str] = {}
    counts: dict[str, int] = {}
    for strategy_id, series in series_by_id.items():
        if series and sizes[series.lower()] >= _MIN_SERIES_GROUP_SIZE:
            key = _hashed_key("s", series)
            titles.setdefault(key, series)
        else:
            key = SERIES_OTHER_KEY
            titles[key] = "Остальные серии"
        group_by_id[strategy_id] = key
        counts[key] = counts.get(key, 0) + 1
    groups = {
        key: StrategyGroupInfo(
            key,
            title,
            "серии из одной-двух стратегий" if key == SERIES_OTHER_KEY else "",
            "fa5s.ellipsis-h" if key == SERIES_OTHER_KEY else "fa5s.tag",
            _NEUTRAL_COLOR,
        )
        for key, title in titles.items()
    }
    return group_by_id, groups, _rank_by_size(counts, titles, SERIES_OTHER_KEY)


def _source_groups(entries: dict) -> tuple[dict[str, str], dict[str, StrategyGroupInfo], dict[str, int]]:
    source_by_id = {strategy_id: strategy_source(_entry_name(strategy_id, entry)) for strategy_id, entry in entries.items()}
    sizes: dict[str, int] = {}
    for source in source_by_id.values():
        sizes[source.lower()] = sizes.get(source.lower(), 0) + 1
    group_by_id: dict[str, str] = {}
    titles: dict[str, str] = {}
    counts: dict[str, int] = {}
    for strategy_id, source in source_by_id.items():
        if source and sizes[source.lower()] >= _MIN_SOURCE_GROUP_SIZE:
            key = _hashed_key("o", source)
            titles.setdefault(key, source)
        else:
            key = SOURCE_NONE_KEY
            titles[key] = "Без источника"
        group_by_id[strategy_id] = key
        counts[key] = counts.get(key, 0) + 1
    groups = {
        key: StrategyGroupInfo(
            key,
            title,
            "в названии источник не указан" if key == SOURCE_NONE_KEY else "",
            "fa5s.ellipsis-h" if key == SOURCE_NONE_KEY else "fa5s.share-square",
            _NEUTRAL_COLOR,
        )
        for key, title in titles.items()
    }
    return group_by_id, groups, _rank_by_size(counts, titles, SOURCE_NONE_KEY)


def _method_groups(entries: dict) -> tuple[dict[str, str], dict[str, StrategyGroupInfo], dict[str, int]]:
    group_by_id = strategy_family_keys(entries)
    groups = {
        family.key: StrategyGroupInfo(family.key, family.title, family.description, family.icon_name, family.color)
        for family in STRATEGY_FAMILIES
    }
    return group_by_id, groups, {family.key: strategy_family_rank(family.key) for family in STRATEGY_FAMILIES}


def _subgroup_titles(grouping: str, entries: dict) -> dict[str, str]:
    """Под каким подзаголовком стратегия стоит внутри своей группы."""
    if grouping == GROUPING_SERIES:
        family_keys = strategy_family_keys(entries)
        return {strategy_id: strategy_family(family_keys[strategy_id]).title for strategy_id in entries}
    return {strategy_id: strategy_series(_entry_name(strategy_id, entry)) for strategy_id, entry in entries.items()}


def strategy_grouping_layout(entries, grouping: str = GROUPING_METHOD) -> StrategyGroupingLayout:
    """Раскладка всего каталога по группам и подзаголовкам.

    Считается по всему каталогу, а не по найденному поиском: иначе стратегия
    переезжала бы из группы в группу, пока пользователь печатает запрос.
    """
    entries = dict(entries or {})
    grouping = normalize_strategy_grouping(grouping)
    if grouping == GROUPING_SERIES:
        group_by_id, groups, group_rank = _series_groups(entries)
    elif grouping == GROUPING_SOURCE:
        group_by_id, groups, group_rank = _source_groups(entries)
    else:
        group_by_id, groups, group_rank = _method_groups(entries)

    raw_titles = _subgroup_titles(grouping, entries)
    members: dict[str, list[str]] = {}
    for strategy_id, group_key in group_by_id.items():
        members.setdefault(group_key, []).append(strategy_id)

    placements: dict[str, StrategyPlacement] = {}
    subgroup_rank: dict[str, int] = {}
    for group_key, strategy_ids in members.items():
        sizes: dict[str, int] = {}
        for strategy_id in strategy_ids:
            title = raw_titles.get(strategy_id, "")
            sizes[title.lower()] = sizes.get(title.lower(), 0) + 1
        other_key = f"{group_key}/"
        titles: dict[str, str] = {}
        counts: dict[str, int] = {}
        subgroup_by_id: dict[str, str] = {}
        for strategy_id in strategy_ids:
            title = raw_titles.get(strategy_id, "")
            if title and sizes[title.lower()] >= _MIN_SUBGROUP_SIZE:
                key = f"{group_key}/{title.lower()}"
                titles.setdefault(key, title)
            else:
                key = other_key
                titles[key] = "Остальные"
            subgroup_by_id[strategy_id] = key
            counts[key] = counts.get(key, 0) + 1
        # Одна группа на весь каталог показывается без заголовка, простым
        # списком по алфавиту (так выходит у Zapret 1): подзаголовки ему не нужны.
        if len(members) < 2 or len(strategy_ids) < _MIN_GROUP_SIZE_FOR_SUBGROUPS or len(counts) < 2:
            for strategy_id in strategy_ids:
                placements[strategy_id] = StrategyPlacement(group_key=group_key)
            continue
        subgroup_rank.update(_rank_by_size(counts, titles, other_key))
        for strategy_id in strategy_ids:
            key = subgroup_by_id[strategy_id]
            placements[strategy_id] = StrategyPlacement(group_key=group_key, subgroup_key=key, subgroup_title=titles[key])

    return StrategyGroupingLayout(
        grouping=grouping,
        placements=placements,
        groups=groups,
        group_rank=group_rank,
        subgroup_rank=subgroup_rank,
    )


__all__ = [
    "GROUPING_METHOD",
    "GROUPING_SERIES",
    "GROUPING_SOURCE",
    "NAME_DETAIL_SEPARATOR",
    "SERIES_OTHER_KEY",
    "SOURCE_NONE_KEY",
    "STRATEGY_GROUPINGS",
    "StrategyGroupInfo",
    "StrategyGroupingLayout",
    "StrategyPlacement",
    "is_method_group_key",
    "normalize_strategy_grouping",
    "split_strategy_name",
    "strategy_detail_without_source",
    "strategy_grouping_layout",
    "strategy_series",
    "strategy_source",
]
