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
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPixmap
from PyQt6.QtWidgets import QAbstractScrollArea, QGridLayout, QHBoxLayout, QLabel, QLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets.common.font import getFont
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
from blockcheck.ui.brand_icons import BrandIcon, named_brand, readable_color, site_brand
from blockcheck.ui.finding_parts import CardsFlow, FindingCard, split_finding, split_server_list
from blockcheck.ui.server_matrix import ServerMatrix, ServiceSummary, cell_state, parse_server_table
from blockcheck.ui.result_cards_model import (
    PREVIEW_LINES,
    Card,
    Counter,
    DotGroup,
    FindingParts,
    Line,
    Section,
    build_cards,
    build_counters,
)
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
from ui.widgets.tone_group import ToneDot, dot_on_first_line, mute, paint_dot

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
    """Карточка одной проверки. Нажатие (или Enter) открывает подробности.

    Всё на карточке — значок, название, слово итога, строки и метки — рисует
    она сама за один проход. Раньше на каждую надпись был свой виджет: сетка из
    сорока карточек — это шестьсот виджетов, и её показ подвешивал окно.
    """

    opened = pyqtSignal(object)

    PAD_X = 14
    PAD_Y = 12
    ICON = 22
    HEADER = 36
    HEADER_GAP = 12
    LINE = 19
    LINE_GAP = 6
    MORE = 16
    CHIP = 20
    CHIP_PAD = 7
    CHIP_GAP_X = 6
    CHIP_GAP_Y = 4
    MARK_ROW = 20
    MARK_COLUMNS = 2

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
        self._title_font = getFont(14, QFont.Weight.DemiBold)
        self._text_font = getFont(12)
        self._text_metrics = QFontMetrics(self._text_font)
        # Логотип сайта — в фирменном цвете; у остальных проверок значок нейтральный.
        brand = site_brand(card.key.removeprefix("site:"), card.title) if card.site else None
        self._icon_name = brand.icon if brand else card.icon
        self._icon_color = brand.color if brand else ""
        self._icon = QPixmap()
        self._lines = tuple(card.lines[:PREVIEW_LINES])
        hidden = len(card.lines) - len(self._lines)
        self._more = f"и ещё {hidden} — нажмите, чтобы увидеть всё" if hidden > 0 else ""
        # У сайта дороги (TLS, HTTP, QUIC, DNS) стоят сеткой на постоянных местах; метками —
        # только то, чего в дорогах нет. У остальных карточек меток столько, сколько есть.
        self._marks = tuple(card.marks)
        self._chips = tuple(card.tags if card.marks else card.chips)

        self.dots: HostingDots | None = None
        if card.dots:
            self.dots = HostingDots(card.dots, self)

        set_control_accessibility(
            self,
            name=f"{card.title}: {card.status}",
            description="Нажмите, чтобы открыть все измерения этой проверки.",
        )
        set_state_text(self, f"{card.title}: {card.status}")
        set_tooltip(self, card_hint(card))
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def icon_name(self) -> str:
        return self._icon_name

    def brand_color(self) -> str:
        return self._icon_color

    def shown_lines(self) -> tuple[Line, ...]:
        """Строки, которые видны на карточке; остальные — в подробностях."""
        return self._lines

    def more_text(self) -> str:
        return self._more

    def shown_header(self) -> tuple[str, str]:
        """Название и слово итога так, как они видны при текущей ширине (с многоточием, если не влезли)."""
        width = max(8, self.width() - self.PAD_X * 2 - self.ICON - 11)
        title = QFontMetrics(self._title_font).elidedText(self.card.title, Qt.TextElideMode.ElideRight, width)
        status = self._text_metrics.elidedText(self.card.status, Qt.TextElideMode.ElideRight, max(8, width - 13))
        return title, status

    def _chip_rects(self, width: int, top: float) -> list[QRectF]:
        """Места меток: в ряд, с переносом на новую строку, когда ряд кончился."""
        rects: list[QRectF] = []
        left, right = self.PAD_X, width - self.PAD_X
        x, y = float(left), float(top)
        for text, _state in self._chips:
            # Запас в пару точек: без него текст метки сокращался многоточием на ровном месте.
            chip = min(self._text_metrics.horizontalAdvance(text) + self.CHIP_PAD * 2 + 4, right - left)
            if x > left and x + chip > right:
                x, y = float(left), y + self.CHIP + self.CHIP_GAP_Y
            rects.append(QRectF(x, y, chip, self.CHIP))
            x += chip + self.CHIP_GAP_X
        return rects

    def _places(self, width: int) -> tuple[float, float, float, list[QRectF], int, float]:
        """Где что стоит при такой ширине: верх строк, надписи «и ещё», точек, места меток, высота карточки и верх сетки дорог."""
        y = float(self.PAD_Y + self.HEADER)
        lines_top = y + self.HEADER_GAP if self._lines else y
        if self._lines:
            y = lines_top + len(self._lines) * (self.LINE + self.LINE_GAP) - self.LINE_GAP
        more_top = y + self.LINE_GAP
        if self._more:
            y = more_top + self.MORE
        dots_top = y + self.LINE_GAP
        if self.dots is not None:
            y = dots_top + self.dots.heightForWidth(max(1, width - self.PAD_X * 2))
        marks_top = y + self.LINE_GAP + 4
        if self._marks:
            y = marks_top + -(-len(self._marks) // self.MARK_COLUMNS) * self.MARK_ROW
        chips = self._chip_rects(width, y + self.LINE_GAP + 2) if self._chips else []
        if chips:
            y = chips[-1].bottom()
        return lines_top, more_top, dots_top, chips, int(y + self.PAD_Y), marks_top

    def mark_rect(self, index: int) -> QRectF:
        """Место дороги номер ``index``: у всех карточек одной ширины оно одно и то же."""
        inner = self.width() - self.PAD_X * 2
        cell = inner / self.MARK_COLUMNS
        row, column = divmod(index, self.MARK_COLUMNS)
        top = self._places(self.width())[5] + row * self.MARK_ROW
        return QRectF(self.PAD_X + column * cell, top, cell - 8, self.MARK_ROW)

    def height_for(self, width: int) -> int:
        return self._places(width)[4]

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self.height_for(width)

    def sizeHint(self) -> QSize:  # noqa: N802
        width = max(240, self.width())
        return QSize(width, self.height_for(width))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.dots is not None:
            inner = max(1, self.width() - self.PAD_X * 2)
            self.dots.setGeometry(self.PAD_X, int(self._places(self.width())[2]), inner, self.dots.heightForWidth(inner))

    def play(self, delay_ms: int = 0) -> None:
        """Карточка выплывает, а точки хостингов проявляются."""
        float_in(self, delay_ms=delay_ms)
        if self.dots is not None:
            self.dots.play()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        self._color = QColor(card_color(self.card, tokens))
        self._light = _is_light(tokens)
        try:
            from profile.ui.profile_icon import profile_icon_pixmap
            from ui.theme import get_theme_tokens, to_qcolor

            tokens = tokens or get_theme_tokens()
            self._surface = to_qcolor(tokens.surface_bg, "#0affffff")
            self._surface_hover = to_qcolor(tokens.surface_bg_hover, "#12ffffff")
            if self._icon_color:
                color = readable_color(self._icon_color, light_theme=bool(tokens.is_light))
            else:
                color = str(tokens.icon_fg_muted)
            self._icon = profile_icon_pixmap(self._icon_name, color=color, size=self.ICON)
        except Exception:
            pass
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._surface_hover if self._hover or self.hasFocus() else self._surface)
        painter.drawRoundedRect(self.rect(), CARD_RADIUS, CARD_RADIUS)

        light = self._light
        text = QColor(0, 0, 0, 228) if light else QColor(255, 255, 255, 235)
        muted = QColor(0, 0, 0, 158) if light else QColor(255, 255, 255, 158)
        left_flag = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        left, right = self.PAD_X, self.width() - self.PAD_X
        top = self.PAD_Y
        if not self._icon.isNull():
            painter.drawPixmap(left, top + (self.HEADER - self.ICON) // 2, self.ICON, self.ICON, self._icon)
        # Слово результата стоит под названием, а не рядом: так оба текста видны целиком.
        titles_left = left + self.ICON + 11
        titles_width = max(8, right - titles_left)
        title, status = self.shown_header()
        painter.setFont(self._title_font)
        painter.setPen(text)
        painter.drawText(QRectF(titles_left, top, titles_width, 19), left_flag, title)
        paint_dot(
            painter, QRectF(titles_left, top + 25, 7, 7), self._color, hollow=self.card.level in _HOLLOW_STATES
        )
        metrics = self._text_metrics
        painter.setFont(self._text_font)
        painter.setPen(self._color)
        painter.drawText(QRectF(titles_left + 13, top + 20, max(8, titles_width - 13), 17), left_flag, status)

        lines_top, more_top, _dots_top, chips, _height, _marks_top = self._places(self.width())
        inner = max(8, right - left - 14)
        for order, line in enumerate(self._lines):
            row_top = lines_top + order * (self.LINE + self.LINE_GAP)
            paint_dot(
                painter,
                QRectF(left, row_top + (self.LINE - 7) / 2, 7, 7),
                QColor(state_color(line.state)),
                hollow=line.state in _HOLLOW_STATES,
            )
            # Название — целиком, сколько есть места; сокращается пояснение справа.
            name_width = min(inner, metrics.horizontalAdvance(line.name) + 2) if line.text else inner
            painter.setPen(text)
            painter.drawText(
                QRectF(left + 14, row_top, name_width, self.LINE),
                left_flag,
                metrics.elidedText(line.name, Qt.TextElideMode.ElideRight, int(name_width)),
            )
            rest = inner - name_width - 10
            if line.text and rest > 16:
                painter.setPen(muted)
                painter.drawText(
                    QRectF(left + 14 + name_width + 10, row_top, rest, self.LINE),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    metrics.elidedText(line.text, Qt.TextElideMode.ElideRight, int(rest)),
                )
        if self._more:
            painter.setPen(muted)
            painter.drawText(QRectF(left, more_top, right - left, self.MORE), left_flag, self._more)
        for index, mark in enumerate(self._marks):
            rect = self.mark_rect(index)
            color = QColor(state_color(mark.state))
            try:
                icon = get_cached_qta_pixmap(mark.icon, color=color.name(), size=12)
                painter.drawPixmap(int(rect.left()), int(rect.top() + (self.MARK_ROW - 12) / 2), 12, 12, icon)
            except Exception:
                pass
            # Название дороги — приглушённо, чем кончилось — в цвете состояния.
            label_width = min(rect.width() - 18, metrics.horizontalAdvance(mark.label) + 2)
            painter.setPen(muted)
            painter.drawText(QRectF(rect.left() + 18, rect.top(), label_width, self.MARK_ROW), left_flag, mark.label)
            rest = rect.width() - 18 - label_width - 6
            if rest > 12:
                painter.setPen(color if mark.state in ("fail", "warn") else text)
                painter.drawText(
                    QRectF(rect.left() + 18 + label_width + 6, rect.top(), rest, self.MARK_ROW),
                    left_flag,
                    metrics.elidedText(mark.word, Qt.TextElideMode.ElideRight, int(rest)),
                )
        back = QColor(0, 0, 0, 13) if light else QColor(255, 255, 255, 15)
        for (label, state), rect in zip(self._chips, chips):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(back)
            painter.drawRoundedRect(rect, 4, 4)
            # Метка говорит о проблеме — её текст в цвете состояния; остальные приглушены.
            painter.setPen(QColor(state_color(state)) if state in _LOUD_CHIPS else muted)
            inside = rect.adjusted(self.CHIP_PAD, 0, -self.CHIP_PAD, 0)
            painter.drawText(inside, left_flag, metrics.elidedText(label, Qt.TextElideMode.ElideRight, int(inside.width())))
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

    Карточки идут ровными рядами: в ряду все одной высоты, по самой высокой;
    широкая (``wide``) занимает весь ряд. Высота сетки считается здесь же: вложенная прокрутка страницы
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

    def sync_cards(self, cards: list[Card], *, animate: bool = True) -> None:
        """Приводит сетку к новому набору карточек, не трогая те, что не изменились.

        Так результаты встают по ходу проверки: новая карточка появляется,
        изменившаяся заменяется на своём месте, остальные стоят как стояли.
        """
        old = {widget.card.key: widget for widget in self.cards()}
        result: list[ResultCard] = []
        fresh: list[ResultCard] = []
        for card in cards:
            widget = old.pop(card.key, None)
            if widget is not None and widget.card != card:
                widget.setParent(None)
                widget.deleteLater()
                widget = None
                replaced = True
            else:
                replaced = False
            if widget is None:
                widget = ResultCard(card, self)
                widget.opened.connect(self.opened)
                widget.show()
                if not replaced:
                    fresh.append(widget)
            result.append(widget)
        for widget in old.values():
            widget.setParent(None)
            widget.deleteLater()
        self._cards = result
        if not result:
            self.setFixedHeight(0)
            return
        self._place()
        if animate:
            # Выплывает только то, чего на экране не было; обновлённая карточка просто меняет текст.
            for order, widget in enumerate(fresh[:ANIMATED_CARDS]):
                widget.play(order * 55)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place()

    def _place(self) -> None:
        width = self.width()
        if width <= 0 or not self._cards:
            return
        columns = self.columns_for(width)
        column_width = (width - GRID_GAP * (columns - 1)) // columns
        # Карточки идут рядами, и в ряду все одной высоты — по самой высокой. Раньше каждая
        # вставала в самый короткий столбец: края рядов получались рваными.
        top = 0
        row: list[tuple[ResultCard, int]] = []

        def flush() -> None:
            nonlocal top
            if not row:
                return
            height = max(need for _widget, need in row)
            for column, (item, _need) in enumerate(row):
                item.setGeometry(QRect(column * (column_width + GRID_GAP), top, column_width, height))
            top += height + GRID_GAP
            row.clear()

        for widget in self._cards:
            if widget.card.wide:
                flush()
                height = self._card_height(widget, width)
                widget.setGeometry(QRect(0, top, width, height))
                top += height + GRID_GAP
                continue
            row.append((widget, self._card_height(widget, column_width)))
            if len(row) == columns:
                flush()
        flush()
        heights = [top]
        total = max(0, max(heights) - GRID_GAP)
        if total != self.height():
            self.setFixedHeight(total)

    @staticmethod
    def _card_height(widget: ResultCard, width: int) -> int:
        return widget.height_for(width)


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
        self._shown: list[Card] = []
        # Карточки поставлены неполным отчётом по ходу проверки (см. show_partial).
        self._live = False
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
        self._shown = []
        self._live = False
        set_state_text(self, "Результаты BlockCheck: пока нет результатов")

    def has_cards(self) -> bool:
        """Есть ли что показать: итог или карточки с хода идущей проверки."""
        return bool(self._shown)

    def show_partial(self, report: dict) -> None:
        """Неполный отчёт по ходу проверки: карточки появляются по мере готовности.

        Счётчики «что проверено» ждут итога: пока проверка идёт, их числа
        менялись бы каждую секунду.
        """
        cards = build_cards(report)
        if cards == self._shown:
            return
        self._live = True
        self._place_cards(cards, animate=True)
        set_state_text(self, f"Результаты BlockCheck: проверка идёт, карточек {len(cards)}")

    def _place_cards(self, cards: list[Card], *, animate: bool) -> None:
        self._shown = cards
        sites = [card for card in cards if card.site]
        # Широкая карточка занимает весь ряд: стоя посреди списка, она оставляла перед собой
        # ряд с одной карточкой и пустотой. Широкие идут первыми, остальные заполняют ряды подряд.
        checks = sorted((card for card in cards if not card.site), key=lambda card: not card.wide)
        self.sites_title.setVisible(bool(sites))
        self.checks_title.setVisible(bool(checks))
        if self._live:
            # Карточки уже стоят с хода проверки: меняем только то, что изменилось.
            self.sites_grid.sync_cards(sites, animate=animate)
            self.checks_grid.sync_cards(checks, animate=animate)
        else:
            self.sites_grid.show_cards(sites, animate=animate)
            self.checks_grid.show_cards(checks, animate=animate, first_delay_ms=200)

    def show_report(self, report: dict, *, animate: bool = True) -> None:
        cards = build_cards(report)
        # Тот же итог показывают повторно (вернулись на страницу): карточки уже стоят, заново не строим.
        if cards and cards == self._shown and not self._live:
            return
        self.counters.show_counters(build_counters(report), animate=animate)
        self._place_cards(cards, animate=animate)
        self._live = False
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
NARROW_NAME = 22
NARROW_TEXT = 26
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
    if section.title in (_ABOUT_TITLE, "Что это значит", "Что найдено"):
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
        # Метод, а не безымянная функция: её ссылка на счётчик пустела, когда отчёт закрывали
        # посреди счёта, и следующий кадр анимации падал с ошибкой.
        self._anim.valueChanged.connect(self._show_share)

    def value(self) -> int:
        return self._value

    def _show_share(self, share) -> None:
        self.setText(str(int(round(self._value * float(share)))))

    def hideEvent(self, event) -> None:  # noqa: N802
        self._anim.stop()
        self.setText(str(self._value))
        super().hideEvent(event)

    def play(self) -> None:
        if are_live_animations_enabled() and self._value:
            self._anim.start()


class RowsTable(QWidget):
    """Строки раздела отчёта, которые рисует один виджет: значок, что измеряли и что получилось.

    Раньше строка была четырьмя виджетами с надписями, и отчёт сайта из
    тридцати строк строился заметную долю секунды. Здесь строки — просто текст
    с переносом: высота каждой считается по ширине окна.
    """

    PAD = 7
    MARK = 16
    GAP = 10
    MIN_ROW = 20

    def __init__(self, lines, icons: list[str], name_width: int, parent=None) -> None:
        super().__init__(parent)
        self._lines = tuple(lines)
        self._icons = list(icons)
        self._name_width = int(name_width)
        self._font = getFont(14)
        self._metrics = QFontMetrics(self._font)
        self._tops: list[tuple[float, float]] = []
        self._placed_for = -1
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())
        set_state_text(self, "; ".join(f"{line.name}: {line.text}" if line.text else line.name for line in self._lines))

    def lines(self) -> tuple:
        return self._lines

    def name_width(self) -> int:
        return self._name_width

    def text_left(self) -> int:
        """Где начинается столбец значений: одна линия у всех строк раздела."""
        return self.MARK + self.GAP + self._name_width + self.GAP

    def _wrap_flags(self) -> int:
        return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)

    def _text_height(self, text: str, width: int) -> int:
        return self._metrics.boundingRect(QRect(0, 0, max(40, width), 100000), self._wrap_flags(), text).height()

    def _place(self, width: int) -> list[tuple[float, float]]:
        if width == self._placed_for:
            return self._tops
        tops: list[tuple[float, float]] = []
        y = 0.0
        for line in self._lines:
            if line.text:
                height = max(
                    self._text_height(line.name, self._name_width),
                    self._text_height(line.text, width - self.text_left()),
                )
            else:
                height = self._text_height(line.name, width - self.MARK - self.GAP)
            height = max(self.MIN_ROW, height) + self.PAD * 2
            tops.append((y, float(height)))
            y += height
        self._tops, self._placed_for = tops, width
        return tops

    def row_rect(self, index: int) -> QRectF:
        top, height = self._place(self.width())[index]
        return QRectF(0, top, self.width(), height)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        tops = self._place(width)
        return int(tops[-1][0] + tops[-1][1]) if tops else 0

    def sizeHint(self) -> QSize:  # noqa: N802
        width = max(240, self.width())
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(160, self.MIN_ROW + self.PAD * 2)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        height = self.heightForWidth(self.width())
        if height != self.minimumHeight() or height != self.maximumHeight():
            self.setFixedHeight(height)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        light = _is_light()
        text = QColor(0, 0, 0, 228) if light else QColor(255, 255, 255, 235)
        muted = QColor(0, 0, 0, 158) if light else QColor(255, 255, 255, 158)
        divider = QColor(0, 0, 0, 20) if light else QColor(255, 255, 255, 18)
        painter.setFont(self._font)
        flags = self._wrap_flags()
        width = self.width()
        for (top, height), line, icon in zip(self._place(width), self._lines, self._icons):
            if top > event.rect().bottom() or top + height < event.rect().top():
                continue
            painter.fillRect(QRectF(0, top, width, 1), divider)
            color = QColor(state_color(line.state))
            y = top + self.PAD
            if icon:
                try:
                    painter.drawPixmap(0, int(y + 3), 13, 13, get_cached_qta_pixmap(icon, color=color.name(), size=13))
                except Exception:
                    pass
            else:
                paint_dot(painter, QRectF(4, y + 6, 7, 7), color, hollow=line.state in _HOLLOW_STATES)
            left = self.MARK + self.GAP
            if line.text:
                # «Что измеряли» — приглушённо и в столбец, «что получилось» — основным цветом.
                painter.setPen(muted)
                painter.drawText(QRectF(left, y, self._name_width, height), flags, line.name)
                painter.setPen(text)
                painter.drawText(QRectF(self.text_left(), y, width - self.text_left(), height), flags, line.text)
            else:
                painter.setPen(text)
                painter.drawText(QRectF(left, y, width - left, height), flags, line.name)
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
    # Свой значок вместо значка состояния (узел дороги, сайт).
    icon: str = ""


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


def plain_tile(line: Line, icon: str = "") -> Tile:
    """Строка перечня → плитка без своей страницы: «Cloudflare · 1.1.1.1» и «адреса · 38 мс» по частям."""
    title, _separator, tag = line.name.partition(" · ")
    # Отметка-разделитель «── здесь стоит фильтр ──» на плитке — обычной фразой.
    if title.startswith("─"):
        title = title.strip("─ ").capitalize()
    parts = [part for part in line.text.split(" · ") if part and part != "—"]
    seconds = next((part for part in parts if part.endswith(" мс")), "")
    result = " · ".join(part for part in parts if part != seconds)
    hint = "\n".join(part for part in (line.name.strip("─ "), line.text) if part)
    return Tile(line.state, title, tag, result, seconds, hint, icon if line.state == "info" else "")


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


_STATE_WORDS = {"ok": "В порядке", "warn": "Работает не полностью", "fail": "Мешает работе"}


def finding_detail_card(text: str, state: str, parts: FindingParts | None = None) -> Card:
    """Отчёт по одной находке: что найдено, какие серверы названы и что это значит.

    ``parts`` — находка готовыми частями: тогда названы все серверы. Без них
    (отчёт прошлой проверки) фраза делится здесь, и перечень в ней обрезан.
    """
    if parts is not None:
        title, servers, more, rest = parts.title, parts.services(), 0, parts.note
    else:
        title, detail = split_finding(text)
        servers, more, rest = split_server_list(detail)
    sections = [Section("Что найдено", (Line(state, str(text)),))]
    if servers:
        lines = [Line("info", name, ", ".join(addresses) or "адрес не назван") for name, addresses in servers]
        if more:
            lines.append(Line("info", f"и ещё {more}", "полный список — в отчёте «DNS-серверы», кнопка «Подробный текст»"))
        sections.append(Section("Серверы", tuple(lines)))
    if rest:
        sections.append(Section("Что это значит", (Line("info", rest),)))
    level = state if state in ("ok", "warn", "fail") else "unknown"
    return Card(
        key=f"finding:{title}",
        icon="fa5s.network-wired",
        title=title,
        level=level,
        status=_STATE_WORDS.get(state, "К сведению"),
        sections=tuple(sections),
    )


def service_card(service: ServiceSummary, columns: list[str]) -> Card:
    """Отчёт по одному DNS-сервису: что ответил каждый его адрес каждым способом связи."""
    sections = []
    for row in service.rows:
        lines = tuple(
            Line(cell_state(cell) if cell_state(cell) != "none" else "info", title, cell or "не проверялось")
            for title, cell in zip(columns, row.cells)
        )
        sections.append(Section(row.address, lines))
    working = sum(1 for row in service.rows if any(cell_state(cell) in ("ok", "warn") for cell in row.cells[1:]))
    total = len(service.rows)
    level = "ok" if working == total else "fail" if working == 0 else "warn"
    return Card(
        key=f"dns_service:{service.name}",
        icon="fa5s.network-wired",
        title=service.name,
        level=level,
        status=f"Отвечает {working} из {total} адресов",
        sections=tuple(sections),
    )


def wants_findings(section: Section, card: Card) -> bool:
    """Раздел — выводы проверки DNS-серверов одной фразой каждая: их показывают по частям, с метками серверов."""
    return card.key == "dns_servers" and bool(section.lines) and not any(line.text for line in section.lines)


def finding_card(line: Line, parent=None) -> FindingCard:
    """Вывод про DNS карточкой: заголовок, серверы метками, остальное — в подсказке."""
    if line.parts is not None:
        # Проверка отдала находку частями: перечень полный, фразу резать не нужно.
        title, servers, more, rest, detail = line.parts.title, line.parts.services(), 0, line.parts.note, line.parts.detail()
    else:
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
    if section.tiles:
        return bool(section.lines)
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

    def __init__(self, tiles: list[Tile], parent=None, *, clickable: bool = True, min_width: int = 0) -> None:
        super().__init__(parent)
        self._tiles = list(tiles)
        self._hover = -1
        # Плитке без своей страницы открывать нечего: курсор обычный, нажатие ничего не делает.
        self._clickable = clickable
        if min_width:
            self.MIN_WIDTH = int(min_width)
        if clickable:
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
        if self._clickable and event.button() == Qt.MouseButton.LeftButton and index >= 0:
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
                name = tile.icon or _STATE_ICONS.get(tile.state, _STATE_ICONS["unknown"])
                icon = get_cached_qta_pixmap(name, color=state_color("unknown") if tile.icon else color, size=15)
                painter.drawPixmap(int(rect.left() + 11), int(rect.top() + (self.HEIGHT - 15) / 2), 15, 15, icon)
            except Exception:
                pass
            left = rect.left() + 36
            right = rect.right() - 10
            second = " · ".join(part for part in (tile.tag, tile.result) if part)
            # Плитка из одного названия (имя сайта): оно стоит по центру, а не прижато к верху.
            first_top = rect.top() + (6 if second else (self.HEIGHT - 17) / 2)
            # Время — справа в первой строке; название занимает остальное.
            painter.setFont(small_font)
            painter.setPen(muted)
            seconds_width = small_metrics.horizontalAdvance(tile.seconds) + 8 if tile.seconds else 0
            if tile.seconds:
                painter.drawText(
                    QRectF(right - seconds_width, first_top, seconds_width, 17),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    tile.seconds,
                )
            painter.drawText(
                QRectF(left, rect.top() + 24, right - left, 17),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                small_metrics.elidedText(second, Qt.TextElideMode.ElideRight, int(right - left)),
            )
            painter.setFont(title_font)
            painter.setPen(text)
            title_width = right - left - seconds_width
            painter.drawText(
                QRectF(left, first_top, title_width, 17),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                title_metrics.elidedText(tile.title, Qt.TextElideMode.ElideRight, int(title_width)),
            )
        painter.end()


class _SectionBlock(QWidget):
    """Раздел отчёта: значок, название, сводка «сколько в порядке» с полосой и строки-таблица."""

    # Просят открыть текст раздела на всю страницу: (название, текст).
    text_opened = pyqtSignal(str, str)
    # Нажали карточку раздела (сервер, находку, сервис): отчёт на уровень глубже.
    child_opened = pyqtSignal(object)

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
        # Раздел из карточек — без своей подложки: она есть у каждой карточки внутри.
        self._flat = tiles or findings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*((0, 8, 0, 4) if self._flat else (16, 12, 16, 8)))
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
        self.table: RowsTable | None = None
        self.rows: list = []
        self.findings_flow: CardsFlow | None = None
        if tiles:
            if section.tiles:
                tiles_list = [plain_tile(line, section.tile_icon) for line in section.lines]
                self.grid = TilesGrid(tiles_list, self, clickable=False)
            else:
                self.grid = TilesGrid([line_tile(line) for line in section.lines], self)
                self.grid.opened.connect(lambda index: self.child_opened.emit(server_card(section.lines[index], section)))
            layout.addSpacing(2)
            layout.addWidget(self.grid)
            layout.addSpacing(6)
        elif findings:
            # Выводы — сеткой карточек, как находки DNS в итоге проверки.
            self.findings_flow = CardsFlow(self, min_width=FindingCard.MIN_WIDTH, card_height=FindingCard.HEIGHT)
            self.rows = [finding_card(line, self.findings_flow) for line in section.lines]
            for card, line in zip(self.rows, section.lines):
                card.set_clickable()
                card.clicked.connect(lambda item=line: self.child_opened.emit(finding_detail_card(item.name, item.state, item.parts)))
                self.findings_flow.add(card)
            layout.addSpacing(2)
            layout.addWidget(self.findings_flow)
            layout.addSpacing(6)
        else:
            # Строки рисует один виджет: строка-виджет на каждое измерение делала отчёт тяжёлым.
            self.table = RowsTable(section.lines, [line_icon(line, section) for line in section.lines], name_width, self)
            layout.addWidget(self.table)
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
            self.matrix.opened.connect(
                lambda index: self.child_opened.emit(service_card(self.matrix.services()[index], self.matrix.columns()))
            )
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

    def set_narrow(self) -> None:
        """Раздел стоит в столбце рядом с другими: полоса долей короче, чтобы заголовок помещался."""
        if self.bar is not None:
            self.bar.setFixedWidth(56)

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        if self._flat:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 12) if _is_light() else QColor(255, 255, 255, 11))
        painter.drawRoundedRect(self.rect(), CARD_RADIUS, CARD_RADIUS)
        painter.end()


def is_narrow_section(section: Section) -> bool:
    """Раздел из коротких строк «что измеряли — что получилось» (пинг, порты одного сервера).

    Во всю ширину окна такой раздел на три четверти пуст, поэтому соседние
    узкие разделы встают в несколько столбцов.
    """
    if section.text or not section.lines:
        return False
    return all(len(line.name) <= NARROW_NAME and len(line.text) <= NARROW_TEXT for line in section.lines)


class _BlocksFlow(QWidget):
    """Узкие разделы отчёта в несколько столбцов: сколько помещается по ширине окна."""

    MIN_WIDTH = 380
    # Число столбцов изменилось — высота отчёта стала другой.
    replaced = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(8)
        # Ширину задаёт окно: иначе три столбца не дали бы странице сузиться обратно до одного.
        self._grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._blocks: list[QWidget] = []
        self._columns = 0

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, self._grid.minimumSize().height())

    def blocks(self) -> list[QWidget]:
        return list(self._blocks)

    def add(self, block: QWidget) -> None:
        block.setParent(self)
        self._blocks.append(block)
        self._place(force=True)

    def columns_for(self, width: int) -> int:
        fit = max(1, (width + 8) // (self.MIN_WIDTH + 8))
        return max(1, min(fit, len(self._blocks)))

    def columns(self) -> int:
        return self._columns

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place()

    def _place(self, *, force: bool = False) -> None:
        columns = self.columns_for(self.width())
        if columns == self._columns and not force:
            return
        for column in range(max(columns, self._columns)):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        self._columns = columns
        # Сетка не переставляет виджет сама: сначала убрать все места, потом раздать заново.
        while self._grid.count():
            self._grid.takeAt(0)
        for order, block in enumerate(self._blocks):
            self._grid.addWidget(block, order // columns, order % columns, Qt.AlignmentFlag.AlignTop)
        self._grid.invalidate()
        self._grid.activate()
        self.updateGeometry()
        self.replaced.emit()


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
        self._flows: list[_BlocksFlow] = []

    def card(self) -> Card | None:
        return self._card

    def show_card(self, card: Card, *, parent_title: str = "") -> None:
        """``parent_title`` — промежуточный шаг строки пути: прошлая проверка, из которой открыт отчёт."""
        parent_title = str(parent_title or "")
        # Тот же отчёт открывают повторно: страница уже собрана, заново её не строим.
        if card == self._card and parent_title == self._parent_title and not self._ancestors and self.blocks:
            self.copy_button.setText("Скопировать")
            return
        self._parent_title = parent_title
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
            block.child_opened.connect(self.open_child)
            self.blocks.append(block)
        for flow in self._flows:
            flow.setParent(None)
            flow.deleteLater()
        self._flows = []
        narrow = [is_narrow_section(block.section) and block.table is not None for block in self.blocks]
        flow: _BlocksFlow | None = None
        for order, block in enumerate(self.blocks):
            # Узкие разделы, стоящие подряд, делят строку; одиночный узкий остаётся как был.
            paired = narrow[order] and (
                (order > 0 and narrow[order - 1]) or (order + 1 < len(narrow) and narrow[order + 1])
            )
            if not paired:
                flow = None
                self._sections_layout.addWidget(block)
            else:
                if flow is None:
                    flow = _BlocksFlow(self._sections_host)
                    self._flows.append(flow)
                    flow.replaced.connect(self._sync_height, Qt.ConnectionType.QueuedConnection)
                    self._sections_layout.addWidget(flow)
                block.set_narrow()
                flow.add(block)
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
