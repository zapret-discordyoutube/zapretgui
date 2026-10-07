"""Карточки результата BlockCheck: сетка, ход проверки и подробный вид.

Что показывать, решает ``result_cards_model`` (чистые данные). Здесь только
рисование:

- ``ResultCard`` — карточка: логотип сайта в фирменном цвете, название,
  под ним слово результата, строки измерений, метки. Нажатие открывает
  подробности;
- ``CardsGrid`` — сетка: сама решает, сколько колонок помещается в ширину
  окна, и ставит карточку в самую короткую колонку, чтобы на широком экране
  не оставалось пустот;
- ``HostingDots`` — по точке на каждый сервер хостинга, по провайдерам;
- ``CountersStrip`` — «что проверено»: числа досчитывают от нуля;
- ``ProgressSteps`` — ход проверки по шагам, пока она идёт;
- ``ResultDetailView`` — подробности одной карточки на всю страницу.

Рамок нет: окно программы без рамок. Подложка у всех карточек одна,
нейтральная; состояние показывают цветная точка и слово результата.
Анимации короткие и подчиняются переключателю «живых анимаций».
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PyQt6.QtCore import QEasingCurve, QEvent, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter
from PyQt6.QtWidgets import QAbstractScrollArea, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    BreadcrumbBar,
    CaptionLabel,
    FlowLayout,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
)

from blockcheck.ui.block_kinds_view import kind_color
from blockcheck.ui.brand_icons import BrandIcon, named_brand, site_brand
from blockcheck.ui.finding_parts import CardsFlow, FindingCard, split_finding, split_server_list
from blockcheck.ui.server_matrix import ServerMatrix, parse_server_table
from blockcheck.ui.result_cards_model import PREVIEW_LINES, Card, Counter, DotGroup, Line, Section, build_cards, build_counters
from ui.accessibility import set_breadcrumb_accessibility, set_control_accessibility, set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.code_editor.chunked_fill import ChunkedReadOnlyFill
from ui.code_editor.editor import CodeEditor
from ui.code_editor.log_syntax import LogSyntaxHighlighter
from ui.fluent_widgets import set_tooltip
from ui.theme import get_cached_qta_pixmap
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.elided_label import ElidedLabel as _ElidedLabel
from ui.widgets.hover_hint import HoverHint
from ui.widgets.share_bar import ShareBar
from ui.widgets.stagger_float_in import float_in
from ui.widgets.tone_group import ToneDot, dot_on_first_line, mute

CARD_RADIUS = 8
GRID_GAP = 10
REVEAL_MS = 700

_STATE_ICONS = {
    "ok": "fa5s.check-circle",
    "warn": "fa5s.exclamation-triangle",
    "fail": "fa5s.times-circle",
    "unknown": "fa5s.question-circle",
    "info": "fa5s.circle",
}
# (тёмная тема, светлая тема)
_STATE_COLORS = {
    "ok": ("#5bb974", "#1a7f37"),
    "warn": ("#d99a4e", "#955800"),
    "fail": ("#e5645d", "#b3261e"),
    "unknown": ("#9aa0aa", "#5f6470"),
    "info": ("#8f96a3", "#6b7180"),
}


def _is_light(tokens=None) -> bool:
    if tokens is None:
        try:
            from ui.theme import get_theme_tokens

            tokens = get_theme_tokens()
        except Exception:
            return False
    return bool(getattr(tokens, "is_light", False))


def state_color(state: str, tokens=None) -> str:
    dark, light = _STATE_COLORS.get(state) or _STATE_COLORS["unknown"]
    return light if _is_light(tokens) else dark


def card_color(card: Card, tokens=None) -> str:
    """Цвет карточки: у вида блокировки свой, иначе — по уровню."""
    return kind_color(card.kind, tokens) if card.kind else state_color(card.level, tokens)


# Состояния, у которых точка — кольцо, а не закрашенный круг: так «предупреждение»
# и «нет ответа» отличаются от «ошибки» и «работает» не только цветом.
_HOLLOW_STATES = frozenset({"warn", "unknown", "info"})
# Метка говорит о проблеме — её текст в цвете состояния; остальные метки приглушены.
_LOUD_CHIPS = frozenset({"warn", "fail"})


def card_hint(card: Card) -> str:
    """Подсказка под мышью: все строки карточки, включая те, что на ней не поместились."""
    lines = [f"{card.title} — {card.status}"]
    lines += [f"{line.name}: {line.text}" if line.text else line.name for line in card.lines]
    if card.chips:
        lines.append(" · ".join(text for text, _state in card.chips))
    lines.append("Нажмите, чтобы открыть полный отчёт")
    return "\n".join(lines)


def _chip_style(text_color: str, tokens=None) -> str:
    light = _is_light(tokens)
    back = "rgba(0, 0, 0, 0.05)" if light else "rgba(255, 255, 255, 0.06)"
    return f"QLabel {{ color: {text_color}; background-color: {back}; border-radius: 4px; padding: 0px 7px; }}"


def _muted_text(tokens=None) -> str:
    return "rgba(0, 0, 0, 0.62)" if _is_light(tokens) else "rgba(255, 255, 255, 0.62)"


class _StateIcon(QLabel):
    """Значок состояния строки, в цвете состояния."""

    def __init__(self, state: str, parent=None, *, size: int = 12, icon: str = "") -> None:
        super().__init__(parent)
        self._state = state
        self._size = size
        self._icon = icon
        self._color_override = ""
        self.setFixedSize(size + 2, size + 2)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def set_color(self, color: str) -> None:
        self._color_override = color
        self._apply_theme_refresh()

    def set_icon(self, icon: str, color: str = "") -> None:
        self._icon = icon
        self._color_override = color
        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        color = self._color_override or state_color(self._state, tokens)
        try:
            self.setPixmap(
                get_cached_qta_pixmap(self._icon or _STATE_ICONS.get(self._state, _STATE_ICONS["unknown"]), color=color, size=self._size)
            )
        except Exception:
            pass


class _LineRow(QWidget):
    """Строка измерения: значок, что проверяли и что получилось."""

    def __init__(self, line: Line, parent=None, *, wrap: bool = False) -> None:
        super().__init__(parent)
        self.line = line
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        state = line.state
        dot = ToneDot(lambda tokens: state_color(state, tokens), self, size=7, hollow=state in _HOLLOW_STATES)
        if wrap:
            layout.addWidget(dot_on_first_line(dot), 0, Qt.AlignmentFlag.AlignTop)
        else:
            layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        if wrap:
            # Подробный вид: текст переносится, название — жирнее.
            # Строка без пояснения — обычный текст (совет, описание), с пояснением — «что: как».
            text = f"<b>{_escape(line.name)}</b> — {_escape(line.text)}" if line.text else _escape(line.name)
            label = BodyLabel(text, self)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label, 1)
        else:
            self.name_label = _ElidedLabel(line.name, self)
            if line.text:
                # Название — целиком, сколько есть места; сокращается пояснение справа.
                self.name_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                layout.addWidget(self.name_label, 0)
                self.text_label = mute(_ElidedLabel(line.text, self, align=Qt.AlignmentFlag.AlignRight))
                layout.addWidget(self.text_label, 1)
            else:
                layout.addWidget(self.name_label, 1)
        set_state_text(self, f"{line.name}: {line.text}" if line.text else line.name)


def _escape(text: str) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class HostingDots(QWidget):
    """Провайдеры и по точке на каждый их сервер. Точки проявляются слева направо."""

    DOT = 9
    DOT_GAP = 4
    ROW = 22
    GROUP_GAP = 18

    def __init__(self, groups: tuple[DotGroup, ...], parent=None) -> None:
        super().__init__(parent)
        self._groups = tuple(groups)
        self._reveal = 1.0
        # Прямоугольники точек с подсказками — для наведения мыши.
        self._hits: list[tuple[QRectF, str]] = []
        self._hint = HoverHint(self, delay_ms=0)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(REVEAL_MS + 300)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_value)
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())
        total = sum(len(group.states) for group in self._groups)
        cut = sum(1 for group in self._groups for state in group.states if state == "fail")
        set_state_text(self, f"Серверов хостингов: {total}, с обрывом: {cut}")

    def groups(self) -> tuple[DotGroup, ...]:
        return self._groups

    def play(self) -> None:
        self._anim.stop()
        if are_live_animations_enabled() and self._groups:
            self._reveal = 0.0
            self._anim.start()
        else:
            self._reveal = 1.0
        self.update()

    def _on_value(self, value) -> None:
        self._reveal = float(value)
        self.update()

    def _placement(self, width: int) -> tuple[list[tuple[DotGroup, float, float, float]], int]:
        """Где стоит каждая группа: (группа, x, y, ширина названия) и общая высота."""
        metrics = QFontMetrics(self.font())
        placed: list[tuple[DotGroup, float, float, float]] = []
        x, y = 0.0, 0.0
        for group in self._groups:
            name_width = metrics.horizontalAdvance(group.name) + 8
            group_width = name_width + len(group.states) * (self.DOT + self.DOT_GAP)
            if x > 0 and x + group_width > width:
                x, y = 0.0, y + self.ROW
            placed.append((group, x, y, name_width))
            x += group_width + self.GROUP_GAP
        return placed, int(y + self.ROW) if self._groups else 0

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._placement(max(1, width))[1]

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, self.heightForWidth(max(320, self.width())))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        height = self.heightForWidth(self.width())
        if height != self.minimumHeight():
            self.setMinimumHeight(height)
            self.setMaximumHeight(height)

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        placed, _height = self._placement(self.width())
        total = sum(len(group.states) for group in self._groups)
        visible = int(round(total * self._reveal))
        text_color = QColor(state_color("info"))
        self._hits = []
        index = 0
        for group, x, y, name_width in placed:
            painter.setPen(text_color)
            painter.drawText(
                QRectF(x, y, name_width, self.ROW),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                group.name,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            dot_x = x + name_width
            for position, state in enumerate(group.states):
                rect = QRectF(dot_x, y + (self.ROW - self.DOT) / 2, self.DOT, self.DOT)
                color = QColor(state_color(state))
                if index >= visible:
                    color.setAlphaF(0.12)
                painter.setBrush(color)
                painter.drawRoundedRect(rect, 3, 3)
                hint = group.hints[position] if position < len(group.hints) else group.name
                self._hits.append((rect.adjusted(-2, -4, 2, 4), hint))
                dot_x += self.DOT + self.DOT_GAP
                index += 1
        painter.end()

    def hint_at(self, x: float, y: float) -> str:
        for rect, hint in self._hits:
            if rect.contains(x, y):
                return hint
        return ""

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            # Своя подсказка вместо системной: та на тёмной теме мелькает белым окном.
            self._hint.show(self.hint_at(event.pos().x(), event.pos().y()), event.globalPos())
            return True
        if event.type() == QEvent.Type.Leave:
            self._hint.hide()
        return super().event(event)


class ResultCard(QWidget):
    """Карточка одной проверки. Нажатие (или Enter) открывает подробности."""

    opened = pyqtSignal(object)

    def __init__(self, card: Card, parent=None) -> None:
        super().__init__(parent)
        self.card = card
        self._color = QColor()
        self._hover = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

        self._surface = QColor(255, 255, 255, 10)
        self._surface_hover = QColor(255, 255, 255, 18)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(11)
        # Логотип сайта — в фирменном цвете; у остальных проверок значок нейтральный.
        brand = site_brand(card.key.removeprefix("site:"), card.title) if card.site else None
        self._icon = BrandIcon(brand.icon if brand else card.icon, brand.color if brand else "", self, size=22)
        header.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        # Слово результата стоит под названием, а не рядом: так оба текста видны целиком.
        titles = QVBoxLayout()
        titles.setSpacing(1)
        self.title_label = _ElidedLabel(card.title, self, strong=True)
        titles.addWidget(self.title_label)
        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self._status_dot = ToneDot(
            lambda tokens: card_color(card, tokens), self, size=7, hollow=card.level in _HOLLOW_STATES
        )
        status_row.addWidget(self._status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.status_label = _ElidedLabel(card.status, self)
        status_row.addWidget(self.status_label, 1, Qt.AlignmentFlag.AlignVCenter)
        titles.addLayout(status_row)
        header.addLayout(titles, 1)
        root.addLayout(header)
        root.addSpacing(2)

        self.rows = [_LineRow(line, self) for line in card.lines[:PREVIEW_LINES]]
        for row in self.rows:
            root.addWidget(row)
        self.more_label: CaptionLabel | None = None
        hidden = len(card.lines) - len(self.rows)
        if hidden > 0:
            self.more_label = mute(CaptionLabel(f"и ещё {hidden} — нажмите, чтобы увидеть всё", self))
            root.addWidget(self.more_label)

        self.dots: HostingDots | None = None
        if card.dots:
            self.dots = HostingDots(card.dots, self)
            root.addWidget(self.dots)

        self.chip_labels: list[QLabel] = []
        if card.chips:
            chips = QWidget(self)
            flow = FlowLayout(chips, needAni=False)
            flow.setContentsMargins(0, 2, 0, 0)
            flow.setHorizontalSpacing(6)
            flow.setVerticalSpacing(4)
            for text, state in card.chips:
                chip = QLabel(text, chips)
                chip.setFixedHeight(20)
                chip.setProperty("chipState", state)
                flow.addWidget(chip)
                self.chip_labels.append(chip)
            root.addWidget(chips)
        root.addStretch(1)

        set_control_accessibility(
            self,
            name=f"{card.title}: {card.status}",
            description="Нажмите, чтобы открыть все измерения этой проверки.",
        )
        set_state_text(self, f"{card.title}: {card.status}")
        set_tooltip(self, card_hint(card))
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def play(self, delay_ms: int = 0) -> None:
        """Карточка выплывает, а точки хостингов проявляются."""
        float_in(self, delay_ms=delay_ms)
        if self.dots is not None:
            self.dots.play()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        self._color = QColor(card_color(self.card, tokens))
        self.status_label.setTextColor(self._color, self._color)
        for chip in self.chip_labels:
            state = str(chip.property("chipState") or "info")
            chip.setStyleSheet(
                _chip_style(state_color(state, tokens) if state in _LOUD_CHIPS else _muted_text(tokens), tokens)
            )
        try:
            from ui.theme import get_theme_tokens, to_qcolor

            tokens = tokens or get_theme_tokens()
            self._surface = to_qcolor(tokens.surface_bg, "#0affffff")
            self._surface_hover = to_qcolor(tokens.surface_bg_hover, "#12ffffff")
        except Exception:
            pass
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._surface_hover if self._hover or self.hasFocus() else self._surface)
        painter.drawRoundedRect(self.rect(), CARD_RADIUS, CARD_RADIUS)
        painter.end()

    def event(self, event) -> bool:
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self._hover = event.type() == QEvent.Type.HoverEnter
            self.update()
        return super().event(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.opened.emit(self.card)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.opened.emit(self.card)
            return
        super().keyPressEvent(event)


class CardsGrid(QWidget):
    """Сетка карточек: колонок столько, сколько помещается в ширину.

    Карточка ставится в самую короткую колонку, широкая (``wide``) занимает
    весь ряд. Высота сетки считается здесь же: вложенная прокрутка страницы
    сама её не узнала бы.
    """

    opened = pyqtSignal(object)

    def __init__(self, min_card_width: int = 280, parent=None) -> None:
        super().__init__(parent)
        self._min_card_width = int(min_card_width)
        self._cards: list[ResultCard] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def cards(self) -> list[ResultCard]:
        return list(self._cards)

    def columns_for(self, width: int) -> int:
        return max(1, (int(width) + GRID_GAP) // (self._min_card_width + GRID_GAP))

    def clear(self) -> None:
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards = []
        self.setFixedHeight(0)

    def show_cards(self, cards: list[Card], *, animate: bool = True, first_delay_ms: int = 0) -> None:
        self.clear()
        for card in cards:
            widget = ResultCard(card, self)
            widget.opened.connect(self.opened)
            widget.show()
            self._cards.append(widget)
        self._place()
        if animate:
            # Выплывают первые карточки; остальные появляются сразу — анимация каждой
            # из десятков карточек делала показ итога тяжёлым.
            for order, widget in enumerate(self._cards[:ANIMATED_CARDS]):
                widget.play(first_delay_ms + order * 55)
            for widget in self._cards[ANIMATED_CARDS:]:
                if widget.dots is not None:
                    widget.dots.play()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place()

    def _place(self) -> None:
        width = self.width()
        if width <= 0 or not self._cards:
            return
        columns = self.columns_for(width)
        column_width = (width - GRID_GAP * (columns - 1)) // columns
        heights = [0] * columns
        for widget in self._cards:
            if widget.card.wide:
                top = max(heights)
                height = self._card_height(widget, width)
                widget.setGeometry(QRect(0, top, width, height))
                heights = [top + height + GRID_GAP] * columns
                continue
            column = heights.index(min(heights))
            height = self._card_height(widget, column_width)
            widget.setGeometry(QRect(column * (column_width + GRID_GAP), heights[column], column_width, height))
            heights[column] += height + GRID_GAP
        total = max(0, max(heights) - GRID_GAP)
        if total != self.height():
            self.setFixedHeight(total)

    @staticmethod
    def _card_height(widget: ResultCard, width: int) -> int:
        layout = widget.layout()
        if layout is not None and layout.hasHeightForWidth():
            return max(layout.totalHeightForWidth(width), layout.totalMinimumSize().height())
        return widget.sizeHint().height()


class _CounterTile(QWidget):
    """Число и подпись: «59 серверов хостингов». Число досчитывает от нуля."""

    def __init__(self, counter: Counter, parent=None) -> None:
        super().__init__(parent)
        self.counter = counter
        self._shown = counter.value
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 12, 4)
        layout.setSpacing(7)
        layout.addWidget(_StateIcon("info", self, size=13, icon=counter.icon), 0, Qt.AlignmentFlag.AlignVCenter)
        self.value_label = StrongBodyLabel(str(counter.value), self)
        layout.addWidget(self.value_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(mute(CaptionLabel(counter.caption, self)), 0, Qt.AlignmentFlag.AlignVCenter)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(REVEAL_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_value)
        set_state_text(self, f"{counter.caption}: {counter.value}")
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())

    def play(self) -> None:
        self._anim.stop()
        if are_live_animations_enabled():
            self._anim.start()
        else:
            self.value_label.setText(str(self.counter.value))

    def _on_value(self, value) -> None:
        self.value_label.setText(str(int(round(self.counter.value * float(value)))))

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 14) if _is_light() else QColor(255, 255, 255, 13))
        painter.drawRoundedRect(self.rect(), 6, 6)
        painter.end()


class CountersStrip(QWidget):
    """«Что проверено» — ряд чисел над карточками."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._flow = FlowLayout(self, needAni=False)
        self._flow.setContentsMargins(0, 0, 0, 0)
        self._flow.setHorizontalSpacing(8)
        self._flow.setVerticalSpacing(8)
        self._tiles: list[_CounterTile] = []

    def tiles(self) -> list[_CounterTile]:
        return list(self._tiles)

    def show_counters(self, counters: list[Counter], *, animate: bool = True) -> None:
        self._flow.takeAllWidgets()
        for tile in self._tiles:
            tile.setParent(None)
            tile.deleteLater()
        self._tiles = []
        for counter in counters:
            tile = _CounterTile(counter, self)
            self._flow.addWidget(tile)
            self._tiles.append(tile)
            if animate:
                tile.play()
        set_state_text(
            self, "Что проверено: " + ", ".join(f"{counter.value} {counter.caption}" for counter in counters)
        )
        self._sync_height()
        # Раскладка узнаёт ширину плиток только после первого прохода событий.
        QTimer.singleShot(0, self._sync_height)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self) -> None:
        if self.width() <= 0:
            return
        height = self._flow.heightForWidth(self.width()) if self._tiles else 0
        if height != self.minimumHeight() or height != self.maximumHeight():
            self.setFixedHeight(height)


class ResultCardsView(QWidget):
    """Всё под итогом: что проверено, карточки сайтов и карточки остальных проверок."""

    opened = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.counters = CountersStrip(self)
        layout.addWidget(self.counters)
        self.sites_title = StrongBodyLabel("Сайты", self)
        layout.addWidget(self.sites_title)
        self.sites_grid = CardsGrid(250, self)
        layout.addWidget(self.sites_grid)
        self.checks_title = StrongBodyLabel("Сеть и компьютер", self)
        layout.addWidget(self.checks_title)
        self.checks_grid = CardsGrid(330, self)
        layout.addWidget(self.checks_grid)
        self.sites_grid.opened.connect(self.opened)
        self.checks_grid.opened.connect(self.opened)
        set_control_accessibility(
            self,
            name="Результаты BlockCheck",
            description="Карточка на каждый сайт и на каждую проверку. Нажатие на карточку открывает все измерения.",
        )
        set_state_text(self, "Результаты BlockCheck: пока нет результатов")

    def cards(self) -> list[ResultCard]:
        return self.sites_grid.cards() + self.checks_grid.cards()

    def card(self, key: str) -> ResultCard | None:
        return next((widget for widget in self.cards() if widget.card.key == key), None)

    def clear(self) -> None:
        self.sites_grid.clear()
        self.checks_grid.clear()
        self.counters.show_counters([], animate=False)
        set_state_text(self, "Результаты BlockCheck: пока нет результатов")

    def show_report(self, report: dict, *, animate: bool = True) -> None:
        cards = build_cards(report)
        sites = [card for card in cards if card.site]
        checks = [card for card in cards if not card.site]
        self.counters.show_counters(build_counters(report), animate=animate)
        self.sites_title.setVisible(bool(sites))
        self.checks_title.setVisible(bool(checks))
        self.sites_grid.show_cards(sites, animate=animate)
        # Широкая карточка занимает весь ряд: стоя посреди списка, она оставляла перед собой
        # ряд с одной карточкой и пустотой. Широкие идут первыми, остальные заполняют ряды подряд.
        checks = sorted(checks, key=lambda card: not card.wide)
        self.checks_grid.show_cards(checks, animate=animate, first_delay_ms=200)
        broken = sum(1 for card in cards if card.level in ("fail", "warn"))
        set_state_text(self, f"Результаты BlockCheck: карточек {len(cards)}, с проблемами {broken}")


# ---------------------------------------------------------------------------
# Ход проверки
# ---------------------------------------------------------------------------

_STEP_TITLES = {
    "sites": ("fa5s.globe", "Сайты: соединение, способ блокировки, QUIC"),
    "hostings": ("fa5s.server", "Зарубежные хостинги: обрыв загрузки и отправки"),
    "voice": ("fa5s.phone-alt", "Голосовые серверы"),
    "ipv6": ("fa5s.project-diagram", "IPv6"),
    "system": ("fa5s.desktop", "Этот компьютер"),
    "dns_servers": ("fa5s.network-wired", "DNS-серверы: UDP, TCP, DoT, DoH"),
    "filter": ("fa5s.route", "Место фильтра"),
}


class _StepRow(QWidget):
    def __init__(self, key: str, parent=None) -> None:
        super().__init__(parent)
        self.key = key
        icon, title = _STEP_TITLES[key]
        self._title = title
        self.done = 0
        self.total = 0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._icon = _StateIcon("info", self, size=13, icon=icon)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = CaptionLabel(title, self)
        self.title_label.setFixedWidth(360)
        layout.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.bar = ProgressBar(self)
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        layout.addWidget(self.bar, 1, Qt.AlignmentFlag.AlignVCenter)
        self.count_label = CaptionLabel("ждёт", self)
        self.count_label.setMinimumWidth(64)
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignVCenter)

    def reset(self) -> None:
        self.done = self.total = 0
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.count_label.setText("ждёт")
        self._icon.set_color("")
        set_state_text(self, f"{self._title}: ждёт")

    def set_progress(self, done: int, total: int) -> None:
        self.done, self.total = int(done), max(1, int(total))
        self.bar.setRange(0, self.total)
        self.bar.setValue(min(self.done, self.total))
        finished = self.done >= self.total
        if self.total == 1:
            text = "готово" if finished else "идёт"
        else:
            text = f"{self.done} / {self.total}"
        self.count_label.setText(text)
        self._icon.set_color(state_color("ok") if finished else "")
        set_state_text(self, f"{self._title}: {text}")


class ProgressSteps(QWidget):
    """Шаги проверки с полосами: видно, что именно сейчас проверяется и сколько осталось."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(5)
        self.rows = {key: _StepRow(key, self) for key in _STEP_TITLES}
        for row in self.rows.values():
            layout.addWidget(row)
        set_state_text(self, "Ход BlockCheck: не выполняется")

    def start(self, steps) -> None:
        wanted = set(steps)
        for key, row in self.rows.items():
            row.reset()
            row.setVisible(key in wanted)
        set_state_text(self, "Ход BlockCheck: проверка началась")

    def set_progress(self, step: str, done: int, total: int) -> None:
        row = self.rows.get(str(step))
        if row is None:
            return
        row.set_progress(done, total)
        ready = sum(1 for item in self.rows.values() if not item.isHidden() and item.total and item.done >= item.total)
        shown = sum(1 for item in self.rows.values() if not item.isHidden())
        set_state_text(self, f"Ход BlockCheck: готово шагов {ready} из {shown}")


# ---------------------------------------------------------------------------
# Подробности одной карточки
# ---------------------------------------------------------------------------


def card_plain_text(card: Card) -> str:
    """Подробности карточки обычным текстом — для буфера обмена."""
    marks = {"ok": "✓", "warn": "!", "fail": "✗", "unknown": "?", "info": "·"}
    lines = [f"{card.title}: {card.status}", ""]
    for section in card.sections:
        lines.append(section.title)
        for line in section.lines:
            lines.append(f"  {marks.get(line.state, '·')} {line.name}" + (f" — {line.text}" if line.text else ""))
        if section.text:
            lines.append(section.text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# Значок строки отчёта — по тому, что измеряли. Цвет значка — состояние строки.
_LINE_ICONS = {
    "Соединение": "fa5s.plug",
    "Адрес сервера": "fa5s.map-marker-alt",
    "Как блокируют": "fa5s.ban",
    "QUIC (UDP 443)": "fa5s.bolt",
    "Объём загрузки": "fa5s.download",
    "DNS": "fa5s.network-wired",
    "Заметка": "fa5s.sticky-note",
    "Провайдер": "fa5s.building",
    "Адрес": "fa5s.link",
    "Сервер": "fa5s.server",
    "Время проверки": "fa5s.clock",
}
_ADVICE_TITLE = "Что делать"
_ABOUT_TITLE = "Что это за проверка"
# Состояния, которые считаются в сводке отчёта, и подписи к их числам.
_TALLY = (("ok", "в порядке"), ("fail", "с проблемой"), ("warn", "с замечанием"), ("unknown", "нет ответа"))
# Сколько первых карточек сетки появляется с анимацией.
ANIMATED_CARDS = 8
# Сколько строк длинного текста видно в отчёте без прокрутки; остальное — внутри редактора.
TEXT_PREVIEW_LINES = 16
# Сколько первых блоков отчёта появляется с анимацией.
ANIMATED_BLOCKS = 4
NAME_COLUMN_MIN = 120
NAME_COLUMN_MAX = 320


def line_icon(line: Line, section: Section) -> str:
    """Значок строки отчёта. Пусто — у строки точка состояния."""
    if section.title == _ADVICE_TITLE:
        return "fa5s.arrow-right"
    if line.name.startswith("TLS"):
        return "fa5s.lock"
    if line.name.startswith("HTTP"):
        return "fa5s.unlock-alt"
    return _LINE_ICONS.get(line.name, "")


def section_icon(section: Section, card: Card) -> tuple[str, str]:
    """(значок, фирменный цвет) раздела отчёта. Цвет пустой — значок нейтральный."""
    if section.title == _ADVICE_TITLE:
        return "fa5s.lightbulb", ""
    if section.title in (_ABOUT_TITLE, "Что это значит"):
        return "fa5s.info-circle", ""
    if section.title == "Результат":
        return "fa5s.clipboard-check", ""
    if section.text and not section.lines:
        return "fa5s.file-alt", ""
    head = section.title.split(" — ")[0]
    if card.site:
        # Раздел сайта — один его адрес: «www.youtube.com — сайт».
        return ("fa5s.link", "") if "." in head else ("fa5s.shield-alt", "")
    brand = named_brand(head)
    if brand is not None:
        return brand.icon, brand.color
    return "fa5s.list-ul", ""


def tally(lines) -> dict[str, int]:
    """Сколько строк в каком состоянии. Справочные строки (info) не считаются."""
    counts = {state: 0 for state, _caption in _TALLY}
    for line in lines:
        if line.state in counts:
            counts[line.state] += 1
    return {state: count for state, count in counts.items() if count}


class _CountLabel(StrongBodyLabel):
    """Число, которое досчитывает от нуля до своего значения."""

    def __init__(self, value: int, parent=None, *, pixel_size: int = 22) -> None:
        super().__init__(parent)
        self.setText(str(value))
        self._value = int(value)
        font = self.font()
        font.setPixelSize(pixel_size)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(REVEAL_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda share: self.setText(str(int(round(self._value * float(share))))))

    def value(self) -> int:
        return self._value

    def play(self) -> None:
        if are_live_animations_enabled() and self._value:
            self._anim.start()


class _ReportRow(QWidget):
    """Строка отчёта как в таблице: значок, что измеряли (столбец одной ширины) и что получилось."""

    def __init__(self, line: Line, icon: str, name_width: int, parent=None, *, divided: bool = False) -> None:
        super().__init__(parent)
        self.line = line
        self._divided = divided
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 7, 0, 7)
        layout.setSpacing(10)

        state = line.state
        holder = QWidget(self)
        holder.setFixedSize(16, 20)
        if icon:
            self.marker = _StateIcon(state, holder, size=13, icon=icon)
            self.marker.move(0, 2)
        else:
            self.marker = ToneDot(lambda tokens: state_color(state, tokens), holder, size=7, hollow=state in _HOLLOW_STATES)
            self.marker.move(4, 6)
        layout.addWidget(holder, 0, Qt.AlignmentFlag.AlignTop)

        self.name_label = BodyLabel(line.name, self)
        self.name_label.setWordWrap(True)
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text_label: BodyLabel | None = None
        if line.text:
            # «Что измеряли» — приглушённо и в столбец, «что получилось» — основным цветом.
            mute(self.name_label)
            self.name_label.setFixedWidth(name_width)
            layout.addWidget(self.name_label, 0, Qt.AlignmentFlag.AlignTop)
            self.text_label = BodyLabel(line.text, self)
            self.text_label.setWordWrap(True)
            self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(self.text_label, 1, Qt.AlignmentFlag.AlignTop)
        else:
            layout.addWidget(self.name_label, 1, Qt.AlignmentFlag.AlignTop)
        set_state_text(self, f"{line.name}: {line.text}" if line.text else line.name)
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        if not self._divided:
            return
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 20) if _is_light() else QColor(255, 255, 255, 18))
        painter.drawRect(0, 0, self.width(), 1)
        painter.end()


@dataclass(frozen=True, slots=True)
class Tile:
    """Одна карточка сетки: что проверяли, чем кончилось и сколько заняло."""

    state: str
    title: str
    tag: str
    result: str
    seconds: str
    hint: str


_SECONDS = re.compile(r"^(.*) · (\d+(?:[.,]\d+)? с)$")
_NOT_CHECKED = "не удалось проверить: "


def line_tile(line: Line) -> Tile:
    """Строка измерения → карточка: «US.DO-01 · ecomstal.com» и «получено 32 КБ; … · 5.0 с» по частям.

    На карточке — короткий итог: начало фразы до пояснений. Вся фраза остаётся в подсказке.
    """
    tag, separator, title = line.name.partition(" · ")
    if not separator:
        tag, title = "", line.name
    text, seconds = line.text, ""
    match = _SECONDS.match(text)
    if match is not None:
        text, seconds = match.group(1), match.group(2)
    result = text.removeprefix(_NOT_CHECKED)
    # «Загрузка проходит, но отправка замирает» — суть стоит после «но».
    result = result.partition(", но ")[2] or result
    for separator in (" — ", "; ", ", хотя "):
        result = result.partition(separator)[0]
    result = f"{result[:1].upper()}{result[1:]}"
    return Tile(line.state, title, tag, result, seconds, f"{line.name}\n{line.text}\nНажмите, чтобы открыть страницу сервера")


_SERVER_MEANING = {
    "ok": "Загрузка прошла целиком: на этом сервере обрыва после 16 КБ нет.",
    "fail": (
        "Соединение установилось, но загрузка или отправка оборвалась на полпути. Так режет фильтр провайдера: "
        "сайты на этом хостинге могут грузиться не до конца."
    ),
    "unknown": (
        "С сервером не удалось соединиться или он не ответил, поэтому про обрыв ничего сказать нельзя. "
        "Это не значит, что сервер заблокирован."
    ),
}


def server_card(line: Line, section: Section) -> Card:
    """Отчёт по одному серверу хостинга: что это за сервер, чем кончилась проверка и что это значит."""
    tile = line_tile(line)
    provider = section.title.split(" — ")[0]
    facts = [Line("info", "Провайдер", provider), Line("info", "Адрес", tile.title)]
    if tile.tag:
        facts.append(Line("info", "Сервер", tile.tag))
    if tile.seconds:
        facts.append(Line("info", "Время проверки", tile.seconds))
    state = line.state if line.state in _SERVER_MEANING else "unknown"
    text = _SECONDS.match(line.text)
    return Card(
        key=f"hosting:{tile.tag or tile.title}",
        icon="fa5s.server",
        title=tile.title,
        level=state,
        status=tile.result,
        sections=(
            Section("Сервер", tuple(facts)),
            Section("Результат", (Line(line.state, "Итог", text.group(1) if text is not None else line.text),)),
            Section("Что это значит", (Line("info", _SERVER_MEANING[state]),)),
        ),
    )


def wants_findings(section: Section, card: Card) -> bool:
    """Раздел — выводы проверки DNS-серверов одной фразой каждая: их показывают по частям, с метками серверов."""
    return card.key == "dns_servers" and bool(section.lines) and not any(line.text for line in section.lines)


def finding_card(line: Line, parent=None) -> FindingCard:
    """Вывод про DNS карточкой: заголовок, серверы метками, остальное — в подсказке."""
    title, detail = split_finding(line.name)
    servers, more, rest = split_server_list(detail)
    state = line.state
    return FindingCard(
        title,
        parent,
        servers=servers,
        more=more,
        note=rest,
        hint="\n".join(part for part in (title, detail) if part),
        color_for=lambda tokens: state_color(state, tokens),
        hollow=state in _HOLLOW_STATES,
        state_text=line.name,
    )


def wants_tiles(section: Section, card: Card) -> bool:
    """Раздел — перечень однотипных серверов (хостинги): такие идут сеткой карточек, а не строками."""
    return card.key == "hostings" and bool(section.lines) and all(" · " in line.name and line.text for line in section.lines)


class TilesGrid(QWidget):
    """Сетка карточек, которую рисует один виджет: значок итога, адрес, короткий итог и время.

    На странице хостингов десятки серверов; по виджету с подписями на каждый —
    долгое открытие и тяжёлая перерисовка. Здесь всё рисуется за один проход, а
    подсказка с полной фразой показывается по месту мыши.
    """

    MIN_WIDTH = 250
    HEIGHT = 46
    GAP = 6
    # Нажали карточку: её номер.
    opened = pyqtSignal(int)

    def __init__(self, tiles: list[Tile], parent=None) -> None:
        super().__init__(parent)
        self._tiles = list(tiles)
        self._hover = -1
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMouseTracking(True)
        self._hint = HoverHint(self)
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())
        set_state_text(self, "; ".join(f"{tile.title}: {tile.result}" for tile in self._tiles))

    def tiles(self) -> list[Tile]:
        return list(self._tiles)

    def columns_for(self, width: int) -> int:
        return max(1, (int(width) + self.GAP) // (self.MIN_WIDTH + self.GAP))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        rows = -(-len(self._tiles) // self.columns_for(width))
        return max(0, rows * (self.HEIGHT + self.GAP) - self.GAP)

    def sizeHint(self) -> QSize:  # noqa: N802
        width = max(self.MIN_WIDTH, self.width())
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.MIN_WIDTH, self.HEIGHT)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        height = self.heightForWidth(self.width())
        if height != self.minimumHeight():
            self.setFixedHeight(height)

    def tile_rect(self, index: int) -> QRectF:
        columns = self.columns_for(self.width())
        width = (self.width() - self.GAP * (columns - 1)) / columns
        row, column = divmod(index, columns)
        return QRectF(column * (width + self.GAP), row * (self.HEIGHT + self.GAP), width, self.HEIGHT)

    def tile_at(self, x: float, y: float) -> int:
        return next((index for index in range(len(self._tiles)) if self.tile_rect(index).contains(x, y)), -1)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hover = self.tile_at(event.position().x(), event.position().y())
        if hover != self._hover:
            self._hover = hover
            self.update()
            self._hint.show(self._tiles[hover].hint if hover >= 0 else "", event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = -1
        self._hint.hide()
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        index = self.tile_at(event.position().x(), event.position().y())
        self._hint.hide()
        if event.button() == Qt.MouseButton.LeftButton and index >= 0:
            self.opened.emit(index)
        super().mouseReleaseEvent(event)

    def event(self, event) -> bool:
        # Системную подсказку не показываем: на тёмной теме она мелькает белым окном.
        if event.type() == QEvent.Type.ToolTip:
            return True
        return super().event(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        light = _is_light()
        base = QColor(0, 0, 0, 10) if light else QColor(255, 255, 255, 10)
        hover = QColor(0, 0, 0, 20) if light else QColor(255, 255, 255, 20)
        text = QColor(0, 0, 0, 228) if light else QColor(255, 255, 255, 235)
        muted = QColor(0, 0, 0, 150) if light else QColor(255, 255, 255, 150)
        title_font = QFont(self.font())
        title_font.setPixelSize(13)
        title_font.setWeight(QFont.Weight.DemiBold)
        small_font = QFont(self.font())
        small_font.setPixelSize(12)
        title_metrics, small_metrics = QFontMetrics(title_font), QFontMetrics(small_font)
        for index, tile in enumerate(self._tiles):
            rect = self.tile_rect(index)
            if not rect.intersects(QRectF(event.rect())):
                continue
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(hover if index == self._hover else base)
            painter.drawRoundedRect(rect, 6, 6)
            color = state_color(tile.state)
            try:
                icon = get_cached_qta_pixmap(_STATE_ICONS.get(tile.state, _STATE_ICONS["unknown"]), color=color, size=15)
                painter.drawPixmap(int(rect.left() + 11), int(rect.top() + (self.HEIGHT - 15) / 2), 15, 15, icon)
            except Exception:
                pass
            left = rect.left() + 36
            right = rect.right() - 10
            # Время — справа в первой строке; название занимает остальное.
            painter.setFont(small_font)
            painter.setPen(muted)
            seconds_width = small_metrics.horizontalAdvance(tile.seconds) + 8 if tile.seconds else 0
            if tile.seconds:
                painter.drawText(
                    QRectF(right - seconds_width, rect.top() + 6, seconds_width, 17),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    tile.seconds,
                )
            second = f"{tile.tag} · {tile.result}" if tile.tag else tile.result
            painter.drawText(
                QRectF(left, rect.top() + 24, right - left, 17),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                small_metrics.elidedText(second, Qt.TextElideMode.ElideRight, int(right - left)),
            )
            painter.setFont(title_font)
            painter.setPen(text)
            title_width = right - left - seconds_width
            painter.drawText(
                QRectF(left, rect.top() + 6, title_width, 17),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                title_metrics.elidedText(tile.title, Qt.TextElideMode.ElideRight, int(title_width)),
            )
        painter.end()


class _SectionBlock(QWidget):
    """Раздел отчёта: значок, название, сводка «сколько в порядке» с полосой и строки-таблица."""

    # Просят открыть текст раздела на всю страницу: (название, текст).
    text_opened = pyqtSignal(str, str)
    # Нажали карточку сервера: строка измерения этого сервера.
    line_opened = pyqtSignal(object)

    def __init__(
        self,
        section: Section,
        parent=None,
        *,
        icon: str = "fa5s.list-ul",
        color: str = "",
        tiles: bool = False,
        findings: bool = False,
    ) -> None:
        super().__init__(parent)
        self.section = section
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 8)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 6)
        header.setSpacing(10)
        self.icon = BrandIcon(icon, color, self, size=16)
        header.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = StrongBodyLabel(section.title, self)
        self.title_label.setWordWrap(True)
        header.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        # Сводка раздела: сколько строк в порядке и полоса долей.
        self.bar: ShareBar | None = None
        self.summary_label: CaptionLabel | None = None
        counts = tally(section.lines)
        total = sum(counts.values())
        if total >= 2 and set(counts) & {"ok", "fail"}:
            self.summary_label = mute(CaptionLabel(f"в порядке {counts.get('ok', 0)} из {total}", self))
            header.addWidget(self.summary_label, 0, Qt.AlignmentFlag.AlignVCenter)
            self.bar = ShareBar(state_color, self)
            self.bar.setFixedWidth(110)
            self.bar.set_segments([(state, counts[state]) for state, _caption in _TALLY if state in counts])
            header.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        metrics = QFontMetrics(self.title_label.font())
        widest = max((metrics.horizontalAdvance(line.name) for line in section.lines if line.text), default=0)
        name_width = max(NAME_COLUMN_MIN, min(NAME_COLUMN_MAX, widest + 12))
        # Перечень серверов — сеткой карточек одним виджетом; остальное — строками таблицы.
        self.grid: TilesGrid | None = None
        self.rows: list = []
        self.findings_flow: CardsFlow | None = None
        if tiles:
            self.grid = TilesGrid([line_tile(line) for line in section.lines], self)
            self.grid.opened.connect(lambda index: self.line_opened.emit(section.lines[index]))
            layout.addSpacing(2)
            layout.addWidget(self.grid)
            layout.addSpacing(6)
        elif findings:
            # Выводы — сеткой карточек, как находки DNS в итоге проверки.
            self.findings_flow = CardsFlow(self, min_width=FindingCard.MIN_WIDTH, card_height=FindingCard.HEIGHT)
            self.rows = [finding_card(line, self.findings_flow) for line in section.lines]
            for card in self.rows:
                self.findings_flow.add(card)
            layout.addSpacing(2)
            layout.addWidget(self.findings_flow)
            layout.addSpacing(6)
        else:
            self.rows = [
                _ReportRow(line, line_icon(line, section), name_width, self, divided=True) for line in section.lines
            ]
            for row in self.rows:
                layout.addWidget(row)
        # Длинный текст (таблица серверов, узлы по дороге) — в редакторе с подсветкой и своей
        # прокруткой. Одной надписью на сотни строк он перерисовывался целиком при каждой
        # прокрутке страницы, и страница заметно тормозила.
        self.editor: CodeEditor | None = None
        self.matrix: ServerMatrix | None = None
        self.open_text_button: PushButton | None = None
        columns, table = parse_server_table(section.text) if section.text else ([], [])
        if table:
            # Таблица серверов — сводкой по сервисам; сам текст открывается кнопкой в заголовке.
            self.matrix = ServerMatrix(columns, table, self)
            layout.addWidget(self.matrix)
            layout.addSpacing(6)
        if section.text:
            self.open_text_button = PushButton("Подробный текст" if table else "Открыть на всю страницу", self)
            set_control_accessibility(
                self.open_text_button,
                name=f"Открыть на всю страницу: {section.title}",
                description="Открывает этот текст страницей-редактором с поиском и переходом к строке.",
            )
            self.open_text_button.clicked.connect(lambda _checked=False: self.text_opened.emit(section.title, section.text))
            header.addWidget(self.open_text_button, 0, Qt.AlignmentFlag.AlignVCenter)
        if section.text and not table:
            self.editor = CodeEditor(self, highlighter_factory=lambda document: LogSyntaxHighlighter(document))
            self.editor.setReadOnly(True)
            self._fill = ChunkedReadOnlyFill(self.editor)
            self._fill.set_text(section.text)
            lines = min(TEXT_PREVIEW_LINES, section.text.count("\n") + 1)
            self.editor.setFixedHeight(lines * self.editor.fontMetrics().lineSpacing() + 28)
            layout.addWidget(self.editor)
            layout.addSpacing(8)

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 12) if _is_light() else QColor(255, 255, 255, 11))
        painter.drawRoundedRect(self.rect(), CARD_RADIUS, CARD_RADIUS)
        painter.end()


class _ReportHero(QWidget):
    """Шапка отчёта: логотип, название, результат, метки и сводка по всем измерениям."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._card: Card | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 16, 16)
        root.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(14)
        self.icon = BrandIcon("fa5s.globe", "", self, size=34)
        top.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        self.title_label = SubtitleLabel("", self)
        titles.addWidget(self.title_label)
        status_row = QHBoxLayout()
        status_row.setSpacing(7)
        self._status_dot = ToneDot(self._status_color, self)
        status_row.addWidget(self._status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.status_label = BodyLabel("", self)
        status_row.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row.addStretch(1)
        titles.addLayout(status_row)
        self._chips_host = QWidget(self)
        self._chips_flow = FlowLayout(self._chips_host, needAni=False)
        self._chips_flow.setContentsMargins(0, 4, 0, 0)
        self._chips_flow.setHorizontalSpacing(6)
        self._chips_flow.setVerticalSpacing(4)
        titles.addWidget(self._chips_host)
        top.addLayout(titles, 1)
        self._stats = QHBoxLayout()
        self._stats.setSpacing(22)
        top.addLayout(self._stats)
        top.addSpacing(8)
        self.copy_button = PushButton("Скопировать", self)
        top.addWidget(self.copy_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)
        self.bar = ShareBar(state_color, self)
        root.addWidget(self.bar)
        self.counts: list[_CountLabel] = []
        self.chip_labels: list[QLabel] = []
        self._stat_widgets: list[QWidget] = []
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)

    def _status_color(self, tokens=None) -> str:
        return card_color(self._card, tokens) if self._card is not None else state_color("unknown", tokens)

    def set_card(self, card: Card) -> None:
        self._card = card
        self.title_label.setText(card.title)
        self.status_label.setText(card.status)
        brand = site_brand(card.key.removeprefix("site:"), card.title) if card.site else None
        self.icon.set_icon(brand.icon if brand else card.icon, brand.color if brand else "")

        self._chips_flow.takeAllWidgets()
        for widget in (*self.chip_labels, *self._stat_widgets):
            widget.setParent(None)
            widget.deleteLater()
        self.chip_labels, self._stat_widgets, self.counts = [], [], []
        for text, state in card.chips:
            chip = QLabel(text, self._chips_host)
            chip.setFixedHeight(20)
            chip.setProperty("chipState", state)
            self._chips_flow.addWidget(chip)
            self.chip_labels.append(chip)
        self._chips_host.setVisible(bool(self.chip_labels))

        counts = tally(line for section in card.sections for line in section.lines)
        for state, caption in _TALLY:
            if state not in counts:
                continue
            box = QWidget(self)
            column = QVBoxLayout(box)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(0)
            number = _CountLabel(counts[state], box)
            column.addWidget(number, 0, Qt.AlignmentFlag.AlignRight)
            caption_row = QHBoxLayout()
            caption_row.setSpacing(5)
            caption_row.addWidget(
                ToneDot(lambda tokens, s=state: state_color(s, tokens), box, size=6, hollow=state in _HOLLOW_STATES),
                0,
                Qt.AlignmentFlag.AlignVCenter,
            )
            caption_row.addWidget(mute(CaptionLabel(caption, box)), 0, Qt.AlignmentFlag.AlignVCenter)
            column.addLayout(caption_row)
            self._stats.addWidget(box, 0, Qt.AlignmentFlag.AlignTop)
            self._stat_widgets.append(box)
            self.counts.append(number)
            number.play()
        self.bar.setVisible(sum(counts.values()) >= 2)
        self.bar.set_segments([(state, counts[state]) for state, _caption in _TALLY if state in counts])
        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        if self._card is None:
            return
        color = QColor(card_color(self._card, tokens))
        self.status_label.setTextColor(color, color)
        self._status_dot._apply_theme_refresh(tokens)
        for chip in self.chip_labels:
            state = str(chip.property("chipState") or "info")
            chip.setStyleSheet(
                _chip_style(state_color(state, tokens) if state in _LOUD_CHIPS else _muted_text(tokens), tokens)
            )
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 12) if _is_light() else QColor(255, 255, 255, 11))
        painter.drawRoundedRect(self.rect(), CARD_RADIUS, CARD_RADIUS)
        painter.end()


class ResultDetailView(QWidget):
    """Отчёт по одной карточке на всю страницу: путь «BlockCheck → карточка», шапка-сводка и разделы."""

    # Шаг назад: туда, откуда отчёт открыли (вкладка BlockCheck или прошлая проверка).
    closed = pyqtSignal()
    # «BlockCheck» в строке пути, когда отчёт открыт из прошлой проверки: сразу на вкладку.
    root_requested = pyqtSignal()
    # Просят открыть длинный текст раздела страницей-редактором: (название, текст).
    text_opened = pyqtSignal(str, str)
    ROOT_KEY = "blockcheck"
    PARENT_KEY = "parent"
    # Шаг строки пути внутри отчёта: «Зарубежные хостинги» над страницей одного сервера.
    LEVEL_KEY = "level:"
    CARD_KEY = "card"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._card: Card | None = None
        self._parent_title = ""
        # Отчёты выше текущего внутри этой страницы и место прокрутки в каждом.
        self._ancestors: list[tuple[Card, int]] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)

        self.breadcrumb = BreadcrumbBar(self)
        self.breadcrumb.currentItemChanged.connect(self._on_breadcrumb)
        self._layout.addWidget(self.breadcrumb)

        self.hero = _ReportHero(self)
        self.title_label = self.hero.title_label
        self.status_label = self.hero.status_label
        self.copy_button = self.hero.copy_button
        set_control_accessibility(
            self.copy_button,
            name="Скопировать подробности",
            description="Кладёт все измерения этой проверки в буфер обмена обычным текстом.",
        )
        self.copy_button.clicked.connect(self._copy)
        self._layout.addWidget(self.hero)

        self._sections_host = QWidget(self)
        self._sections_layout = QVBoxLayout(self._sections_host)
        self._sections_layout.setContentsMargins(0, 0, 0, 0)
        self._sections_layout.setSpacing(8)
        self._layout.addWidget(self._sections_host)
        # Отчёт короче окна: лишняя высота уходит вниз, а не растягивает шапку и строки.
        self._layout.addStretch(1)
        self.blocks: list[_SectionBlock] = []

    def card(self) -> Card | None:
        return self._card

    def show_card(self, card: Card, *, parent_title: str = "") -> None:
        """``parent_title`` — промежуточный шаг строки пути: прошлая проверка, из которой открыт отчёт."""
        self._parent_title = str(parent_title or "")
        self._ancestors = []
        self._render(card)

    def open_child(self, card: Card) -> None:
        """Шаг вглубь: отчёт по одному серверу. Строка пути получает ещё один шаг, «назад» ведёт к списку."""
        if self._card is None:
            return
        area = self._scroll_area()
        scroll = area.verticalScrollBar().value() if area is not None else 0
        self._ancestors.append((self._card, scroll))
        self._render(card)
        if area is not None:
            area.verticalScrollBar().setValue(0)

    def go_back(self) -> bool:
        """Шаг назад внутри отчёта. ``False`` — возвращаться некуда: отчёт пора закрывать."""
        if not self._ancestors:
            return False
        self._return_to(len(self._ancestors) - 1)
        return True

    def _return_to(self, level: int) -> None:
        card, scroll = self._ancestors[level]
        del self._ancestors[level:]
        self._render(card)
        area = self._scroll_area()
        if area is not None:
            # К тому же месту списка, с которого уходили; раскладка к этому мигу ещё досчитывается.
            QTimer.singleShot(0, lambda: area.verticalScrollBar().setValue(scroll))

    def _scroll_area(self) -> QAbstractScrollArea | None:
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def _render(self, card: Card) -> None:
        self._card = card
        ancestors = [item.title for item, _scroll in self._ancestors]
        path = ["BlockCheck", *([self._parent_title] if self._parent_title else []), *ancestors, card.title]
        self.breadcrumb.blockSignals(True)
        try:
            self.breadcrumb.clear()
            self.breadcrumb.addItem(self.ROOT_KEY, "BlockCheck")
            if self._parent_title:
                self.breadcrumb.addItem(self.PARENT_KEY, self._parent_title)
            for level, title in enumerate(ancestors):
                self.breadcrumb.addItem(f"{self.LEVEL_KEY}{level}", title)
            self.breadcrumb.addItem(self.CARD_KEY, card.title)
            set_breadcrumb_accessibility(self.breadcrumb, path)
        finally:
            self.breadcrumb.blockSignals(False)
        self.copy_button.setText("Скопировать")
        self.hero.set_card(card)
        for block in self.blocks:
            block.setParent(None)
            block.deleteLater()
        self.blocks = []
        for section in card.sections:
            icon, color = section_icon(section, card)
            block = _SectionBlock(
                section,
                self._sections_host,
                icon=icon,
                color=color,
                tiles=wants_tiles(section, card),
                findings=wants_findings(section, card),
            )
            block.text_opened.connect(self.text_opened)
            block.line_opened.connect(lambda line, source=section: self.open_child(server_card(line, source)))
            self.blocks.append(block)
        for order, block in enumerate(self.blocks):
            self._sections_layout.addWidget(block)
            # Выплывают только первые блоки — те, что видны сразу. Анимация каждого из
            # десятков блоков длинного отчёта делала открытие страницы долгим и дёрганым.
            if order < ANIMATED_BLOCKS:
                float_in(block, delay_ms=order * 45)
        set_state_text(self, f"Подробности: {card.title}, {card.status}, разделов {len(card.sections)}")
        self._sync_height()

    def _on_breadcrumb(self, key: str) -> None:
        if key.startswith(self.LEVEL_KEY):
            self._return_to(int(key.removeprefix(self.LEVEL_KEY)))
        elif key == self.PARENT_KEY:
            self._ancestors = []
            self.closed.emit()
        elif key == self.ROOT_KEY:
            self._ancestors = []
            (self.root_requested if self._parent_title else self.closed).emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if not self.go_back():
                self.closed.emit()
            return
        super().keyPressEvent(event)

    def _copy(self) -> None:
        if self._card is None:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(card_plain_text(self._card))
        self.copy_button.setText("Скопировано")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self) -> None:
        # Текст с переносом не передаёт высоту через вложенную прокрутку страницы.
        if self.width() <= 0:
            return
        needed = self._layout.totalHeightForWidth(self.width()) if self._layout.hasHeightForWidth() else -1
        if needed <= 0:
            needed = self._layout.totalSizeHint().height()
        if needed != self.minimumHeight():
            self.setMinimumHeight(needed)


__all__ = [
    "CardsGrid",
    "CountersStrip",
    "HostingDots",
    "ProgressSteps",
    "ResultCard",
    "ResultCardsView",
    "ResultDetailView",
    "card_color",
    "card_plain_text",
    "state_color",
]
