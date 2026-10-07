"""Список готовых стратегий на странице профиля.

Виджет хранит только то, что выбрал человек (поиск, отбор, группировка,
раскрытые группы и варианты), и то, что прислала страница (каталог, оценки,
частота в готовых пресетах, выбранная стратегия). Всё, что на экране,
каждый раз выводится из этого заново одной функцией ``_refresh``:

    состояние → build_plan → visible_rows → модель → список

Других путей обновления нет, поэтому экран не может разойтись с состоянием.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from profile.strategy_list import (
    FILTER_ALL,
    GROUPING_METHOD,
    ROW_STRATEGY,
    PlanRequest,
    StrategyListPlan,
    build_plan,
    build_strategy_facts,
    default_open_group,
    next_to_try,
    normalize_quick_filter,
    normalize_strategy_grouping,
    try_stage,
    visible_rows,
)
from profile.ui.strategy_context_menu import (
    COMMAND_FAVORITE,
    COMMAND_RATING,
    can_rate_strategy,
    show_strategy_context_menu,
)
from profile.ui.strategy_list.panels import StrategyToolbar, TryNextPanel
from profile.ui.strategy_list.view import StrategyListView
from ui.accessibility import set_control_accessibility, set_state_text

# В коротком списке сворачивать нечего: все группы раскрыты сразу. В длинном
# раскрыта одна группа: открыл другую — прежняя закрылась сама.
LONG_LIST_MIN_ROWS = 30

_LIST_DESCRIPTION = (
    "Стрелки ходят по стратегиям, Enter или Пробел выбирает стратегию либо сворачивает группу. "
    "Клавиша меню или правая кнопка мыши открывает оценку стратегии и избранное. "
    "Ctrl+F ставит курсор в поиск."
)


class ProfileStrategyListWidget(QWidget):
    strategy_activated = pyqtSignal(str)
    # Человек открыл другую группу длинного списка: (чей список, ключ группы;
    # пустой ключ — всё свёрнуто). Страница сохраняет это в настройки.
    open_group_changed = pyqtSignal(str, str)
    # Человек выбрал, по чему группировать список.
    grouping_changed = pyqtSignal(str)
    # Оценка стратегии: (стратегия, "work" / "notwork" / "" — снять).
    strategy_rating_requested = pyqtSignal(str, str)
    # Избранное: (стратегия, добавить или убрать).
    strategy_favorite_requested = pyqtSignal(str, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Что прислала страница.
        self._entries: dict = {}
        self._facts: dict = {}
        self._states: dict = {}
        self._usage: dict = {}
        self._current_strategy_id = "none"
        # Что выбрал человек.
        self._grouping = GROUPING_METHOD
        self._grouping_chosen_here = False
        self._quick_filter = FILTER_ALL
        # Раскрытые группы: None — раскрыты все (короткий список).
        self._open_groups: set[str] | None = None
        self._open_twins: set[str] = set()
        self._open_group_token = ""
        self._plan = StrategyListPlan()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._try_panel = TryNextPanel(self)
        self._try_panel.rated.connect(self._on_current_rated)
        self._try_panel.next_requested.connect(self._activate_next_to_try)
        self._try_panel.hide()
        layout.addWidget(self._try_panel)

        self._toolbar = StrategyToolbar(self)
        self._toolbar.search_changed.connect(lambda _text: self._refresh())
        self._toolbar.filter_changed.connect(self._on_filter_changed)
        self._toolbar.grouping_changed.connect(self._on_grouping_chosen)
        self._toolbar.search.close_requested.connect(self._leave_search)
        self._toolbar.search.activate_current_result.connect(self._activate_current_row)
        layout.addWidget(self._toolbar)

        self._list = StrategyListView(self)
        self._list.strategy_chosen.connect(self._on_strategy_chosen)
        self._list.group_toggle_requested.connect(self._on_group_toggle)
        self._list.twins_toggle_requested.connect(self._on_twins_toggle)
        self._list.menu_requested.connect(self._show_strategy_menu)
        self._toolbar.search.navigate_results.connect(self._list.move_from_search)
        set_control_accessibility(self._list, name="Список готовых стратегий", description=_LIST_DESCRIPTION)
        layout.addWidget(self._list, 1)

        # Страница выстраивает порядок обхода клавишей Tab по этим именам.
        self._search = self._toolbar.search
        self._grouping_combo = self._toolbar.grouping_combo
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocusProxy(self._list)
        QWidget.setTabOrder(self._search, self._grouping_combo)
        QWidget.setTabOrder(self._grouping_combo, self._list)

        self._search_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self)
        # WindowShortcut: Ctrl+F работает с любым фокусом в окне; на других
        # страницах его гасит проверка isVisible() в обработчике.
        self._search_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._search_shortcut.activated.connect(self._focus_search)
        self._search_shortcut.activatedAmbiguously.connect(self._focus_search)

    # ------------------------------------------------------------------
    # Что присылает страница
    # ------------------------------------------------------------------
    def set_rows(
        self,
        *,
        entries,
        states,
        current_strategy_id: str,
        open_group_token: str | None = None,
        open_group: str | None = None,
        grouping: str | None = None,
        usage=None,
    ) -> None:
        """Показывает стратегии профиля.

        open_group_token — постоянный ключ профиля, open_group — группа, которую
        человек оставил открытой у него в прошлый раз (None — ещё не открывал).
        grouping — сохранённая группировка; она действует, пока человек не
        выбрал другую сам. usage — частота стратегий в готовых пресетах.
        """
        entries = dict(entries or {})
        if entries.keys() != self._entries.keys() or any(
            entries[key] is not self._entries[key] for key in entries
        ):
            self._facts = build_strategy_facts(entries)
        self._entries = entries
        self._states = dict(states or {})
        if usage is not None:
            # None — частота та же, что прислали раньше (обновились только оценки).
            self._usage = dict(usage)
        self._current_strategy_id = str(current_strategy_id or "none").strip() or "none"
        if grouping is not None and not self._grouping_chosen_here:
            self._grouping = normalize_strategy_grouping(grouping)

        token = str(open_group_token or "")
        owner_changed = open_group_token is not None and token != self._open_group_token
        if owner_changed:
            # Другой профиль: раскрытое относилось к прежнему списку.
            self._open_group_token = token
            self._open_twins.clear()
        plan = self._build_plan()
        if owner_changed or self._open_groups_stale(plan):
            self._open_groups = self._initial_open_groups(plan, open_group if open_group_token is not None else None)
        # У нового профиля список прокручивается к выбранной стратегии, но
        # раскрытую группу определяет только то, что человек оставил открытым.
        self._refresh(plan, scroll_to_current=owner_changed)

    def set_current_strategy_id(self, strategy_id: str) -> None:
        next_id = str(strategy_id or "none").strip() or "none"
        if next_id == self._current_strategy_id:
            return
        self._current_strategy_id = next_id
        self._refresh(open_current_group=True)

    def onboarding_target(self, name: str):
        """Что подсветить обучающей экскурсии; None — этой части сейчас нет на экране."""
        if name == "strategy_try":
            return None if self._try_panel.isHidden() else self._try_panel
        if name == "strategy_find":
            return self._toolbar
        return None

    # ------------------------------------------------------------------
    # Состояние → экран
    # ------------------------------------------------------------------
    def _long_list(self) -> bool:
        return len(self._entries) > LONG_LIST_MIN_ROWS

    def _build_plan(self) -> StrategyListPlan:
        return build_plan(
            PlanRequest(
                facts=self._facts,
                states=self._states,
                usage=self._usage,
                current_strategy_id=self._current_strategy_id,
                query=self._search.text(),
                quick_filter=self._quick_filter,
                grouping=self._grouping,
            )
        )

    def _initial_open_groups(self, plan: StrategyListPlan, remembered: str | None) -> set[str] | None:
        if not self._long_list():
            return None
        key = default_open_group(plan, remembered)
        return {key} if key else set()

    def _open_groups_stale(self, plan: StrategyListPlan) -> bool:
        """Раскрытое больше не подходит списку: он стал длинным или коротким."""
        return (self._open_groups is None) == self._long_list()

    def _refresh(
        self,
        plan: StrategyListPlan | None = None,
        *,
        open_current_group: bool = False,
        scroll_to_current: bool = False,
    ) -> None:
        """Единственное место, где состояние превращается в строки на экране."""
        plan = plan if plan is not None else self._build_plan()
        self._plan = plan
        reveal_current = open_current_group or scroll_to_current
        if open_current_group and self._open_groups is not None and not plan.narrowed:
            # Выбранная стратегия не должна оставаться в свёрнутой группе.
            group_key = plan.group_of(plan.current_strategy_id)
            if group_key and group_key not in self._open_groups:
                self._open_groups = {group_key}
        current_row = self._list.current_row()
        keep_key = current_row.key if current_row is not None else ""
        rows = visible_rows(plan, open_groups=self._open_groups, open_twins=self._open_twins)
        self._list.list_model().set_rows(rows)

        current_key = f"i:{plan.current_strategy_id}"
        if reveal_current and self._list.set_current_key(current_key, scroll=True):
            pass
        elif not (keep_key and self._list.set_current_key(keep_key)):
            if not self._list.set_current_key(current_key):
                first = next((row for row in rows if row.selectable), None)
                if first is not None:
                    self._list.set_current_key(first.key)

        long_list = self._long_list()
        self._toolbar.set_long_list(long_list)
        self._toolbar.set_grouping(self._grouping)
        self._toolbar.set_quick_filter(self._quick_filter)
        self._toolbar.set_summary(plan.visible_count, plan.total_count)
        self._sync_try_panel(plan)
        self._sync_list_state_text(plan)

    def _sync_try_panel(self, plan: StrategyListPlan) -> None:
        current_id = plan.current_strategy_id
        facts = self._facts.get(current_id)
        if facts is None or not can_rate_strategy(current_id) or not self._long_list():
            self._try_panel.hide()
            return
        next_id = next_to_try(plan.queue, self._states, current_id)
        stage, tried, total = try_stage(plan, self._states)
        self._try_panel.show_state(
            name=facts.name,
            plain_label=facts.plain_label,
            rating=str(getattr(self._states.get(current_id), "rating", "") or ""),
            next_name=self._facts[next_id].name if next_id in self._facts else "",
            tried=tried,
            total=total,
            recommended_stage=stage == "recommended",
        )
        self._try_panel.show()

    def _sync_list_state_text(self, plan: StrategyListPlan) -> None:
        if not plan.total_count:
            text = "Список готовых стратегий пуст"
        elif not plan.visible_count:
            text = "Список готовых стратегий: ничего не найдено"
        else:
            text = f"Список готовых стратегий: показано {plan.visible_count} из {plan.total_count}"
        set_state_text(self._list, text)

    # ------------------------------------------------------------------
    # Что делает человек
    # ------------------------------------------------------------------
    def _on_strategy_chosen(self, strategy_id: str) -> None:
        if strategy_id and strategy_id != self._current_strategy_id:
            self.strategy_activated.emit(strategy_id)

    def _activate_current_row(self) -> None:
        row = self._list.current_row()
        if row is not None and row.kind == ROW_STRATEGY:
            self._on_strategy_chosen(row.strategy_id)

    def _on_group_toggle(self, group_key: str, expand: bool) -> None:
        if not group_key:
            return
        if self._long_list():
            # В длинном списке раскрыта одна группа: открыл другую — прежняя закрылась.
            self._open_groups = {group_key} if expand else set()
            self.open_group_changed.emit(self._open_group_token, group_key if expand else "")
        else:
            if self._open_groups is None:
                self._open_groups = {group.key for group in self._plan.groups}
            if expand:
                self._open_groups.add(group_key)
            else:
                self._open_groups.discard(group_key)
        self._refresh()
        self._list.set_current_key(f"g:{group_key}", scroll=True)

    def _on_twins_toggle(self, twin_key: str) -> None:
        if not twin_key:
            return
        if twin_key in self._open_twins:
            self._open_twins.discard(twin_key)
        else:
            self._open_twins.add(twin_key)
        self._refresh()

    def _on_filter_changed(self, key: str) -> None:
        self._quick_filter = normalize_quick_filter(key)
        self._refresh()

    def _on_grouping_chosen(self, grouping: str) -> None:
        grouping = normalize_strategy_grouping(grouping)
        if grouping == self._grouping:
            return
        self._grouping = grouping
        self._grouping_chosen_here = True
        self._open_twins.clear()
        plan = self._build_plan()
        # Группы стали другими: раскрытая прежде к ним не относится.
        self._open_groups = self._initial_open_groups(plan, None)
        self._refresh(plan, scroll_to_current=True)
        self.grouping_changed.emit(grouping)

    def _show_strategy_menu(self, strategy_id: str, global_pos: QPoint) -> None:
        facts = self._facts.get(strategy_id)
        if facts is None or not can_rate_strategy(strategy_id):
            return
        state = self._states.get(strategy_id)
        command, value = show_strategy_context_menu(
            parent=self,
            global_pos=global_pos,
            strategy_name=facts.name,
            rating=str(getattr(state, "rating", "") or ""),
            favorite=bool(getattr(state, "favorite", False)),
        )
        if command == COMMAND_RATING:
            self.strategy_rating_requested.emit(strategy_id, str(value or ""))
        elif command == COMMAND_FAVORITE:
            self.strategy_favorite_requested.emit(strategy_id, bool(value))

    def _on_current_rated(self, rating: str) -> None:
        if can_rate_strategy(self._current_strategy_id):
            self.strategy_rating_requested.emit(self._current_strategy_id, rating)

    def _activate_next_to_try(self) -> None:
        next_id = next_to_try(self._plan.queue, self._states, self._current_strategy_id)
        if next_id:
            self.strategy_activated.emit(next_id)

    def _focus_search(self) -> None:
        if not self.isVisible() or not self.isEnabled():
            return
        self._search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._search.selectAll()

    def _leave_search(self) -> None:
        """Esc в поиске: очистить запрос и вернуть фокус в список."""
        if self._search.text():
            self._search.clear()
        self._list.setFocus(Qt.FocusReason.OtherFocusReason)
