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
    CardWidget,
    FlowLayout,
    FluentIcon,
    IconWidget,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
    isDarkTheme,
)

from profile.strategy_families import strategy_family
from profile.strategy_list.knowledge import StrategyStep, explain_strategy
from app.ui_texts import tr as tr_catalog
from profile.ui.profile_icon import profile_icon_pixmap
from profile.ui.strategy_list.icons import strategy_icon
from ui.accessibility import set_control_accessibility
from ui.fluent_widgets import set_tooltip
from ui.onboarding.illustrations import TechniqueIllustration

_LABELS = {
    "recommended": "советуемая",
    "stock": "из готового пресета",
    "experimental": "опытная",
    "caution": "осторожно",
    "game": "для игр",
    "stable": "стабильная",
}
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
    # Где стоит в готовых пресетах: StrategyPlace (profile.strategy_usage).
    places: tuple = ()
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


def experience_rows(details: StrategyDetails) -> list[tuple[str, str, str]]:
    """Отметки человека строками (значок, подпись, значение)."""
    icon, here = {
        "work": ("ACCEPT", "Работает"),
        "notwork": ("CLOSE", "Не работает"),
    }.get(details.rating, ("HELP", "Ещё не оценивали"))
    rows = [(icon, "На этом профиле", here)]
    works, fails = details.personal
    if works or fails:
        parts = []
        if works:
            parts.append(f"работает — {works}")
        if fails:
            parts.append(f"не работает — {fails}")
        rows.append(("PEOPLE", "На других профилях", ", ".join(parts)))
    if details.favorite:
        rows.append(("HEART", "Избранное", "Стратегия у вас в избранном"))
    return rows


def presets_text(count: int) -> str:
    return f"в {count} {_plural(count, 'готовом пресете', 'готовых пресетах', 'готовых пресетах')}"


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


def _visible_color(color: str) -> str:
    """Фирменный цвет, который видно на фоне: чёрный логотип на тёмной теме пропал бы."""
    value = QColor(color)
    if not value.isValid():
        return color
    brightness = (value.red() * 299 + value.green() * 587 + value.blue() * 114) / 255000
    if isDarkTheme() and brightness < 0.22:
        return "#f2f2f2"
    if not isDarkTheme() and brightness > 0.85:
        return "#1f1f1f"
    return color


_NOTE_ICONS = {
    "protection": "CERTIFICATE",
    "names": "TAG",
    "content": "DOCUMENT",
    "cut": "CUT",
    "repeats": "SYNC",
    "overlap": "COPY",
    "tweak": "EDIT",
    "origin": "INFO",
}


def _fluent_icon(name: str):
    return getattr(FluentIcon, name, FluentIcon.INFO)


def _soft_fill() -> str:
    return "rgba(255, 255, 255, 0.045)" if isDarkTheme() else "rgba(0, 0, 0, 0.04)"


class _Block(QFrame):
    """Подписанный блок: значок, заголовок мелким текстом и содержимое под ним."""

    def __init__(self, icon_name: str, caption: str, text: str, parent=None, *, detail: str = "", fill: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("strategyBlock")
        if fill:
            self.setStyleSheet(f"QFrame#strategyBlock {{ background: {_soft_fill()}; border-radius: 6px; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10) if fill else layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        icon = IconWidget(_fluent_icon(icon_name), self)
        icon.setFixedSize(16, 16)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(_text(CaptionLabel, caption, colors=_MUTED))
        texts.addWidget(_text(BodyLabel, text, selectable=True))
        if detail:
            texts.addWidget(_text(CaptionLabel, detail, colors=_MUTED))
        layout.addLayout(texts, 1)


class _Warning(QFrame):
    """То, о чём стоит знать: отдельная цветная строка со значком."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("strategyWarning")
        tint = "rgba(233, 160, 113, 0.14)" if isDarkTheme() else "rgba(181, 90, 42, 0.10)"
        self.setStyleSheet(f"QFrame#strategyWarning {{ background: {tint}; border-radius: 6px; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        icon = IconWidget(FluentIcon.INFO, self)
        icon.setFixedSize(16, 16)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(_text(BodyLabel, text, colors=_WARNING), 1)


class _StepRow(CardWidget):
    """Шаг стратегии. Нажатие показывает анимацию этого шага."""

    def __init__(self, number: int, step: StrategyStep, parent=None) -> None:
        super().__init__(parent)
        self.step = step
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        icon = QLabel(self)
        icon.setFixedSize(32, 32)
        icon.setPixmap(
            strategy_icon(step.family, strategy_family(step.family).color, "", 32, self.devicePixelRatioF(), "#2d2d2d")
        )
        head.addWidget(icon)
        head.addWidget(_text(StrongBodyLabel, f"{number}. {step.title}"), 1)
        if step.scene:
            head.addWidget(_text(CaptionLabel, "показать на схеме", colors=_MUTED), 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)

        about = QHBoxLayout()
        about.setSpacing(8)
        about.addWidget(_Block("PLAY", "Что происходит", step.text, self), 1)
        if step.why:
            about.addWidget(_Block("HELP", "Зачем", step.why, self), 1)
        layout.addLayout(about)

        if step.notes:
            layout.addWidget(_text(CaptionLabel, "Настройки шага", colors=_MUTED))
            # Плитки настроек в два столбца: значок, название, значение и пояснение.
            for start in range(0, len(step.notes), 2):
                row = QHBoxLayout()
                row.setSpacing(8)
                pair = step.notes[start : start + 2]
                for note in pair:
                    row.addWidget(_Block(_NOTE_ICONS.get(note.kind, "INFO"), note.label, note.value, self, detail=note.detail), 1)
                if len(pair) == 1:
                    row.addStretch(1)
                layout.addLayout(row)

        for caution in step.cautions:
            layout.addWidget(_Warning(caution, self))

        layout.addWidget(_text(CaptionLabel, f"В пресете: {step.line}", colors=_MUTED, selectable=True))
        if step.scene:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            set_tooltip(self, "Нажмите, чтобы посмотреть схему этого шага.")
        set_control_accessibility(self, name=f"Шаг {number}: {step.title}", description=f"{step.text} {step.why}".strip())


class _PlaceCard(CardWidget):
    """Сервис, на котором стратегия стоит в готовых пресетах.

    Если профиль этого сервиса есть в открытом пресете, нажатие открывает его.
    """

    def __init__(self, place, parent=None) -> None:
        super().__init__(parent)
        self.place = place
        self.setFixedSize(260, 60)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        icon = QLabel(self)
        icon.setFixedSize(24, 24)
        pixmap = profile_icon_pixmap(place.icon_name, color=_visible_color(place.icon_color), size=24)
        if not pixmap.isNull():
            icon.setPixmap(pixmap)
        layout.addWidget(icon)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        name = BodyLabel(place.name)
        name.setToolTip(place.name)
        texts.addWidget(name)
        caption = presets_text(place.presets)
        if place.profile_key:
            caption = f"{caption} · открыть"
        texts.addWidget(_text(CaptionLabel, caption, colors=_MUTED))
        layout.addLayout(texts, 1)
        if place.profile_key:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            set_tooltip(self, f"Открыть профиль «{place.name}» в этом пресете.")
            set_control_accessibility(self, name=f"Открыть профиль {place.name}", description=presets_text(place.presets))
        else:
            set_tooltip(self, f"{place.name}: в открытом пресете такого профиля нет.")
            set_control_accessibility(self, name=place.name, description=presets_text(place.presets))


class StrategyDetailsView(QWidget):
    # Нажали «Применить»: стратегию выбрали так же, как щелчком в списке.
    strategy_chosen = pyqtSignal(str)
    rating_requested = pyqtSignal(str, str)
    favorite_requested = pyqtSignal(str, bool)
    # Нажали карточку сервиса: открыть его профиль в этом пресете.
    profile_chosen = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._details: StrategyDetails | None = None
        self._step_rows: list[_StepRow] = []
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
        # Схема та же, что в экскурсии «Как пользоваться программой»: пакеты
        # едут от вас через проверку провайдера к сайту. Она одна на страницу
        # и показывает выбранный шаг — несколько анимаций сразу только грузили бы процессор.
        self._scene_caption = _text(CaptionLabel, "", colors=_MUTED)
        self._illustration = TechniqueIllustration(
            self._steps_card, tr_fn=lambda key, default: tr_catalog(key, default=default)
        )
        self._steps_layout = QVBoxLayout()
        self._steps_layout.setSpacing(8)
        self._steps_card.body.addWidget(self._illustration)
        self._steps_card.body.addWidget(self._scene_caption)
        self._steps_card.body.addLayout(self._steps_layout)
        layout.addWidget(self._steps_card)

        self._places_card = _Card("Где стоит в готовых пресетах", content)
        self._places_hint = _text(CaptionLabel, "", colors=_MUTED)
        self._places_card.body.addWidget(self._places_hint)
        places_host = QWidget(self._places_card)
        self._places_flow = FlowLayout(places_host, needAni=False)
        self._places_flow.setContentsMargins(0, 0, 0, 0)
        self._places_flow.setHorizontalSpacing(8)
        self._places_flow.setVerticalSpacing(8)
        self._places_card.body.addWidget(places_host)
        layout.addWidget(self._places_card)

        pair = QHBoxLayout()
        pair.setSpacing(12)
        self._experience_card = _Card("Ваш опыт", content)
        self._facts_card = _Card("Сведения", content)
        pair.addWidget(self._experience_card, 1)
        pair.addWidget(self._facts_card, 1)
        layout.addLayout(pair)
        self._args_card = _Card("Строки запуска", content)
        layout.addWidget(self._args_card)
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

    def _show_scene(self, row: _StepRow | None) -> None:
        """Схема показывает выбранный шаг; у шага без схемы она убрана."""
        scene = row.step.scene if row is not None else ""
        self._illustration.setVisible(bool(scene))
        self._scene_caption.setVisible(bool(scene))
        if scene:
            number = self._step_rows.index(row) + 1
            self._scene_caption.setText(
                f"Схема шага {number}: {row.step.title.lower()}. Слева вы, посередине проверка у провайдера, справа сайт."
            )
            self._illustration.set_scene(scene)

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
        for icon_name, caption, value in experience_rows(details):
            self._experience_card.body.addWidget(_Block(icon_name, caption, value, self._experience_card, fill=False))
        if same_strategy:
            # Изменилась только оценка: остальные разделы те же, не перестраиваем.
            return

        while self._steps_layout.count():
            item = self._steps_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._step_rows = []
        if not steps:
            self._steps_layout.addWidget(_text(BodyLabel, "У стратегии нет строк обхода: трафик идёт как есть."))
        for number, step in enumerate(steps, 1):
            row = _StepRow(number, step, self._steps_card)
            if step.scene:
                row.clicked.connect(lambda shown=row: self._show_scene(shown))
            self._step_rows.append(row)
            self._steps_layout.addWidget(row)
        # Сразу видна схема первого шага, у которого она есть.
        self._show_scene(next((row for row in self._step_rows if row.step.scene), None))

        self._places_flow.takeAllWidgets()
        for card in self._places_card.findChildren(_PlaceCard):
            card.deleteLater()
        if not details.places:
            self._places_hint.setText("В готовых пресетах эта стратегия не встречается.")
        else:
            opened = sum(1 for place in details.places if place.profile_key)
            hint = f"Сервисов: {len(details.places)}."
            if opened:
                hint += " Нажмите карточку, чтобы открыть профиль этого сервиса в вашем пресете."
            self._places_hint.setText(hint)
        for place in details.places:
            card = _PlaceCard(place, self._places_card)
            if place.profile_key:
                card.clicked.connect(lambda key=place.profile_key: self.profile_chosen.emit(key))
            self._places_flow.addWidget(card)

        self._facts_card.clear()
        facts = [
            ("PEOPLE", "Автор", details.author),
            ("TAG", "Пометка каталога", _LABELS.get(details.label, details.label)),
            ("HISTORY", "Прежнее название", details.old_name),
            ("CODE", "Имя в каталоге", details.strategy_id),
        ]
        for icon_name, name, value in facts:
            if value:
                self._facts_card.body.addWidget(_Block(icon_name, name, value, self._facts_card, fill=False))

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
