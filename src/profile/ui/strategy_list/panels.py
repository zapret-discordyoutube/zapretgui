"""Панели над списком стратегий: поиск, вкладки и «попробовать следующую»."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    SegmentedWidget,
    SimpleCardWidget,
    StrongBodyLabel,
)

from profile.strategy_list import (
    FILTER_ALL,
    FILTER_FAVORITE,
    FILTER_RECOMMENDED,
    FILTER_WORKS,
    QUICK_FILTERS,
    STRATEGY_GROUPINGS,
)
from ui.accessibility import remove_line_edit_buttons_from_tab_order, set_control_accessibility, set_state_text
from ui.fluent_widgets import set_tooltip

_NAVIGATION_KEYS = (
    Qt.Key.Key_Down,
    Qt.Key.Key_Up,
    Qt.Key.Key_Home,
    Qt.Key.Key_End,
    Qt.Key.Key_PageDown,
    Qt.Key.Key_PageUp,
)


# Что лежит на вкладке: подсказка при наведении и текст для чтения с экрана.
_TAB_HINTS = {
    FILTER_RECOMMENDED: "Стратегии, которые в готовых пресетах стоят на этом же сервисе. Чем чаще, тем выше.",
    FILTER_WORKS: "Стратегии, которые вы отметили рабочими у этого профиля.",
    FILTER_FAVORITE: "Стратегии, которые вы добавили в избранное.",
    FILTER_ALL: "Весь каталог готовых стратегий, разложенный по группам.",
}
# Что написать в пустом списке вкладки.
TAB_EMPTY_TEXTS = {
    FILTER_WORKS: "Здесь появятся стратегии, которые вы отметите кнопкой «Работает».",
    FILTER_FAVORITE: "Здесь появятся стратегии, добавленные в избранное. Добавить можно правой кнопкой мыши по стратегии.",
}


def _muted(text: str = "") -> CaptionLabel:
    label = CaptionLabel(text)
    label.setTextColor(QColor(0, 0, 0, 150), QColor(255, 255, 255, 150))
    return label


class StrategySearchLineEdit(SearchLineEdit):
    """Поиск стратегий: Enter выбирает найденное, стрелки ходят по списку."""

    activate_current_result = pyqtSignal()
    navigate_results = pyqtSignal(int)
    close_requested = pyqtSignal()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close_requested.emit()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activate_current_result.emit()
            event.accept()
            return
        if event.key() in _NAVIGATION_KEYS:
            self.navigate_results.emit(int(event.key()))
            event.accept()
            return
        super().keyPressEvent(event)


class StrategyToolbar(QWidget):
    """Вкладки списка и группировка; поиск со счётчиком открывается по Ctrl+F.

    Поиск нужен редко, а место занимает всегда, поэтому по умолчанию его нет
    на экране: строка появляется по Ctrl+F и убирается по Esc.
    """

    search_changed = pyqtSignal(str)
    filter_changed = pyqtSignal(str)
    grouping_changed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.search_row = QWidget(self)
        search_row = QHBoxLayout(self.search_row)
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(10)
        self.search = StrategySearchLineEdit(self.search_row)
        self.search.setPlaceholderText("Поиск: название, способ обхода, параметр, автор")
        self.search.setClearButtonEnabled(True)
        hint = (
            "Ищет по названию и прежнему названию, способу обхода словами, параметрам --lua-desync, "
            "описанию и автору. Ctrl+F открывает и закрывает поиск, Esc закрывает его."
        )
        set_tooltip(self.search, hint)
        set_control_accessibility(
            self.search,
            name="Поиск готовых стратегий",
            description=hint + " Стрелки вверх и вниз ходят по найденному, Enter выбирает стратегию.",
        )
        remove_line_edit_buttons_from_tab_order(self.search)
        self.search.textChanged.connect(lambda text: self.search_changed.emit(str(text or "")))
        search_row.addWidget(self.search, 1)

        self.summary = BodyLabel("")
        set_tooltip(self.summary, "Сколько стратегий сейчас показано из всех стратегий каталога.")
        search_row.addWidget(self.summary)
        self.search_row.hide()
        layout.addWidget(self.search_row)

        self.filter_row = QWidget(self)
        filter_layout = QHBoxLayout(self.filter_row)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(6)

        # Вкладки с числами: одна строка отвечает и на «что тут есть», и на
        # «сколько этого». Группы по способу обхода остаются только во «Все».
        self.tabs = SegmentedWidget(self.filter_row)
        self._tab_titles = dict(QUICK_FILTERS)
        self._tab_counts: dict[str, int] = {}
        for key, title in QUICK_FILTERS:
            self.tabs.addItem(routeKey=key, text=title, onClick=lambda _checked=False, value=key: self._on_tab_clicked(value))
            set_tooltip(self.tabs.items[key], _TAB_HINTS[key])
        self.tabs.setCurrentItem(FILTER_ALL)
        filter_layout.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignVCenter)
        filter_layout.addStretch(1)

        self.grouping_combo = ComboBox(self.filter_row)
        for key, title in STRATEGY_GROUPINGS:
            self.grouping_combo.addItem(title, userData=key)
        self.grouping_combo.setMinimumWidth(190)
        grouping_hint = (
            "По чему разложить весь каталог: по способу обхода, по серии (первое слово "
            "названия) или по источнику (уточнение «из …» в названии)."
        )
        set_tooltip(self.grouping_combo, grouping_hint)
        set_control_accessibility(self.grouping_combo, name="Группировка готовых стратегий", description=grouping_hint)
        self.grouping_combo.currentIndexChanged.connect(self._on_grouping_index_changed)
        filter_layout.addWidget(self.grouping_combo)
        layout.addWidget(self.filter_row)

    def search_open(self) -> bool:
        return not self.search_row.isHidden()

    def open_search(self) -> None:
        self.search_row.show()
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def close_search(self) -> None:
        """Убирает строку поиска; запрос сбрасывается — спрятанный поиск не должен сужать список."""
        if self.search.text():
            self.search.clear()
        self.search_row.hide()

    def _on_grouping_index_changed(self, index: int) -> None:
        self.grouping_changed.emit(str(self.grouping_combo.itemData(index) or ""))

    def _on_tab_clicked(self, key: str) -> None:
        self.set_quick_filter(key)
        self.filter_changed.emit(key)

    def quick_filter(self) -> str:
        return str(self.tabs.currentRouteKey() or FILTER_ALL)

    def set_quick_filter(self, key: str) -> None:
        self.tabs.setCurrentItem(key)

    def set_tab_counts(self, counts: dict[str, int]) -> None:
        """Числа на вкладках. Вкладки «Советуемые» нет, когда советовать нечего."""
        counts = {key: int(counts.get(key, 0)) for key in self._tab_titles}
        if counts == self._tab_counts:
            return
        self._tab_counts = counts
        for key, title in self._tab_titles.items():
            item = self.tabs.items[key]
            item.setText(f"{title}  {counts[key]}")
            set_control_accessibility(
                item,
                name=f"Вкладка стратегий: {title}",
                description=f"{_TAB_HINTS[key]} Стратегий на вкладке: {counts[key]}.",
            )
        self.tabs.items[FILTER_RECOMMENDED].setHidden(counts[FILTER_RECOMMENDED] <= 0)
        # Ширина вкладок изменилась: подсветка выбранной встаёт на новое место
        # сразу, а не остаётся там, где вкладка была до смены чисел.
        self.tabs.adjustSize()
        self.tabs.layout().activate()
        self.tabs._adjustIndicatorPos()

    def set_grouping(self, grouping: str) -> None:
        for index in range(self.grouping_combo.count()):
            if self.grouping_combo.itemData(index) == grouping:
                if self.grouping_combo.currentIndex() != index:
                    self.grouping_combo.blockSignals(True)
                    self.grouping_combo.setCurrentIndex(index)
                    self.grouping_combo.blockSignals(False)
                return

    def set_summary(self, visible: int, total: int) -> None:
        text = f"{int(visible)} из {int(total)}"
        if self.summary.text() != text:
            self.summary.setText(text)
            set_state_text(self.summary, f"Показано готовых стратегий: {text}")

    def set_layout_mode(self, *, tabs: bool, grouping: bool) -> None:
        """Что из строки вкладок нужно этому списку.

        В коротком списке без советуемых выбирать не из чего — остаётся только
        поиск по Ctrl+F. Группировка относится ко всему каталогу, поэтому
        переключатель виден только на вкладке «Все».
        """
        self.filter_row.setHidden(not tabs)
        self.grouping_combo.setHidden(not grouping)


class TryNextPanel(SimpleCardWidget):
    """Панель над списком: что выбрано сейчас и что делать, если не помогло.

    Новичку не нужен каталог из сотен строк — ему нужно понять, работает ли
    выбранное, и получить следующую стратегию, если нет. Очередь — стратегии,
    которые в готовых пресетах стоят на этом же сервисе, самые частые первыми.
    """

    # Человек оценил выбранную стратегию: "work" или "notwork".
    rated = pyqtSignal(str)
    # После «не работает» нужно включить следующую стратегию очереди.
    next_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 12, 10)
        layout.setSpacing(12)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = StrongBodyLabel("")
        self.hint = _muted("")
        self.hint.setWordWrap(True)
        texts.addWidget(self.title)
        texts.addWidget(self.hint)
        layout.addLayout(texts, 1)

        self.works_button = PushButton("Работает", icon=FluentIcon.ACCEPT)
        set_tooltip(self.works_button, "Отметить, что с выбранной стратегией сайт открывается.")
        self.works_button.clicked.connect(lambda: self.rated.emit("work"))
        layout.addWidget(self.works_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.fails_button = PrimaryPushButton("Не работает — следующая")
        self.fails_button.clicked.connect(self._on_fails_clicked)
        layout.addWidget(self.fails_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._has_next = False

    def _on_fails_clicked(self) -> None:
        self.rated.emit("notwork")
        if self._has_next:
            self.next_requested.emit()

    def show_state(
        self,
        *,
        name: str,
        plain_label: str,
        rating: str,
        next_name: str,
        tried: int,
        total: int,
        recommended_stage: bool = True,
    ) -> None:
        title = f"Сейчас выбрана: {name}"
        if plain_label:
            title = f"{title} · {plain_label}"
        self.title.setText(title)
        self._has_next = bool(next_name)

        if rating == "work":
            hint = "Вы отметили, что она работает."
        elif rating == "notwork":
            hint = "Вы отметили, что она не работает."
        else:
            hint = "Откройте сайт и проверьте. Потом отметьте, помогло или нет."
        if total > 0 and recommended_stage:
            hint = f"{hint} Проверено {tried} из {total} советуемых для этого сервиса."
        elif total > 0:
            # Советуемые кончились — перебор идёт дальше по всему каталогу.
            hint = f"{hint} Советуемые проверены, дальше идут остальные: {tried} из {total}."
        if next_name:
            hint = f"{hint} Следующая: {next_name}."
        self.hint.setText(hint)

        self.works_button.setEnabled(rating != "work")
        self.fails_button.setText("Не работает — следующая" if next_name else "Не работает")
        self.fails_button.setEnabled(bool(next_name) or rating != "notwork")
        set_tooltip(
            self.fails_button,
            f"Отметить, что стратегия не помогла, и сразу включить следующую: {next_name}."
            if next_name
            else "Отметить, что с выбранной стратегией сайт не открывается.",
        )
        set_control_accessibility(self.works_button, name="Выбранная стратегия работает", description=hint)
        set_control_accessibility(
            self.fails_button,
            name="Выбранная стратегия не работает" + (", включить следующую" if next_name else ""),
            description=hint,
        )
