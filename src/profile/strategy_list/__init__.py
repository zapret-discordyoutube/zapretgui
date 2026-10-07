"""Список готовых стратегий профиля: данные без окон.

От каталога до экрана список проходит три шага, и каждый — чистая функция:

1. ``facts`` — что известно о каждой стратегии каталога (название, способ
   обхода словами, серия, источник, пометка каталога). Считается один раз на
   каталог.
2. ``plan`` — как разложить стратегии для конкретного профиля: группы,
   подзаголовки, порядок «что пробовать первым», метки, поиск и отборы.
3. ``rows`` — какие строки видны прямо сейчас: зависит от плана и от того,
   какие группы и наборы вариантов человек раскрыл.

Окно (``profile.ui.strategy_list``) только показывает результат третьего шага.
Скрытых строк не бывает: свёрнутой группы в списке строк просто нет.
"""

from profile.strategy_list.facts import StrategyFacts, build_strategy_facts
from profile.strategy_list.plan import (
    BADGE_NEUTRAL,
    BADGE_RECOMMENDED,
    BADGE_WARNING,
    FILTER_ALL,
    FILTER_FAVORITE,
    FILTER_RECOMMENDED,
    FILTER_UNTRIED,
    FILTER_WORKS,
    GROUPING_METHOD,
    GROUPING_SERIES,
    GROUPING_SOURCE,
    QUICK_FILTERS,
    RECOMMENDED_GROUP,
    STRATEGY_GROUPINGS,
    PlanRequest,
    StrategyGroup,
    StrategyItem,
    StrategyListPlan,
    StrategySection,
    build_plan,
    next_to_try,
    normalize_quick_filter,
    normalize_strategy_grouping,
    try_progress,
    try_stage,
)
from profile.strategy_list.rows import (
    ROW_GROUP,
    ROW_SECTION,
    ROW_STRATEGY,
    VisibleRow,
    default_open_group,
    visible_rows,
)

__all__ = [
    "BADGE_NEUTRAL",
    "BADGE_RECOMMENDED",
    "BADGE_WARNING",
    "FILTER_ALL",
    "FILTER_FAVORITE",
    "FILTER_RECOMMENDED",
    "FILTER_UNTRIED",
    "FILTER_WORKS",
    "GROUPING_METHOD",
    "GROUPING_SERIES",
    "GROUPING_SOURCE",
    "QUICK_FILTERS",
    "RECOMMENDED_GROUP",
    "ROW_GROUP",
    "ROW_SECTION",
    "ROW_STRATEGY",
    "STRATEGY_GROUPINGS",
    "PlanRequest",
    "StrategyFacts",
    "StrategyGroup",
    "StrategyItem",
    "StrategyListPlan",
    "StrategySection",
    "VisibleRow",
    "build_plan",
    "build_strategy_facts",
    "default_open_group",
    "next_to_try",
    "normalize_quick_filter",
    "normalize_strategy_grouping",
    "try_progress",
    "try_stage",
    "visible_rows",
]
