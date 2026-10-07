"""Страница подробностей о стратегии.

Открывается кнопкой-меткой на плитке («в 13 пресетах») и занимает место
списка. Рассказывает то, что на плитке не помещается: что стратегия делает
шаг за шагом, где стоит в готовых пресетах, что человек о ней уже отмечал, и
показывает её строки запуска. Отсюда же стратегию можно применить и оценить.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
)

from profile.strategy_list.knowledge import StrategyStep, explain_strategy
from profile.ui.strategy_list.icons import strategy_icon
from ui.accessibility import set_control_accessibility

_LABELS = {
    "recommended": "советуемая",
    "stock": "из готового пресета",
    "experimental": "опытная",
    "caution": "осторожно",
    "game": "для игр",
    "stable": "стабильная",
}
_MAX_PLACES = 12
_WARNING = (QColor("#b55a2a"), QColor("#e9a071"))
_MUTED = (QColor(0, 0, 0, 150), QColor(255, 255, 255, 150))


@dataclass(frozen=True)
class StrategyDetails:
    """Всё, что показывает страница подробностей."""

    strategy_id: str
    name: str
    plain_label: str = ""
    family_key: str = ""
    family_color: str = ""
    args: str = ""
    description: str = ""
    author: str = ""
    label: str = ""
    old_name: str = ""
    rating: str = ""
    favorite: bool = False
    is_current: bool = False
    # Где стоит в готовых пресетах: ((сервис, число пресетов), ...).
    places: tuple[tuple[str, int], ...] = ()
    same_service: int = 0
    # Отметки человека на других профилях: (работает, не работает).
    personal: tuple[int, int] = (0, 0)
    steps: tuple[StrategyStep, ...] = field(default=())


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        return few
    return many


def experience_lines(details: StrategyDetails) -> list[str]:
    here = {
        "work": "На этом профиле вы отметили, что она работает.",
        "notwork": "На этом профиле вы отметили, что она не работает.",
    }.get(details.rating, "На этом профиле вы её ещё не оценивали.")
    lines = [here]
    works, fails = details.personal
    if works:
        lines.append(f"На других профилях отмечена рабочей: {works}.")
    if fails:
        lines.append(f"На других профилях отмечена нерабочей: {fails}.")
    if details.favorite:
        lines.append("Стратегия у вас в избранном.")
    return lines


def places_lines(details: StrategyDetails) -> list[str]:
    if not details.places:
        return ["В готовых пресетах эта стратегия не встречается."]
    lines = [
        f"{service} — в {count} {_plural(count, 'пресете', 'пресетах', 'пресетах')}"
        for service, count in details.places[:_MAX_PLACES]
    ]
    rest = len(details.places) - _MAX_PLACES
    if rest > 0:
        lines.append(f"…и ещё на {rest} {_plural(rest, 'сервисе', 'сервисах', 'сервисах')}.")
    return lines


def _text(label_class, text: str, *, colors=None, selectable: bool = False):
    label = label_class(text)
    label.setWordWrap(True)
    if colors is not None:
        label.setTextColor(*colors)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class _Card(SimpleCardWidget):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 14, 18, 16)
        self.body.setSpacing(8)
        self.body.addWidget(StrongBodyLabel(title))

    def clear(self) -> None:
        while self.body.count() > 1:
            item = self.body.takeAt(1)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget() is not None:
                        child.widget().deleteLater()


class StrategyDetailsView(QWidget):
    # Нажали «Применить»: стратегию выбрали так же, как щелчком в списке.
    strategy_chosen = pyqtSignal(str)
    rating_requested = pyqtSignal(str, str)
    favorite_requested = pyqtSignal(str, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._details: StrategyDetails | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = ScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        outer.addWidget(self._scroll)
        content = QWidget()
        self._scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        head = SimpleCardWidget(content)
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(18, 16, 18, 16)
        head_layout.setSpacing(16)
        self._icon = QLabel(head)
        self._icon.setFixedSize(52, 52)
        head_layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self._title = _text(SubtitleLabel, "", selectable=True)
        self._subtitle = _text(BodyLabel, "", colors=_MUTED)
        self._description = _text(BodyLabel, "")
        titles.addWidget(self._title)
        titles.addWidget(self._subtitle)
        titles.addWidget(self._description)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._apply_button = PrimaryPushButton("Применить")
        self._apply_button.clicked.connect(lambda: self._emit(self.strategy_chosen))
        self._works_button = PushButton("Работает", icon=FluentIcon.ACCEPT)
        self._works_button.clicked.connect(lambda: self._rate("work"))
        self._fails_button = PushButton("Не работает", icon=FluentIcon.CLOSE)
        self._fails_button.clicked.connect(lambda: self._rate("notwork"))
        self._favorite_button = PushButton("В избранное", icon=FluentIcon.HEART)
        self._favorite_button.clicked.connect(self._toggle_favorite)
        for button in (self._apply_button, self._works_button, self._fails_button, self._favorite_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        titles.addSpacing(6)
        titles.addLayout(buttons)
        head_layout.addLayout(titles, 1)
        layout.addWidget(head)

        self._steps_card = _Card("Что делает эта стратегия", content)
        self._places_card = _Card("Где стоит в готовых пресетах", content)
        self._experience_card = _Card("Ваш опыт", content)
        self._facts_card = _Card("Сведения", content)
        self._args_card = _Card("Строки запуска", content)
        for card in (self._steps_card, self._places_card, self._experience_card, self._facts_card, self._args_card):
            layout.addWidget(card)
        layout.addStretch(1)

    # ------------------------------------------------------------------
    def strategy_id(self) -> str:
        return self._details.strategy_id if self._details is not None else ""

    def _emit(self, signal) -> None:
        if self._details is not None:
            signal.emit(self._details.strategy_id)

    def _rate(self, rating: str) -> None:
        if self._details is not None:
            # Повторное нажатие на уже поставленную оценку снимает её.
            self.rating_requested.emit(self._details.strategy_id, "" if self._details.rating == rating else rating)

    def _toggle_favorite(self) -> None:
        if self._details is not None:
            self.favorite_requested.emit(self._details.strategy_id, not self._details.favorite)

    def _copy_args(self) -> None:
        if self._details is not None:
            QGuiApplication.clipboard().setText(self._details.args)

    # ------------------------------------------------------------------
    def show_details(self, details: StrategyDetails) -> None:
        steps = details.steps or explain_strategy(details.args)
        same_strategy = self._details is not None and self._details.strategy_id == details.strategy_id
        self._details = details

        ratio = self.devicePixelRatioF()
        self._icon.setPixmap(strategy_icon(details.family_key, details.family_color, details.rating, 52, ratio, "#2d2d2d"))
        self._title.setText(details.name)
        subtitle = [details.plain_label] if details.plain_label else []
        if details.same_service:
            word = _plural(details.same_service, "готовом пресете", "готовых пресетах", "готовых пресетах")
            subtitle.append(f"стоит на этом сервисе в {details.same_service} {word}")
        if details.label in _LABELS:
            subtitle.append(f"пометка каталога: {_LABELS[details.label]}")
        self._subtitle.setText(" · ".join(subtitle))
        self._subtitle.setVisible(bool(subtitle))
        self._description.setText(details.description)
        self._description.setVisible(bool(details.description))

        self._apply_button.setText("Выбрана" if details.is_current else "Применить")
        self._apply_button.setEnabled(not details.is_current)
        self._works_button.setText("Снять «Работает»" if details.rating == "work" else "Работает")
        self._fails_button.setText("Снять «Не работает»" if details.rating == "notwork" else "Не работает")
        self._favorite_button.setText("Убрать из избранного" if details.favorite else "В избранное")
        for button in (self._apply_button, self._works_button, self._fails_button, self._favorite_button):
            set_control_accessibility(button, name=f"{button.text()}: {details.name}")

        self._experience_card.clear()
        for line in experience_lines(details):
            self._experience_card.body.addWidget(_text(BodyLabel, line))
        if same_strategy:
            # Изменилась только оценка: остальные разделы те же, не перестраиваем.
            return

        self._steps_card.clear()
        if not steps:
            self._steps_card.body.addWidget(_text(BodyLabel, "У стратегии нет строк обхода: трафик идёт как есть."))
        for number, step in enumerate(steps, 1):
            title = _text(BodyLabel, f"{number}. {step.title}")
            font = title.font()
            font.setBold(True)
            title.setFont(font)
            self._steps_card.body.addWidget(title)
            self._steps_card.body.addWidget(_text(BodyLabel, step.text))
            for note in step.notes:
                self._steps_card.body.addWidget(_text(BodyLabel, f"• {note}", colors=_MUTED))
            if step.caution:
                self._steps_card.body.addWidget(_text(BodyLabel, f"Обратите внимание: {step.caution}", colors=_WARNING))
            self._steps_card.body.addWidget(_text(CaptionLabel, step.line, colors=_MUTED, selectable=True))

        self._places_card.clear()
        for line in places_lines(details):
            self._places_card.body.addWidget(_text(BodyLabel, line))

        self._facts_card.clear()
        facts = [
            ("Автор", details.author),
            ("Пометка каталога", _LABELS.get(details.label, details.label)),
            ("Прежнее название", details.old_name),
            ("Имя в каталоге", details.strategy_id),
        ]
        for name, value in facts:
            if value:
                self._facts_card.body.addWidget(_text(BodyLabel, f"{name}: {value}", selectable=True))

        self._args_card.clear()
        self._args_card.body.addWidget(_text(BodyLabel, details.args or "—", selectable=True))
        copy_row = QHBoxLayout()
        copy_button = PushButton("Скопировать", icon=FluentIcon.COPY)
        copy_button.clicked.connect(self._copy_args)
        set_control_accessibility(copy_button, name="Скопировать строки запуска стратегии")
        copy_row.addWidget(copy_button)
        copy_row.addStretch(1)
        self._args_card.body.addLayout(copy_row)
        self._scroll.verticalScrollBar().setValue(0)
