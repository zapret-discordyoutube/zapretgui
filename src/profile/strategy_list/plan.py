"""Как разложить стратегии каталога для одного профиля.

``build_plan`` — чистая функция: на входе сведения о стратегиях, оценки
человека, частота в готовых пресетах, поиск и отбор; на выходе дерево
«группа → подзаголовок → стратегия» в том порядке, в каком его читает человек.

Порядок внутри группы — «что пробовать первым»: отмеченные «работает»,
избранные, затем по частоте в готовых пресетах (сначала на этом же сервисе,
потом — на скольких сервисах стратегия встречается), «не работает» в конце.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re

from profile.strategy_families import STRATEGY_FAMILIES, strategy_family

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

FILTER_ALL = "all"
FILTER_RECOMMENDED = "recommended"
FILTER_WORKS = "works"
FILTER_UNTRIED = "untried"
FILTER_FAVORITE = "favorite"
# Быстрые отборы над списком; порядок кортежа — порядок кнопок.
QUICK_FILTERS: tuple[tuple[str, str], ...] = (
    (FILTER_ALL, "Все"),
    (FILTER_RECOMMENDED, "Советуемые"),
    (FILTER_WORKS, "Работают у меня"),
    (FILTER_UNTRIED, "Не пробовал"),
    (FILTER_FAVORITE, "Избранное"),
)
_FILTER_KEYS = frozenset(key for key, _title in QUICK_FILTERS)

BADGE_RECOMMENDED = "recommended"
BADGE_NEUTRAL = "neutral"
BADGE_WARNING = "warning"

# Группа над всеми остальными: стратегии, которые в готовых пресетах стоят на
# этом же сервисе (profile.strategy_usage). Есть при любой группировке.
RECOMMENDED_GROUP = "recommended"
_SERIES_OTHER = "s_other"
_SOURCE_NONE = "o_none"
_NEUTRAL_COLOR = "#9aa6b2"

# Серия из одной-двух стратегий своего заголовка не получает.
_MIN_SERIES_GROUP = 3
_MIN_SOURCE_GROUP = 2
_MIN_SECTION = 2
# В маленькой группе подзаголовки только мешают: её и так видно целиком.
_MIN_GROUP_FOR_SECTIONS = 9
_SOURCE_NUMBER = re.compile(r"\s*№\s*\d+$")
_RATING_RANK = {"work": 0, "": 1, "notwork": 2}
_FAMILY_RANK = {family.key: rank for rank, family in enumerate(STRATEGY_FAMILIES)}
# «Ничего не делать» — не способ обхода: в перебор не входит.
_NOT_FOR_QUEUE = frozenset({"pass"})


def normalize_strategy_grouping(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in _GROUPING_KEYS else GROUPING_METHOD


def normalize_quick_filter(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in _FILTER_KEYS else FILTER_ALL


@dataclass(frozen=True)
class PlanRequest:
    """Всё, от чего зависит раскладка списка."""

    facts: dict
    states: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    current_strategy_id: str = "none"
    query: str = ""
    quick_filter: str = FILTER_ALL
    grouping: str = GROUPING_METHOD


@dataclass(frozen=True)
class StrategyItem:
    strategy_id: str
    name: str
    title: str
    detail: str
    plain_label: str
    badge_text: str
    badge_tone: str
    payload_badge: str
    rating: str
    favorite: bool
    is_current: bool
    tooltip: str
    accessible_text: str
    # Способ обхода для значка плитки: ключ группы способа и её цвет.
    family_key: str = ""
    family_color: str = ""
    # Стратегии с одним названием («General ALT11 1.9.9» из пяти источников)
    # складываются в одну строку с числом вариантов; пусто — вариантов нет.
    twin_key: str = ""


@dataclass(frozen=True)
class StrategySection:
    """Подзаголовок внутри группы; пустой ключ — группа без подзаголовков."""

    key: str
    title: str
    items: tuple[StrategyItem, ...]


@dataclass(frozen=True)
class StrategyGroup:
    key: str
    title: str
    description: str
    icon_name: str
    color: str
    sections: tuple[StrategySection, ...]

    @property
    def count(self) -> int:
        return sum(len(section.items) for section in self.sections)

    @property
    def items(self) -> tuple[StrategyItem, ...]:
        return tuple(item for section in self.sections for item in section.items)

    @property
    def current_name(self) -> str:
        """Название выбранной стратегии, если она в этой группе."""
        for item in self.items:
            if item.is_current:
                return item.name
        return ""


@dataclass(frozen=True)
class StrategyListPlan:
    groups: tuple[StrategyGroup, ...] = ()
    total_count: int = 0
    visible_count: int = 0
    current_strategy_id: str = "none"
    grouping: str = GROUPING_METHOD
    # Список сужен поиском или отбором: найденное показывается целиком,
    # без сворачивания групп и вариантов.
    narrowed: bool = False
    # Очередь «что пробовать» — весь каталог: сначала советуемые для сервиса
    # по убыванию частоты, потом остальные. Если не помогло ни одно из
    # советуемых, перебор на этом не кончается.
    queue: tuple[str, ...] = ()
    # Сколько первых стратегий очереди — советуемые.
    recommended_count: int = 0

    def group_of(self, strategy_id: str) -> str:
        for group in self.groups:
            for item in group.items:
                if item.strategy_id == strategy_id:
                    return group.key
        return ""

    def item(self, strategy_id: str) -> StrategyItem | None:
        for group in self.groups:
            for item in group.items:
                if item.strategy_id == strategy_id:
                    return item
        return None


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        return few
    return many


def strategy_badge(usage, label: str) -> tuple[str, str]:
    """Метка справа на плитке: (текст, тон). Пусто — метки нет.

    Главное знание о стратегии — где она стоит в готовых пресетах. Пометка
    каталога «осторожно» важнее частоты на чужих сервисах, но не важнее того,
    что стратегию ставили на этот же сервис.
    """
    same = int(getattr(usage, "same_service", 0) or 0)
    services = int(getattr(usage, "services", 0) or 0)
    if same > 0:
        return f"в {same} {_plural(same, 'пресете', 'пресетах', 'пресетах')}", BADGE_RECOMMENDED
    if label == "caution":
        return "осторожно", BADGE_WARNING
    if services >= 2:
        return f"на {services} {_plural(services, 'сервисе', 'сервисах', 'сервисах')}", BADGE_NEUTRAL
    if label == "experimental":
        return "опытная", BADGE_NEUTRAL
    return "", ""


def usage_sentence(usage) -> str:
    """Фраза для подсказки: где стратегия стоит в готовых пресетах."""
    same = int(getattr(usage, "same_service", 0) or 0)
    services = int(getattr(usage, "services", 0) or 0)
    parts: list[str] = []
    if same > 0:
        word = _plural(same, "готовом пресете", "готовых пресетах", "готовых пресетах")
        parts.append(f"Стоит на этом сервисе в {same} {word}.")
    others = services - (1 if same > 0 else 0)
    if others > 0:
        word = _plural(others, "сервисе", "сервисах", "сервисах")
        parts.append(f"В готовых пресетах встречается ещё на {others} {word}.")
    return " ".join(parts)


def _priority(facts, state, usage) -> tuple:
    return (
        _RATING_RANK.get(str(getattr(state, "rating", "") or ""), 1),
        not bool(getattr(state, "favorite", False)),
        -int(getattr(usage, "same_service", 0) or 0),
        -int(getattr(usage, "services", 0) or 0),
        facts.name.lower(),
    )


def _matches_filter(quick_filter: str, state, usage) -> bool:
    if quick_filter == FILTER_RECOMMENDED:
        return bool(getattr(usage, "recommended", False))
    rating = str(getattr(state, "rating", "") or "")
    if quick_filter == FILTER_WORKS:
        return rating == "work"
    if quick_filter == FILTER_UNTRIED:
        return not rating
    if quick_filter == FILTER_FAVORITE:
        return bool(getattr(state, "favorite", False))
    return True


def _hashed_key(prefix: str, title: str) -> str:
    # Ключ открытой группы хранится в настройках и обязан быть коротким
    # латинским словом (settings.normalize), поэтому название заменено отпечатком.
    digest = hashlib.sha1(str(title or "").strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


@dataclass(frozen=True)
class _GroupInfo:
    key: str
    title: str
    description: str
    icon_name: str
    color: str
    rank: tuple


def _named_groups(facts: dict, value_of, *, prefix: str, min_size: int, other_key: str, other_title: str,
                  other_description: str, icon_name: str) -> tuple[dict[str, str], dict[str, _GroupInfo]]:
    """Группы по значению из названия (серия, источник): крупные первыми."""
    values = {strategy_id: value_of(item) for strategy_id, item in facts.items()}
    sizes: dict[str, int] = {}
    for value in values.values():
        sizes[value.lower()] = sizes.get(value.lower(), 0) + 1
    group_of: dict[str, str] = {}
    titles: dict[str, str] = {}
    counts: dict[str, int] = {}
    for strategy_id, value in values.items():
        if value and sizes[value.lower()] >= min_size:
            key = _hashed_key(prefix, value)
            titles.setdefault(key, value)
        else:
            key = other_key
            titles[key] = other_title
        group_of[strategy_id] = key
        counts[key] = counts.get(key, 0) + 1
    infos = {
        key: _GroupInfo(
            key,
            title,
            other_description if key == other_key else "",
            "fa5s.ellipsis-h" if key == other_key else icon_name,
            _NEUTRAL_COLOR,
            (key == other_key, -counts[key], title.lower()),
        )
        for key, title in titles.items()
    }
    return group_of, infos


def _source_title(facts) -> str:
    return _SOURCE_NUMBER.sub("", facts.source)


def _groups_for(facts: dict, grouping: str) -> tuple[dict[str, str], dict[str, _GroupInfo]]:
    if grouping == GROUPING_SERIES:
        return _named_groups(
            facts, lambda item: item.series, prefix="s", min_size=_MIN_SERIES_GROUP, other_key=_SERIES_OTHER,
            other_title="Остальные серии", other_description="серии из одной-двух стратегий", icon_name="fa5s.tag",
        )
    if grouping == GROUPING_SOURCE:
        return _named_groups(
            facts, _source_title, prefix="o", min_size=_MIN_SOURCE_GROUP, other_key=_SOURCE_NONE,
            other_title="Без источника", other_description="в названии источник не указан",
            icon_name="fa5s.share-square",
        )
    infos = {
        family.key: _GroupInfo(
            family.key, family.title, f"способ обхода: {family.description}", family.icon_name, family.color, (rank,)
        )
        for rank, family in enumerate(STRATEGY_FAMILIES)
    }
    return {strategy_id: item.family_key for strategy_id, item in facts.items()}, infos


_RECOMMENDED_INFO = _GroupInfo(
    RECOMMENDED_GROUP,
    "Советуем для этого сервиса",
    "стоят на нём в готовых пресетах: чем чаще, тем выше",
    "fa5s.thumbs-up",
    "#e5b454",
    (),
)


def _section_title(facts, grouping: str) -> str:
    """Под каким подзаголовком стратегия стоит внутри своей группы."""
    if grouping == GROUPING_SERIES:
        return strategy_family(facts.family_key).title
    return facts.series


def _tooltip(facts, usage) -> str:
    parts = [
        facts.description,
        facts.technique_description,
        usage_sentence(usage),
        f"Автор: {facts.author}" if facts.author else "",
        f"Раньше называлась: {facts.old_name}" if facts.old_name else "",
        facts.args,
    ]
    return "\n\n".join(part for part in parts if part)


def _status_words(state, *, is_current: bool) -> list[str]:
    words = ["Выбрана" if is_current else "Не выбрана"]
    if bool(getattr(state, "favorite", False)):
        words.append("В избранном")
    rating = str(getattr(state, "rating", "") or "")
    if rating == "work":
        words.append("Работает")
    elif rating == "notwork":
        words.append("Не работает")
    return words


def _accessible_text(facts, state, *, is_current: bool, badge_text: str) -> str:
    parts = [facts.name, *(word.lower() for word in _status_words(state, is_current=is_current))]
    if facts.plain_label:
        parts.append(f"способ: {facts.plain_label}")
    if badge_text:
        parts.append(badge_text)
    if facts.payload_badge_accessible:
        parts.append(facts.payload_badge_accessible)
    return ", ".join(parts)


def _make_item(facts, state, usage, *, is_current: bool, detail: str) -> StrategyItem:
    badge_text, badge_tone = strategy_badge(usage, facts.label)
    return StrategyItem(
        strategy_id=facts.strategy_id,
        name=facts.name,
        title=facts.title,
        detail=detail,
        plain_label=facts.plain_label,
        badge_text=badge_text,
        badge_tone=badge_tone,
        payload_badge=facts.payload_badge,
        rating=str(getattr(state, "rating", "") or ""),
        favorite=bool(getattr(state, "favorite", False)),
        is_current=is_current,
        tooltip=_tooltip(facts, usage),
        accessible_text=_accessible_text(facts, state, is_current=is_current, badge_text=badge_text),
        family_key=facts.family_key,
        family_color=strategy_family(facts.family_key).color,
    )


def _with_twins(group_key: str, section_key: str, items: list[StrategyItem]) -> tuple[StrategyItem, ...]:
    """Ставит стратегии с одним названием рядом и помечает их общим ключом.

    Набор занимает место своей лучшей стратегии, внутри набора порядок прежний.
    """
    by_title: dict[str, list[StrategyItem]] = {}
    for item in items:
        by_title.setdefault(item.title.lower(), []).append(item)
    ordered: list[StrategyItem] = []
    placed: set[str] = set()
    for item in items:
        title = item.title.lower()
        if title in placed:
            continue
        placed.add(title)
        twins = by_title[title]
        if len(twins) < 2:
            ordered.append(item)
            continue
        twin_key = f"{group_key}/{section_key}/{title}"
        ordered.extend(
            StrategyItem(**{**twin.__dict__, "twin_key": twin_key}) for twin in twins
        )
    return tuple(ordered)


def recommended_queue(facts: dict, usage: dict) -> tuple[str, ...]:
    """Очередь «что пробовать»: советуемые для сервиса, самые частые первыми."""
    recommended = [
        strategy_id for strategy_id in facts if getattr(usage.get(strategy_id), "recommended", False)
    ]
    recommended.sort(
        key=lambda strategy_id: (
            -int(usage[strategy_id].same_service),
            -int(usage[strategy_id].services),
            facts[strategy_id].name.lower(),
        )
    )
    return tuple(recommended)


def full_queue(facts: dict, usage: dict, recommended: tuple[str, ...]) -> tuple[str, ...]:
    """Очередь перебора по всему каталогу.

    После советуемых идут стратегии, которые в готовых пресетах стоят хоть
    где-то (чем на большем числе сервисов, тем раньше), затем остальные.
    Остальные чередуются по способам обхода: если не помогла «подделка»,
    следующей пробуется «нарезка», а не ещё одна похожая «подделка».
    """
    taken = set(recommended)
    rest = [strategy_id for strategy_id in facts if strategy_id not in taken and strategy_id not in _NOT_FOR_QUEUE]
    used = [strategy_id for strategy_id in rest if int(getattr(usage.get(strategy_id), "services", 0) or 0) > 0]
    used.sort(key=lambda strategy_id: (-int(usage[strategy_id].services), facts[strategy_id].name.lower()))
    used_set = set(used)
    by_family: dict[str, list[str]] = {}
    for strategy_id in sorted((item for item in rest if item not in used_set), key=lambda item: facts[item].name.lower()):
        by_family.setdefault(facts[strategy_id].family_key, []).append(strategy_id)
    queues = [by_family[key] for key in sorted(by_family, key=lambda key: _FAMILY_RANK.get(key, len(_FAMILY_RANK)))]
    mixed: list[str] = []
    while queues:
        queues = [queue for queue in queues if queue]
        for queue in queues:
            mixed.append(queue.pop(0))
    return (*recommended, *used, *mixed)


def build_plan(request: PlanRequest) -> StrategyListPlan:
    facts = dict(request.facts or {})
    states = dict(request.states or {})
    usage = dict(request.usage or {})
    grouping = normalize_strategy_grouping(request.grouping)
    quick_filter = normalize_quick_filter(request.quick_filter)
    query = str(request.query or "").strip().lower()
    current_id = str(request.current_strategy_id or "none").strip() or "none"
    narrowed = bool(query) or quick_filter != FILTER_ALL

    # Раскладка считается по всему каталогу, а не по найденному: иначе
    # стратегия переезжала бы из группы в группу, пока человек печатает запрос.
    group_of, infos = _groups_for(facts, grouping)
    recommended = recommended_queue(facts, usage)
    # Советовать имеет смысл только часть каталога.
    if recommended and len(recommended) < len(facts):
        for strategy_id in recommended:
            group_of[strategy_id] = RECOMMENDED_GROUP
        infos = {**infos, RECOMMENDED_GROUP: _RECOMMENDED_INFO}
    else:
        recommended = ()
    queue = full_queue(facts, usage, recommended)

    members: dict[str, list[str]] = {}
    for strategy_id, group_key in group_of.items():
        members.setdefault(group_key, []).append(strategy_id)

    groups: list[StrategyGroup] = []
    visible_count = 0
    for group_key in sorted(members, key=lambda key: (key != RECOMMENDED_GROUP, infos[key].rank)):
        info = infos[group_key]
        ids = members[group_key]
        # Подзаголовки считаются по полной группе: поиск их не перетасовывает.
        titles = {strategy_id: _section_title(facts[strategy_id], grouping) for strategy_id in ids}
        sizes: dict[str, int] = {}
        for title in titles.values():
            sizes[title.lower()] = sizes.get(title.lower(), 0) + 1
        section_of: dict[str, tuple[str, str]] = {}
        section_counts: dict[str, int] = {}
        for strategy_id in ids:
            title = titles[strategy_id]
            if title and sizes[title.lower()] >= _MIN_SECTION:
                section = (f"{group_key}/{title.lower()}", title)
            else:
                section = (f"{group_key}/", "Остальные")
            section_of[strategy_id] = section
            section_counts[section[0]] = section_counts.get(section[0], 0) + 1
        sectioned = (
            not narrowed
            # Советуемые стоят по частоте, подзаголовки порвали бы этот порядок.
            and group_key != RECOMMENDED_GROUP
            and len(members) > 1
            and len(ids) >= _MIN_GROUP_FOR_SECTIONS
            and len(section_counts) > 1
        )

        shown = [
            strategy_id
            for strategy_id in ids
            if (not query or query in facts[strategy_id].search_text)
            and _matches_filter(quick_filter, states.get(strategy_id), usage.get(strategy_id))
        ]
        if not shown:
            continue
        shown.sort(key=lambda strategy_id: _priority(facts[strategy_id], states.get(strategy_id), usage.get(strategy_id)))
        visible_count += len(shown)

        by_section: dict[str, list[StrategyItem]] = {}
        section_titles: dict[str, str] = {}
        for strategy_id in shown:
            item_facts = facts[strategy_id]
            detail = item_facts.detail
            if grouping == GROUPING_SOURCE and group_key not in (_SOURCE_NONE, RECOMMENDED_GROUP):
                # Источник уже написан в заголовке группы.
                detail = NAME_SEPARATOR.join(
                    part for part in detail.split(NAME_SEPARATOR) if part and not part.startswith("из ")
                )
            item = _make_item(
                item_facts,
                states.get(strategy_id),
                usage.get(strategy_id),
                is_current=strategy_id == current_id,
                detail=detail,
            )
            key, title = section_of[strategy_id] if sectioned else ("", "")
            by_section.setdefault(key, []).append(item)
            section_titles[key] = title

        section_order = sorted(
            by_section,
            key=lambda key: (key == f"{group_key}/", -section_counts.get(key, 0), section_titles[key].lower()),
        )
        groups.append(
            StrategyGroup(
                key=info.key,
                title=info.title,
                description=info.description,
                icon_name=info.icon_name,
                color=info.color,
                sections=tuple(
                    StrategySection(
                        key=key,
                        title=section_titles[key],
                        items=tuple(by_section[key]) if narrowed else _with_twins(group_key, key, by_section[key]),
                    )
                    for key in section_order
                ),
            )
        )

    return StrategyListPlan(
        groups=tuple(groups),
        total_count=len(facts),
        visible_count=visible_count,
        current_strategy_id=current_id,
        grouping=grouping,
        narrowed=narrowed,
        queue=queue,
        recommended_count=len(recommended),
    )


NAME_SEPARATOR = " · "


def next_to_try(queue, states, current_strategy_id: str) -> str:
    """Следующая стратегия очереди, которую человек ещё не оценил; пусто — очередь пройдена."""
    states = dict(states or {})
    current = str(current_strategy_id or "")
    for strategy_id in tuple(queue or ()):
        if strategy_id == current:
            continue
        if not str(getattr(states.get(strategy_id), "rating", "") or ""):
            return strategy_id
    return ""


def try_progress(queue, states) -> tuple[int, int]:
    """Сколько стратегий очереди человек уже оценил: (оценено, всего)."""
    states = dict(states or {})
    queue = tuple(queue or ())
    tried = sum(1 for strategy_id in queue if str(getattr(states.get(strategy_id), "rating", "") or ""))
    return tried, len(queue)


def try_stage(plan: StrategyListPlan, states) -> tuple[str, int, int]:
    """Где сейчас перебор: (этап, оценено на этапе, всего на этапе).

    Этап «recommended», пока среди советуемых есть неоценённые; потом «all».
    """
    recommended = plan.queue[: plan.recommended_count]
    tried, total = try_progress(recommended, states)
    if total and tried < total:
        return "recommended", tried, total
    tried, total = try_progress(plan.queue, states)
    return "all", tried, total
