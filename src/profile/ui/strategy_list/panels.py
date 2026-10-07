"""Панели над списком стратегий: поиск с отборами и «попробовать следующую»."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    PillPushButton,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    SimpleCardWidget,
    StrongBodyLabel,
)

from profile.strategy_list import FILTER_ALL, QUICK_FILTERS, STRATEGY_GROUPINGS
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
    """Поиск, счётчик найденного, группировка и быстрые отборы."""

    search_changed = pyqtSignal(str)
    filter_changed = pyqtSignal(str)
    grouping_changed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self.search = StrategySearchLineEdit(self)
        self.search.setPlaceholderText("Поиск: название, способ обхода, параметр, автор")
        self.search.setClearButtonEnabled(True)
        hint = (
            "Ищет по названию и прежнему названию, способу обхода словами, параметрам --lua-desync, "
            "описанию и автору. Ctrl+F ставит курсор сюда, Esc очищает поиск."
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

        self.grouping_combo = ComboBox(self)
        for key, title in STRATEGY_GROUPINGS:
            self.grouping_combo.addItem(title, userData=key)
        self.grouping_combo.setMinimumWidth(190)
        grouping_hint = (
            "По чему разложить стратегии ниже советуемых: по способу обхода, по серии (первое слово "
            "названия) или по источнику (уточнение «из …» в названии)."
        )
        set_tooltip(self.grouping_combo, grouping_hint)
        set_control_accessibility(self.grouping_combo, name="Группировка готовых стратегий", description=grouping_hint)
        self.grouping_combo.currentIndexChanged.connect(self._on_grouping_index_changed)
        search_row.addWidget(self.grouping_combo)
        layout.addLayout(search_row)

        self.filter_row = QWidget(self)
        filter_layout = QHBoxLayout(self.filter_row)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(6)
        self.filter_buttons: dict[str, PillPushButton] = {}
        for key, title in QUICK_FILTERS:
            button = PillPushButton(title, self.filter_row)
            button.setCheckable(True)
            button.setChecked(key == FILTER_ALL)
            set_control_accessibility(
                button,
                name=f"Отбор стратегий: {title}",
                description="Показывает в списке только такие стратегии.",
            )
            button.clicked.connect(lambda _checked=False, value=key: self._on_filter_clicked(value))
            self.filter_buttons[key] = button
            filter_layout.addWidget(button)
        filter_layout.addStretch(1)
        layout.addWidget(self.filter_row)

    def _on_grouping_index_changed(self, index: int) -> None:
        self.grouping_changed.emit(str(self.grouping_combo.itemData(index) or ""))

    def _on_filter_clicked(self, key: str) -> None:
        self.set_quick_filter(key)
        self.filter_changed.emit(key)

    def set_quick_filter(self, key: str) -> None:
        for value, button in self.filter_buttons.items():
            button.setChecked(value == key)

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

    def set_long_list(self, long_list: bool) -> None:
        """В коротком списке группировать и отбирать нечего: остаётся только поиск."""
        self.filter_row.setVisible(bool(long_list))
        self.grouping_combo.setVisible(bool(long_list))


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

    def show_state(self, *, name: str, plain_label: str, rating: str, next_name: str, tried: int, total: int) -> None:
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
        if total > 0:
            hint = f"{hint} Проверено {tried} из {total} советуемых для этого сервиса."
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
