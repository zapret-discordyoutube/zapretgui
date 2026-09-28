"""В каком порядке проверять стратегии.

1. Сначала те, что уже подтвердились у этого пользователя на этой цели.
2. Дальше — «разнообразием»: стратегии собраны в группы по приёмам
   (fake, multisplit, syndata, …), и группы идут по кругу. Так первые 30
   проверок покрывают разные приёмы, а не 30 вариаций одного.
3. В конец — те, что недавно не сработали на этой цели. Поэтому повторный
   «Быстрый» подбор сам берёт ещё не проверенные стратегии.

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
        *interleave_by_technique(fresh),
        *interleave_by_technique([candidate for _time, candidate in recently_failed]),
    ]


def batch_for_mode(ordered: Sequence[Candidate], mode: str) -> list[Candidate]:
    size = MODE_BATCH_SIZES.get(str(mode or "").strip().lower())
    return list(ordered) if size is None else list(ordered[:size])
