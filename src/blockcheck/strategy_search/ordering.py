"""В каком порядке проверять стратегии.

1. Сначала те, что уже подтвердились у этого пользователя на этой цели.
2. Дальше — «разнообразием»: стратегии собраны в группы по приёмам
   (fake, multisplit, syndata, …), и группы идут по кругу. Так первые 30
   проверок покрывают разные приёмы, а не 30 вариаций одного.
3. В конец — те, что недавно не сработали на этой цели. Поэтому повторный
   «Быстрый» подбор сам берёт ещё не проверенные стратегии.

Если BlockCheck недавно выяснил, как ведёт себя фильтр (``habit`` — код из
``diagnostics.filter_habits``), внутри шагов 2 и 3 порядок уточняется: вперёд
идут приёмы, у которых на таком фильтре есть шанс, в конец — заведомо слабые.
Ничего не выбрасывается: подсказка меняет только очерёдность.

Стратегия «ничего не делать» (``pass``) в кандидаты не входит: подбор
использует её как контроль.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

_DESYNC_FUNCTION = re.compile(r"^--lua-desync=([A-Za-z0-9_]+)")
PASS_FUNCTION = "pass"
# Сколько помнить «не сработала»: сеть и DPI провайдера со временем меняются.
FAILED_MEMORY_SECONDS = 14 * 24 * 3600

MODE_BATCH_SIZES = {"quick": 30, "standard": 80}


@dataclass(frozen=True, slots=True)
class Candidate:
    strategy_id: str
    name: str
    args: str


# Простое дробление пакета: куски идут подряд и ничем не прикрыты.
_PLAIN_SPLIT = frozenset({"multisplit", "multisplit_tls", "tls_multisplit_sni", "tls_split_gentle", "slowsplit"})
_DISORDER = frozenset({"multidisorder", "multidisorder_legacy", "fakeddisorder", "tls_disorder_gentle"})
_FAKES = frozenset({"fake", "fakedsplit", "fakeddisorder", "hostfakesplit", "hostfakesplit_multi", "syndata"})
_RECORDS = frozenset({"tlsrec"})
# Привычка фильтра → (каким приёмам дать дорогу, считать ли простое дробление слабым, пояснение для журнала).
_HABIT_PLANS: dict[str, tuple[frozenset[str], bool, str]] = {
    "tcp": (_PLAIN_SPLIT | _DISORDER, False, "фильтр не склеивает пакеты — сначала стратегии с дроблением"),
    "timer": (
        _DISORDER | _FAKES,
        True,
        "фильтр склеивает куски, пришедшие подряд, — сначала смена порядка кусков и подделки, простое дробление в конце",
    ),
    "records": (
        _RECORDS | _FAKES,
        True,
        "фильтр склеивает пакеты, но не записи TLS — сначала дробление записи и подделки, простое дробление в конце",
    ),
    "none": (
        _FAKES,
        True,
        "фильтр собирает приветствие целиком — сначала подделки и наложение кусков, простое дробление в конце",
    ),
}


def habit_note(habit: str) -> str:
    """Пояснение для журнала подбора: как учтена проверка «Как работает фильтр». Пусто — подсказки нет."""
    plan = _HABIT_PLANS.get(str(habit or ""))
    return plan[2] if plan else ""


def habit_rank(candidate: Candidate, habit: str) -> int:
    """0 — приём с шансом на таком фильтре, 1 — обычный, 2 — заведомо слабый."""
    plan = _HABIT_PLANS.get(str(habit or ""))
    if plan is None:
        return 1
    prefer, plain_is_weak, _note = plan
    functions = set(technique_key(candidate))
    # Наложение кусков (seqovl) — отдельный приём, даже когда функция называется «дробление».
    overlap = "seqovl=" in candidate.args
    if plain_is_weak and functions and functions <= _PLAIN_SPLIT and not overlap:
        return 2
    if functions & prefer or (plain_is_weak and overlap):
        return 0
    return 1


def _by_habit(candidates: Sequence[Candidate], habit: str) -> list[Candidate]:
    """Группы по шансам, внутри каждой — приёмы по кругу."""
    ordered: list[Candidate] = []
    for rank in (0, 1, 2):
        ordered += interleave_by_technique([item for item in candidates if habit_rank(item, habit) == rank])
    return ordered


def desync_functions(args: str) -> tuple[str, ...]:
    names: list[str] = []
    for line in str(args or "").splitlines():
        match = _DESYNC_FUNCTION.match(line.strip())
        if match:
            names.append(match.group(1).lower())
    return tuple(names)


def is_pass_strategy(candidate: Candidate) -> bool:
    functions = set(desync_functions(candidate.args))
    return not functions or functions == {PASS_FUNCTION}


def technique_key(candidate: Candidate) -> tuple[str, ...]:
    """Группа приёма: набор функций обхода без повторов и порядка."""
    return tuple(sorted(set(desync_functions(candidate.args)) - {PASS_FUNCTION}))


def interleave_by_technique(candidates: Sequence[Candidate]) -> list[Candidate]:
    groups: OrderedDict[tuple[str, ...], list[Candidate]] = OrderedDict()
    for candidate in candidates:
        groups.setdefault(technique_key(candidate), []).append(candidate)
    ordered: list[Candidate] = []
    queues = [list(group) for group in groups.values()]
    while queues:
        next_round: list[list[Candidate]] = []
        for queue in queues:
            ordered.append(queue.pop(0))
            if queue:
                next_round.append(queue)
        queues = next_round
    return ordered


def order_candidates(
    candidates: Iterable[Candidate],
    *,
    confirmed_ids: Sequence[str] = (),
    failed_at: Mapping[str, float] | None = None,
    now: float = 0.0,
    habit: str = "",
) -> list[Candidate]:
    pool = [candidate for candidate in candidates if not is_pass_strategy(candidate)]
    by_id = {candidate.strategy_id: candidate for candidate in pool}
    first = [by_id[strategy_id] for strategy_id in dict.fromkeys(confirmed_ids) if strategy_id in by_id]
    taken = {candidate.strategy_id for candidate in first}

    failed_at = failed_at or {}
    fresh: list[Candidate] = []
    recently_failed: list[tuple[float, Candidate]] = []
    for candidate in pool:
        if candidate.strategy_id in taken:
            continue
        failed_time = float(failed_at.get(candidate.strategy_id, 0.0) or 0.0)
        if failed_time and now - failed_time < FAILED_MEMORY_SECONDS:
            recently_failed.append((failed_time, candidate))
        else:
            fresh.append(candidate)
    # Давно проверенные раньше, чем проверенные только что.
    recently_failed.sort(key=lambda item: item[0])
    return [
        *first,
        *_by_habit(fresh, habit),
        *_by_habit([candidate for _time, candidate in recently_failed], habit),
    ]


def batch_for_mode(ordered: Sequence[Candidate], mode: str) -> list[Candidate]:
    size = MODE_BATCH_SIZES.get(str(mode or "").strip().lower())
    return list(ordered) if size is None else list(ordered[:size])
