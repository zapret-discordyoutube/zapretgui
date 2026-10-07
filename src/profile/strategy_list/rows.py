"""Какие строки списка видны прямо сейчас.

``visible_rows`` выводит плоский список строк из плана и из того, что человек
раскрыл. Свёрнутой группы и свёрнутых вариантов в результате просто нет —
у строки не бывает состояния «скрыта».
"""

from __future__ import annotations

from dataclasses import dataclass

from profile.strategy_list.plan import StrategyGroup, StrategyItem, StrategyListPlan, StrategySection

ROW_GROUP = "group"
ROW_SECTION = "section"
ROW_STRATEGY = "strategy"


@dataclass(frozen=True)
class VisibleRow:
    kind: str
    # Постоянное имя строки: по нему список понимает, что строка та же самая.
    key: str
    group: StrategyGroup | None = None
    section: StrategySection | None = None
    item: StrategyItem | None = None
    # У заголовка группы: раскрыта ли она.
    expanded: bool = True
    # У первой стратегии набора вариантов: сколько вариантов всего (0 — набора нет)
    # и раскрыт ли набор.
    twin_count: int = 0
    twin_open: bool = False

    @property
    def strategy_id(self) -> str:
        return self.item.strategy_id if self.item is not None else ""

    @property
    def group_key(self) -> str:
        return self.group.key if self.group is not None else ""

    @property
    def shown(self) -> tuple:
        """Всё, что строка показывает на экране.

        По этому список решает, перерисовывать ли строку: стратегия не должна
        перерисовываться оттого, что в её группе изменилась соседняя.
        """
        if self.kind == ROW_STRATEGY:
            return (self.item, self.twin_count, self.twin_open)
        if self.kind == ROW_SECTION:
            return (self.section.title, len(self.section.items))
        group = self.group
        return (group.title, group.description, group.icon_name, group.color, group.count, group.current_name, self.expanded)

    @property
    def selectable(self) -> bool:
        """Клавиатура останавливается на строке (подзаголовок — только подпись)."""
        return self.kind != ROW_SECTION


def _strategy_rows(group: StrategyGroup, section: StrategySection, open_twins) -> list[VisibleRow]:
    rows: list[VisibleRow] = []
    index = 0
    items = section.items
    while index < len(items):
        item = items[index]
        if not item.twin_key:
            rows.append(VisibleRow(ROW_STRATEGY, f"i:{item.strategy_id}", group, section, item))
            index += 1
            continue
        end = index
        while end < len(items) and items[end].twin_key == item.twin_key:
            end += 1
        twins = items[index:end]
        is_open = item.twin_key in open_twins
        if is_open:
            shown = twins
        else:
            # Свёрнутый набор показывает выбранную стратегию, а если её в нём
            # нет — лучшую по порядку «что пробовать первым».
            shown = (next((twin for twin in twins if twin.is_current), twins[0]),)
        for position, twin in enumerate(shown):
            rows.append(
                VisibleRow(
                    ROW_STRATEGY,
                    f"i:{twin.strategy_id}",
                    group,
                    section,
                    twin,
                    twin_count=len(twins) if position == 0 else 0,
                    twin_open=is_open,
                )
            )
        index = end
    return rows


def visible_rows(plan: StrategyListPlan, *, open_groups=None, open_twins=()) -> tuple[VisibleRow, ...]:
    """Строки списка сверху вниз.

    open_groups — ключи раскрытых групп; None — раскрыты все. Одна группа на
    весь список показывается без заголовка. Когда список сужен поиском или
    отбором, раскрыто всё: найденное нельзя спрятать в свёрнутой группе.
    """
    open_twins = set(open_twins or ())
    groups = plan.groups
    with_headers = len(groups) > 1
    rows: list[VisibleRow] = []
    for group in groups:
        expanded = True
        if with_headers:
            expanded = plan.narrowed or open_groups is None or group.key in open_groups
            rows.append(VisibleRow(ROW_GROUP, f"g:{group.key}", group, expanded=expanded))
        if not expanded:
            continue
        for section in group.sections:
            if section.key:
                rows.append(VisibleRow(ROW_SECTION, f"s:{section.key}", group, section))
            rows.extend(_strategy_rows(group, section, open_twins))
    return tuple(rows)


def default_open_group(plan: StrategyListPlan, remembered: str | None) -> str:
    """Какая группа длинного списка раскрыта, пока человек не выбрал сам.

    Та, которую он оставил открытой у этого профиля (пустая строка — всё
    свёрнуто). Если ещё ничего не открывал — группа выбранной стратегии, а
    когда стратегия не выбрана — первая группа (советуемые, если они есть).
    """
    keys = [group.key for group in plan.groups]
    if remembered is not None:
        if not remembered or remembered in keys:
            return remembered
    current_group = plan.group_of(plan.current_strategy_id)
    if current_group:
        return current_group
    return keys[0] if keys else ""
