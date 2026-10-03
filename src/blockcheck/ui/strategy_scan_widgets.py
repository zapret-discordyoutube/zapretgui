"""Виджеты вкладки «Подбор стратегии».

- ``ChoiceTiles`` — выбор «что починить» крупными плитками.
- ``ChoiceRadios`` — выбор тщательности переключателями.
  Оба отвечают тем же набором методов, что и выпадающий список
  (``currentIndex``, ``currentData``, ``findData``, ``setItemText``, сигнал
  ``currentIndexChanged``), поэтому логика страницы от них не зависит.
- ``ScanProgressPanel`` — ход подбора и итог: талисман, шаги, прогресс,
  весёлая строка, счётчик и кнопка «Применить лучшую».
- ``StrategyResultsView`` — найденные стратегии и свёрнутые группы остальных.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    RadioButton,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
)

from blockcheck.ui.check_results import _HeightKeeper, tone_color
from ui.accessibility import enable_keyboard_click, set_control_accessibility, set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.fluent_widgets import SemanticNotice, set_tooltip
from ui.theme import get_cached_qta_pixmap
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.fun import CounterBadge, FunTicker, Mascot, StepList, burst_confetti
from ui.widgets.fun.mascot import MOOD_ALARM, MOOD_BUSY, MOOD_HAPPY, MOOD_IDLE, MOOD_SAD
from ui.widgets.fun.steps import STEP_PENDING
from ui.widgets.motion_icon import MotionIcon
from ui.widgets.stagger_float_in import float_in


def _theme_tokens(tokens=None):
    if tokens is not None:
        return tokens
    try:
        from ui.theme import get_theme_tokens

        return get_theme_tokens()
    except Exception:
        return None


# --- Выбор, похожий на выпадающий список -------------------------------------------


class _ChoiceModel(QWidget):
    """Общая часть: список пунктов с данными и сигнал смены выбора."""

    currentIndexChanged = pyqtSignal(int)  # noqa: N815 (как у ComboBox)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[dict] = []
        self._current = -1

    def addItem(self, text: str, userData=None) -> None:  # noqa: N802
        self._items.append({"text": str(text), "data": userData})
        self._on_item_added(len(self._items) - 1)
        if self._current < 0:
            self.setCurrentIndex(0)

    def count(self) -> int:
        return len(self._items)

    def itemText(self, index: int) -> str:  # noqa: N802
        return self._items[index]["text"] if 0 <= index < len(self._items) else ""

    def itemData(self, index: int):  # noqa: N802
        return self._items[index]["data"] if 0 <= index < len(self._items) else None

    def setItemText(self, index: int, text: str) -> None:  # noqa: N802
        if 0 <= index < len(self._items):
            self._items[index]["text"] = str(text)
            self._on_item_changed(index)

    def currentIndex(self) -> int:  # noqa: N802
        return self._current

    def currentText(self) -> str:  # noqa: N802
        return self.itemText(self._current)

    def currentData(self):  # noqa: N802
        return self.itemData(self._current)

    def findData(self, value) -> int:  # noqa: N802
        for index, item in enumerate(self._items):
            if item["data"] == value:
                return index
        return -1

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not 0 <= index < len(self._items) or index == self._current:
            return
        self._current = index
        self._on_current_changed(index)
        self.currentIndexChanged.emit(index)

    def _on_item_added(self, index: int) -> None: ...
    def _on_item_changed(self, index: int) -> None: ...
    def _on_current_changed(self, index: int) -> None: ...


class _Tile(QWidget):
    clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setMinimumHeight(74)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._selected = False
        self._hover = False
        self._icon_name = "fa5s.circle"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)
        self.icon = MotionIcon(self, size=26)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.title = StrongBodyLabel("", self)
        self.subtitle = CaptionLabel("", self)
        self.subtitle.setWordWrap(True)
        texts.addWidget(self.title)
        texts.addWidget(self.subtitle)
        layout.addLayout(texts, 1)
        enable_keyboard_click(self)
        self._colors = {}
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def click(self) -> None:
        if self.isEnabled():
            self.clicked.emit()

    def set_icon(self, name: str) -> None:
        self._icon_name = name
        self._apply_theme_refresh()

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        self._apply_theme_refresh()
        if selected:
            self.icon.bounce()

    def is_selected(self) -> bool:
        return self._selected

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.click()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        self.update()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        tokens = _theme_tokens(tokens)
        accent = getattr(tokens, "accent_hex", "#60cdff")
        from ui.theme import to_qcolor

        self._colors = {
            "accent": to_qcolor(accent, "#60cdff"),
            "soft": to_qcolor(getattr(tokens, "accent_soft_bg", None), "#2060cdff"),
            "bg": to_qcolor(getattr(tokens, "surface_bg", None), "#10ffffff"),
            "hover": to_qcolor(getattr(tokens, "surface_bg_hover", None), "#18ffffff"),
        }
        color = accent if self._selected else getattr(tokens, "fg_muted", None)
        try:
            self.icon.setPixmap(get_cached_qta_pixmap(self._icon_name, color=color, size=26, muted_fallback=color is None))
        except Exception:
            pass
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._selected:
            fill = QColor(self._colors.get("soft", QColor(96, 205, 255, 40)))
        elif self._hover and self.isEnabled():
            fill = QColor(self._colors.get("hover", QColor(255, 255, 255, 20)))
        else:
            fill = QColor(self._colors.get("bg", QColor(255, 255, 255, 10)))
        if not self.isEnabled():
            fill.setAlphaF(fill.alphaF() * 0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 8, 8)
        if self._selected:
            # Мягкая метка выбора слева вместо рамки (окно без рамок фокуса).
            accent = QColor(self._colors.get("accent", QColor("#60cdff")))
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(rect.left() + 3, rect.top() + 14, 3, rect.height() - 28), 1.5, 1.5)
        painter.end()
        super().paintEvent(event)


class ChoiceTiles(_ChoiceModel):
    """Выбор плитками: у каждого пункта значок, название и пояснение."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)
        self._tiles: list[_Tile] = []
        self._accessible_name = "Что починить"

    def set_item_details(self, index: int, *, subtitle: str, icon: str) -> None:
        if 0 <= index < len(self._tiles):
            self._tiles[index].subtitle.setText(subtitle)
            self._tiles[index].set_icon(icon)
            self._announce()

    def set_accessible_group_name(self, name: str) -> None:
        self._accessible_name = str(name or "")
        self._announce()

    def tiles(self) -> list[_Tile]:
        return list(self._tiles)

    def _on_item_added(self, index: int) -> None:
        tile = _Tile(self)
        tile.title.setText(self.itemText(index))
        tile.clicked.connect(lambda i=index: self.setCurrentIndex(i))
        self._tiles.append(tile)
        self._layout.addWidget(tile, 1)
        self._announce()

    def _on_item_changed(self, index: int) -> None:
        self._tiles[index].title.setText(self.itemText(index))
        self._announce()

    def _on_current_changed(self, index: int) -> None:
        for position, tile in enumerate(self._tiles):
            tile.set_selected(position == index)
        self._announce()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.setCurrentIndex(max(0, self._current - 1))
            return
        if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self.setCurrentIndex(min(self.count() - 1, self._current + 1))
            return
        super().keyPressEvent(event)

    def _announce(self) -> None:
        for position, tile in enumerate(self._tiles):
            state = "выбрано" if position == self._current else "не выбрано"
            text = f"{self._accessible_name}: {tile.title.text()}, {state}"
            set_control_accessibility(tile, name=text, description=tile.subtitle.text())
            set_state_text(tile, text)


class ChoiceRadios(_ChoiceModel):
    """Выбор переключателями в одну строку."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(18)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[RadioButton] = []
        self._layout.addStretch(1)

    def buttons(self) -> list[RadioButton]:
        return list(self._buttons)

    def _on_item_added(self, index: int) -> None:
        button = RadioButton(self.itemText(index), self)
        self._group.addButton(button, index)
        button.toggled.connect(lambda checked, i=index: checked and self.setCurrentIndex(i))
        self._buttons.append(button)
        self._layout.insertWidget(self._layout.count() - 1, button)

    def _on_item_changed(self, index: int) -> None:
        self._buttons[index].setText(self.itemText(index))

    def _on_current_changed(self, index: int) -> None:
        button = self._buttons[index]
        if not button.isChecked():
            button.setChecked(True)


# --- Панель хода и итога -------------------------------------------------------------

SCAN_STEPS = (
    ("network", "Проверяем, есть ли интернет"),
    ("baseline", "Пробуем открыть без обхода"),
    ("control", "Контрольный запуск winws2"),
    ("strategies", "Перебираем стратегии"),
)


class GeoSiteNotice(QWidget):
    """Предупреждение под полем цели: выбран гео-сайт, стратегия его не чинит.

    Гео-сайт сам ограничивает доступ из России. Ему помогают DNS-профиль в
    «Редакторе hosts» или другой DNS — на них и ведут кнопки.
    """

    open_hosts_clicked = pyqtSignal()
    open_dns_clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.notice = SemanticNotice(tone="warning", parent=self)
        layout.addWidget(self.notice)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.hosts_button = PushButton("Открыть «Редактор hosts»", self)
        self.dns_button = PushButton("Настройка DNS", self)
        self.hosts_button.clicked.connect(self.open_hosts_clicked)
        self.dns_button.clicked.connect(self.open_dns_clicked)
        buttons.addWidget(self.hosts_button)
        buttons.addWidget(self.dns_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.set_button_texts(self.hosts_button.text(), self.dns_button.text())

    def set_button_texts(self, hosts_text: str, dns_text: str) -> None:
        self.hosts_button.setText(hosts_text)
        self.dns_button.setText(dns_text)
        set_control_accessibility(
            self.hosts_button,
            name=hosts_text,
            description="Открывает «Редактор hosts», где сервису включается DNS-профиль.",
        )
        set_control_accessibility(
            self.dns_button,
            name=dns_text,
            description="Открывает раздел «Настройка DNS», где меняется DNS-сервер.",
        )

    def set_text(self, text: str) -> None:
        self.notice.setText(text)

    def text(self) -> str:
        return self.notice.text()


class ScanProgressPanel(_HeightKeeper, SimpleCardWidget):
    """Талисман, заголовок, шаги, прогресс, весёлая строка и итог."""

    apply_best_clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(16)

        self.mascot = Mascot(self, size=56)
        root.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(8)
        self.title_label = SubtitleLabel("", self)
        self.title_label.setWordWrap(True)
        body.addWidget(self.title_label)
        # Строка статуса страницы: в покое — подсказка, в конце — итог цифрами.
        self.status_label = CaptionLabel("", self)
        self.status_label.setWordWrap(True)
        body.addWidget(self.status_label)

        self.steps = StepList(self)
        self.steps.reset(list(SCAN_STEPS))
        body.addWidget(self.steps)

        self.progress_bar = ProgressBar(self)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setRange(0, 100)
        body.addWidget(self.progress_bar)

        meta = QHBoxLayout()
        meta.setSpacing(10)
        self.ticker = FunTicker(self)
        meta.addWidget(self.ticker, 1)
        meta.addStretch(0)
        self.found_badge = CounterBadge("надёжно работают", self, tone="success", mark="✓")
        meta.addWidget(self.found_badge, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        body.addLayout(meta)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)
        actions.setSpacing(10)
        self.best_label = BodyLabel("", self)
        self.best_label.setWordWrap(True)
        actions.addWidget(self.best_label, 1)
        self.apply_best_btn = PrimaryPushButton("Применить лучшую", self)
        set_control_accessibility(
            self.apply_best_btn,
            name="Применить лучшую стратегию",
            description="Записывает самую быструю из надёжных стратегий в выбранный пресет.",
        )
        self.apply_best_btn.clicked.connect(self.apply_best_clicked.emit)
        actions.addWidget(self.apply_best_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._actions_host = QWidget(self)
        self._actions_host.setLayout(actions)
        body.addWidget(self._actions_host)

        root.addLayout(body, 1)
        self._state = ""
        self._running = False
        self._happy_timer = QTimer(self)
        self._happy_timer.setSingleShot(True)
        self._happy_timer.timeout.connect(self._back_to_work)
        self.show_idle()

    # --- состояния -------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_min_height()

    def _set_title(self, title: str) -> None:
        self.title_label.setText(title)
        set_state_text(self, f"Подбор стратегии: {title}")
        self._schedule_min_height_sync()

    def show_idle(self, hint: str = "") -> None:
        self._state = "idle"
        self._running = False
        self._happy_timer.stop()
        self.mascot.set_mood(MOOD_IDLE)
        self._set_title("Медоед готов искать обход")
        self.status_label.setText(
            hint
            or "Выберите, что должно заработать, и нажмите «Найти рабочую стратегию». "
            "Сначала проверим сайт без обхода, потом переберём стратегии и каждую удачную перепроверим трижды."
        )
        self.steps.setVisible(False)
        self.progress_bar.setVisible(False)
        self.ticker.stop()
        self.ticker.setVisible(False)
        self.found_badge.setVisible(False)
        self._actions_host.setVisible(False)
        self._schedule_min_height_sync()

    def show_running(self, target: str, subtitle: str = "") -> None:
        self._state = "running"
        self._running = True
        self._set_title(f"Ищем обход для {target}")
        self.status_label.setText(subtitle)
        self.steps.reset(list(SCAN_STEPS))
        self.steps.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.found_badge.set_value(0)
        self.found_badge.setVisible(True)
        self._actions_host.setVisible(False)
        self.ticker.setVisible(True)
        self.mascot.set_mood(MOOD_BUSY)
        self._schedule_min_height_sync()

    def set_phrases(self, phrases) -> None:
        self.ticker.set_phrases(phrases)
        if self._running and not self.ticker.is_running():
            self.ticker.start()

    def set_step(self, step: str, status: str, text: str = "") -> None:
        self.steps.set_step(step, status, text)
        self._schedule_min_height_sync()

    def set_strategy_progress(self, index: int, total: int) -> None:
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(max(0, min(total, index)))

    def note_found(self, count: int) -> None:
        """Нашлась ещё одна надёжная стратегия: счётчик подпрыгивает, медоед радуется."""
        self.found_badge.set_value(count)
        if self._running:
            self.mascot.set_mood(MOOD_HAPPY)
            self._happy_timer.start(1300)

    def _back_to_work(self) -> None:
        if self._running:
            self.mascot.set_mood(MOOD_BUSY)

    def show_outcome(self, *, kind: str, title: str, detail: str, best_text: str = "", celebrate: bool = False) -> None:
        self._state = kind
        self._running = False
        self._happy_timer.stop()
        self.ticker.stop()
        self.ticker.setVisible(False)
        self.progress_bar.setVisible(False)
        # Число найденных уже в заголовке итога.
        self.found_badge.setVisible(False)
        # Незаконченные шаги больше не «идут».
        for key in self.steps.keys():
            if self.steps.status(key) == "running":
                self.steps.set_step(key, STEP_PENDING)
        self._set_title(title)
        if detail:
            self.status_label.setText(detail)
        self.best_label.setText(best_text)
        self._actions_host.setVisible(bool(best_text))
        mood = {
            "found": MOOD_HAPPY,
            "not_found": MOOD_SAD,
            "open": MOOD_IDLE,
            "cancelled": MOOD_IDLE,
        }.get(kind, MOOD_ALARM)
        self.mascot.set_mood(mood)
        if celebrate:
            burst_confetti(self)
        self._schedule_min_height_sync()


# --- Результаты ----------------------------------------------------------------------

_VERDICT_ICONS = {
    "working": ("fa5s.check-circle", "success"),
    "unstable": ("fa5s.adjust", "warning"),
    "failed": ("fa5s.times-circle", "error"),
    "crash": ("fa5s.bug", "warning"),
    "not_counted": ("fa5s.question-circle", "muted"),
}
RESULT_GROUPS = (
    ("unstable", "Нестабильные — открывались не каждый раз"),
    ("not_counted", "Не засчитаны — сеть вела себя странно"),
    ("crash", "Сбой winws2 — стратегия не запустилась"),
    ("failed", "Не сработали"),
)


class _ResultRow(QWidget):
    def __init__(self, presentation, verdict: str, on_apply: Callable[[], None] | None, parent=None) -> None:
        super().__init__(parent)
        self._verdict = verdict
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(10)
        self.icon = QLabel(self)
        self.icon.setFixedSize(18, 18)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.name_label = BodyLabel(presentation.strategy_name, self)
        set_tooltip(self.name_label, presentation.strategy_tooltip)
        layout.addWidget(self.name_label, 1)
        # У найденных — «Работает 3/3», у остальных — сразу причина словами.
        status_text = presentation.status_text
        if verdict != "working" and presentation.status_tooltip and presentation.status_tooltip != status_text:
            status_text = presentation.status_tooltip.split(": ", 1)[-1] if verdict == "unstable" else presentation.status_tooltip
            status_text = f"{presentation.status_text} · {status_text}" if verdict == "unstable" else status_text
        self.status_label = CaptionLabel(status_text, self)
        set_tooltip(self.status_label, presentation.status_tooltip)
        layout.addWidget(self.status_label, 0)
        self.time_label = CaptionLabel(
            f"{presentation.time_text} мс" if presentation.time_text not in ("", "—") else "",
            self,
        )
        self.time_label.setMinimumWidth(56)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.time_label, 0)
        self.apply_button = None
        if on_apply is not None:
            button = PushButton("Применить", self)
            button.setFixedHeight(28)
            set_control_accessibility(
                button,
                name=f"Применить стратегию {presentation.strategy_name}",
                description="Записывает проверенную стратегию в выбранный пресет.",
            )
            button.clicked.connect(lambda _checked=False: on_apply())
            layout.addWidget(button, 0)
            self.apply_button = button
        text = (
            f"Строка {presentation.number_text}. Стратегия {presentation.strategy_name}, "
            f"статус {presentation.status_text}, время {presentation.time_text or '-'}"
        )
        if on_apply is not None:
            text += ". Доступно действие: применить"
        set_state_text(self, text + ".")
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        icon_name, tone = _VERDICT_ICONS.get(self._verdict, _VERDICT_ICONS["failed"])
        color = tone_color(tone, tokens) or None
        try:
            self.icon.setPixmap(get_cached_qta_pixmap(icon_name, color=color, size=16, muted_fallback=color is None))
        except Exception:
            pass


class _GroupHeader(QWidget):
    clicked = pyqtSignal()

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)
        self.chevron = QLabel(self)
        self.chevron.setFixedSize(14, 14)
        layout.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = StrongBodyLabel(text, self)
        layout.addWidget(self.label, 1)
        self._expanded = False
        enable_keyboard_click(self)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def click(self) -> None:
        self.clicked.emit()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.click()
        super().mouseReleaseEvent(event)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        tokens = _theme_tokens(tokens)
        name = "fa5s.chevron-down" if self._expanded else "fa5s.chevron-right"
        color = getattr(tokens, "fg_muted", None)
        try:
            self.chevron.setPixmap(get_cached_qta_pixmap(name, color=color, size=12, muted_fallback=color is None))
        except Exception:
            pass


class _ResultGroup(QWidget):
    def __init__(self, title: str, *, collapsible: bool, parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._collapsible = collapsible
        self._expanded = not collapsible
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.header = _GroupHeader(title, self)
        self.header.clicked.connect(self.toggle)
        layout.addWidget(self.header)
        self.rows_host = QWidget(self)
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(22 if collapsible else 0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        layout.addWidget(self.rows_host)
        self.rows_host.setVisible(self._expanded)
        self.header.set_expanded(self._expanded)
        self.setVisible(False)
        self._refresh_header()

    def rows(self) -> list[_ResultRow]:
        return [
            self.rows_layout.itemAt(i).widget()
            for i in range(self.rows_layout.count())
            if isinstance(self.rows_layout.itemAt(i).widget(), _ResultRow)
        ]

    def is_expanded(self) -> bool:
        return self._expanded

    def toggle(self) -> None:
        if not self._collapsible:
            return
        self._expanded = not self._expanded
        self.rows_host.setVisible(self._expanded)
        self.header.set_expanded(self._expanded)
        self._refresh_header()

    def add_row(self, row: _ResultRow) -> None:
        self.rows_layout.addWidget(row)
        self.setVisible(True)
        self._refresh_header()
        if self._expanded and are_live_animations_enabled():
            float_in(row)

    def clear(self) -> None:
        for row in self.rows():
            self.rows_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.setVisible(False)
        self._refresh_header()

    def _refresh_header(self) -> None:
        count = self.rows_layout.count()
        self.header.label.setText(f"{self._title} ({count})")
        state = "раскрыт" if self._expanded else "свёрнут"
        set_control_accessibility(self.header, name=f"{self._title}: {count}, список {state}", description="")
        set_state_text(self.header, f"{self._title}: {count}, список {state}")


class StrategyResultsView(QWidget):
    """Найденные стратегии сверху, остальные — в свёрнутых группах."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.working_group = _ResultGroup("Надёжно работают", collapsible=False, parent=self)
        layout.addWidget(self.working_group)
        self.groups: dict[str, _ResultGroup] = {"working": self.working_group}
        for key, title in RESULT_GROUPS:
            group = _ResultGroup(title, collapsible=True, parent=self)
            layout.addWidget(group)
            self.groups[key] = group
        set_state_text(self, "Результаты подбора стратегии: пока нет результатов")

    def clear(self) -> None:
        for group in self.groups.values():
            group.clear()
        set_state_text(self, "Результаты подбора стратегии: пока нет результатов")

    def row_count(self) -> int:
        return sum(len(group.rows()) for group in self.groups.values())

    def add_result(self, presentation, verdict: str, on_apply: Callable[[], None] | None) -> _ResultRow:
        key = verdict if verdict in self.groups else "failed"
        group = self.groups[key]
        row = _ResultRow(presentation, key, on_apply if key == "working" else None, group.rows_host)
        group.add_row(row)
        working = len(self.working_group.rows())
        set_state_text(
            self,
            f"Результаты подбора стратегии: проверено {self.row_count()}, надёжно работают {working}",
        )
        return row


__all__ = [
    "ChoiceRadios",
    "ChoiceTiles",
    "RESULT_GROUPS",
    "SCAN_STEPS",
    "ScanProgressPanel",
    "StrategyResultsView",
]
