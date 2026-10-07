"""Карточки серверов для вкладки «DNS-серверы».

Вместо таблицы «строка на адрес» — карточка на сервер: что с ним в целом
(работает, блокируется по дороге, работает не полностью, сам фильтрует, молчит), короткое замечание
и точки по способам связи. Адреса с временем ответа раскрываются по нажатию.

Карточки рисует делегат списка, а не отдельные виджеты: Qt рисует только то,
что попало в видимую часть страницы, и сотня адресов ничего не стоит.

Своей прокрутки у списка нет: он вытянут на всю высоту содержимого, а
прокручивает его страница (иначе получалась полоса прокрутки внутри полосы
прокрутки). На широком окне карточки идут в несколько колонок.
"""

from __future__ import annotations

from PyQt6.QtCore import QAbstractListModel, QEvent, QModelIndex, QPointF, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton, QAbstractItemView, QHBoxLayout, QSizePolicy, QStyledItemDelegate, QWidget
from qfluentwidgets import ListView

import dns.server_check_plans as plans
from ui.accessibility import set_control_accessibility, set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.theme import get_theme_tokens, to_qcolor
from ui.theme_refresh import ThemeRefreshBinding
from ui.theme_semantic import get_semantic_palette
from ui.widgets.fluent_item_tooltip import FLUENT_ITEM_TOOLTIP_ROLE, install_fluent_item_tooltips
from ui.widgets.share_bar import ShareBar

CARD_ROLE = Qt.ItemDataRole.UserRole + 1
EXPANDED_ROLE = Qt.ItemDataRole.UserRole + 2
FILTER_ALL = "all"

_CARD_HEIGHT = 86
_ADDRESS_HEIGHT = 24
_EXPANDED_PADDING = 10
# Карточка уже этого плохо читается: раскрытая таблица адресов перестаёт помещаться.
_COLUMN_MIN_WIDTH = 620
_COLUMN_GAP = 8
_MAX_COLUMNS = 3
_SIDE = 18
_DOT = 8
_ADDRESS_WIDTH = 250
_CELL_WIDTH = 132
_TRANSPORT_LABELS = ("UDP", "TCP", "DoT", "DoH")
_ADDRESS_LABELS = ("Пинг", "UDP", "TCP", "DoT", "DoH")
# Полоса «насколько всё плохо» читается слева направо: от рабочих к молчащим.
_BAR_ORDER = (plans.CARD_OK, plans.CARD_SELF, plans.CARD_PARTIAL, plans.CARD_NETWORK, plans.CARD_SILENT)
_REVEAL_MS = 420
_REVEAL_STEP_MS = 55
_REVEAL_ROWS = 9
_REVEAL_RISE = 12.0


def _text_color(alpha: int = 255) -> QColor:
    color = QColor(Qt.GlobalColor.black if get_theme_tokens().is_light else Qt.GlobalColor.white)
    color.setAlpha(alpha)
    return color


def status_color(status: str) -> QColor:
    """Цвет вывода: зелёный — работает, красный — доказанно мешает сеть, оранжевый — работает не полностью, цвет акцента — решил сам сервер."""
    palette = get_semantic_palette()
    if status == plans.CARD_OK:
        return to_qcolor(palette.success_text, "#6ccb5f")
    if status == plans.CARD_NETWORK:
        return to_qcolor(palette.error_text, "#ff6b6b")
    if status == plans.CARD_PARTIAL:
        return to_qcolor(palette.warning_text, "#ff9800")
    if status == plans.CARD_SELF:
        return to_qcolor(get_theme_tokens().accent_hex, "#60cdff")
    return _text_color(120)


def _cell_color(level: str) -> QColor:
    palette = get_semantic_palette()
    if level == plans.CELL_OK:
        return to_qcolor(palette.success_text, "#6ccb5f")
    if level == plans.CELL_WARN:
        return to_qcolor(palette.warning_text, "#ff9800")
    if level == plans.CELL_FAIL:
        return to_qcolor(palette.error_text, "#ff6b6b")
    return _text_color(110)


class SeverityBar(ShareBar):
    """Одна полоса из цветных долей: сколько серверов работает, скольким мешают, сколько молчит."""

    def __init__(self, parent=None) -> None:
        super().__init__(status_color, parent)
        self._counts: dict[str, int] = {}

    def counts(self) -> dict[str, int]:
        return dict(self._counts)

    def set_counts(self, counts: dict[str, int], *, animate: bool = False) -> None:
        self._counts = {status: int(counts.get(status, 0)) for status in _BAR_ORDER}
        words = ", ".join(
            f"{plans.CARD_GROUPS[status].lower()} {count}" for status, count in self._counts.items() if count
        )
        set_state_text(self, f"Серверы: {words or 'нет данных'}")
        self.set_segments(self._counts.items(), animate=animate and self.isVisible())


class StatusChip(QAbstractButton):
    """Кнопка-фильтр: цветная точка, подпись и число серверов."""

    def __init__(self, key: str, parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self._caption = ""
        self._count = 0
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())

    def count(self) -> int:
        return self._count

    def set_content(self, caption: str, count: int, description: str) -> None:
        self._caption, self._count = caption, int(count)
        self.setText(f"{caption} {count}")
        metrics = QFontMetrics(self.font())
        dot = 0 if self.key == FILTER_ALL else _DOT + 6
        self.setFixedSize(metrics.horizontalAdvance(self.text()) + 24 + dot, metrics.height() + 12)
        set_control_accessibility(self, name=f"{caption}: {count}", description=description)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = rect.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_text_color(40 if self.isChecked() else 26 if self.underMouse() else 14))
        painter.drawRoundedRect(rect, radius, radius)
        x = rect.left() + 12
        if self.key != FILTER_ALL:
            painter.setBrush(status_color(self.key))
            painter.drawEllipse(QPointF(x + _DOT / 2, rect.center().y()), _DOT / 2, _DOT / 2)
            x += _DOT + 6
        painter.setPen(_text_color(255 if self.isChecked() else 200))
        painter.drawText(QRectF(x, rect.top(), rect.right() - x, rect.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        painter.end()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()


class StatusFilter(QWidget):
    """Ряд кнопок-фильтров: «Все» и по одной на каждый вывод, у которого есть серверы."""

    changed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.chips: dict[str, StatusChip] = {}
        for key in (FILTER_ALL, *plans.CARD_ORDER):
            chip = StatusChip(key, self)
            chip.clicked.connect(lambda _checked=False, k=key: self.changed.emit(k))
            layout.addWidget(chip)
            self.chips[key] = chip
        layout.addStretch(1)
        self.chips[FILTER_ALL].setChecked(True)
        self.set_counts({})

    def current(self) -> str:
        return next((key for key, chip in self.chips.items() if chip.isChecked()), FILTER_ALL)

    def set_counts(self, counts: dict[str, int]) -> None:
        self.chips[FILTER_ALL].set_content("Все", sum(counts.values()), "Показать все серверы.")
        for status in plans.CARD_ORDER:
            chip, count = self.chips[status], int(counts.get(status, 0))
            chip.set_content(plans.CARD_GROUPS[status], count, plans.CARD_HINTS[status])
            # Пустая группа не нужна; но выбранную не прячем, пока человек сам не уйдёт с неё.
            chip.setVisible(count > 0 or chip.isChecked())


class _CardsModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cards: tuple[plans.ServerCard, ...] = ()
        self._expanded: set[str] = set()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._cards)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._cards):
            return None
        card = self._cards[index.row()]
        if role == CARD_ROLE:
            return card
        if role == EXPANDED_ROLE:
            return card.server in self._expanded
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return card.spoken
        if role == FLUENT_ITEM_TOOLTIP_ROLE:
            return card.tooltip
        return None

    def cards(self) -> tuple[plans.ServerCard, ...]:
        return self._cards

    def set_cards(self, cards) -> None:
        cards = tuple(cards)
        if cards == self._cards:
            return
        same_shape = [card.server for card in cards] == [card.server for card in self._cards]
        if same_shape:
            # Те же серверы на тех же местах: обновляем на месте, список не дёргается.
            self._cards = cards
            self.dataChanged.emit(self.index(0), self.index(len(cards) - 1))
            return
        self.beginResetModel()
        self._cards = cards
        self.endResetModel()

    def toggle(self, index) -> None:
        card = self.data(index, CARD_ROLE)
        if card is None:
            return
        self._expanded.symmetric_difference_update({card.server})
        self.dataChanged.emit(index, index, [EXPANDED_ROLE])

    def collapse_all(self) -> None:
        self._expanded.clear()


class _CardDelegate(QStyledItemDelegate):
    def __init__(self, view: "ServerCardsView") -> None:
        super().__init__(view)
        self._view = view

    # Список qfluentwidgets сообщает своему делегату о наведении, нажатии и
    # выделении. Без этих методов программа падала при движении мыши над списком.
    def setHoverRow(self, row: int) -> None:  # noqa: N802
        self._view.set_hovered_row(row)

    def setPressedRow(self, row: int) -> None:  # noqa: N802
        _ = row

    def setSelectedRows(self, indexes) -> None:  # noqa: N802
        _ = indexes

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(self._view.column_width(), self._view.card_height(index))

    def paint(self, painter: QPainter, option, index) -> None:
        card = index.data(CARD_ROLE)
        if card is None:
            return
        expanded = bool(index.data(EXPANDED_ROLE))
        hovered = self._view.hovered_row() == index.row()
        reveal = self._view.reveal_of(index.row())
        painter.save()
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        if reveal < 1.0:
            painter.setOpacity(reveal)
            painter.translate(0.0, (1.0 - reveal) * _REVEAL_RISE)
        # Зазор между колонками; у последней колонки его нет — край ровно по полосе сверху.
        gap = 0 if self._view.is_last_column(index.row()) else _COLUMN_GAP
        box = QRectF(option.rect).adjusted(0, 3, -gap, -3)
        accent = status_color(card.status)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_text_color(20 if hovered else 12))
        painter.drawRoundedRect(box, 8, 8)
        painter.setBrush(accent)
        painter.drawRoundedRect(QRectF(box.left(), box.top() + 10, 4, _CARD_HEIGHT - 26), 2, 2)

        base = QFont(option.font)
        title_font = QFont(base)
        title_font.setBold(True)
        title_font.setPixelSize(15)
        small = QFont(base)
        small.setPixelSize(12)
        left, right = box.left() + _SIDE, box.right() - _SIDE

        # Справа: стрелка раскрытия, плашка с выводом и лучшее время.
        self._chevron(painter, QPointF(right - 5, box.top() + 23), expanded)
        right -= 22
        painter.setFont(small)
        pill_text = plans.CARD_TITLES[card.status]
        pill_width = QFontMetrics(small).horizontalAdvance(pill_text) + 20
        pill = QRectF(right - pill_width, box.top() + 12, pill_width, 22)
        fill = QColor(accent)
        fill.setAlpha(40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(pill, 11, 11)
        painter.setPen(accent)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, pill_text)
        right = pill.left() - 14
        if card.best:
            best_width = QFontMetrics(small).horizontalAdvance(card.best)
            painter.setPen(_text_color(150))
            painter.drawText(QRectF(right - best_width, box.top() + 12, best_width, 22), Qt.AlignmentFlag.AlignVCenter, card.best)
            right -= best_width + 14

        # Слева: имя сервера и число адресов.
        painter.setFont(title_font)
        title_metrics = QFontMetrics(title_font)
        name = title_metrics.elidedText(card.server, Qt.TextElideMode.ElideRight, int(max(60.0, right - left - 90)))
        painter.setPen(_text_color())
        painter.drawText(QRectF(left, box.top() + 11, right - left, 24), Qt.AlignmentFlag.AlignVCenter, name)
        painter.setFont(small)
        painter.setPen(_text_color(130))
        count = len(card.addresses)
        painter.drawText(
            QRectF(left + title_metrics.horizontalAdvance(name) + 10, box.top() + 12, 120, 24),
            Qt.AlignmentFlag.AlignVCenter,
            f"адресов: {count}",
        )

        # Вторая строка: что не так — во всю ширину карточки.
        note = card.note or ("Без замечаний" if card.status == plans.CARD_OK else plans.CARD_HINTS[card.status])
        painter.setPen(_text_color(215 if card.note else 140))
        width = int(box.right() - _SIDE - left)
        painter.drawText(
            QRectF(left, box.top() + 36, width, 20),
            Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(small).elidedText(note, Qt.TextElideMode.ElideRight, width),
        )

        # Третья строка: по точке на адрес для каждого способа связи.
        x = left
        y = box.top() + 68
        metrics = QFontMetrics(small)
        for label, levels in zip(_TRANSPORT_LABELS, card.dots):
            painter.setPen(_text_color(130))
            painter.drawText(QRectF(x, y - 10, 40, 20), Qt.AlignmentFlag.AlignVCenter, label)
            x += metrics.horizontalAdvance(label) + 8
            for level in levels:
                self._dot(painter, QPointF(x + _DOT / 2, y), level)
                x += _DOT + 4
            x += 18

        if expanded:
            self._paint_addresses(painter, card, box, small)
        painter.restore()

    def _dot(self, painter: QPainter, center: QPointF, level: str) -> None:
        color = _cell_color(level)
        if level == plans.CELL_MUTED:
            # Способ не объявлен или ещё не проверен — пустой кружок.
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color, 1.2))
            painter.drawEllipse(center, _DOT / 2 - 0.6, _DOT / 2 - 0.6)
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(center, _DOT / 2, _DOT / 2)

    def _chevron(self, painter: QPainter, center: QPointF, expanded: bool) -> None:
        pen = QPen(_text_color(150), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        dy = -2.5 if expanded else 2.5
        painter.drawPolyline(
            QPointF(center.x() - 5, center.y() - dy), QPointF(center.x(), center.y() + dy), QPointF(center.x() + 5, center.y() - dy)
        )

    def _paint_addresses(self, painter: QPainter, card: plans.ServerCard, box: QRectF, font: QFont) -> None:
        painter.setFont(font)
        metrics = QFontMetrics(font)
        left = box.left() + _SIDE
        top = box.top() + _CARD_HEIGHT - 4
        painter.setPen(QPen(_text_color(26), 1))
        painter.drawLine(QPointF(left, top), QPointF(box.right() - _SIDE, top))
        # В узкой колонке таблица адресов сжимается, а не вылезает за карточку.
        available = box.width() - 2 * _SIDE
        address_width = int(min(_ADDRESS_WIDTH, available * 0.3))
        cell_width = int(min(_CELL_WIDTH, (available - address_width) / len(_ADDRESS_LABELS)))
        for order, row in enumerate(card.addresses):
            y = top + _EXPANDED_PADDING / 2 + order * _ADDRESS_HEIGHT
            painter.setPen(_text_color(230))
            painter.drawText(
                QRectF(left, y, address_width - 12, _ADDRESS_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter,
                metrics.elidedText(row.address, Qt.TextElideMode.ElideMiddle, address_width - 12),
            )
            x = left + address_width
            for label, text, level in zip(_ADDRESS_LABELS, row.cells, row.cell_levels):
                painter.setPen(_text_color(120))
                painter.drawText(QRectF(x, y, cell_width, _ADDRESS_HEIGHT), Qt.AlignmentFlag.AlignVCenter, label)
                painter.setPen(_cell_color(level) if level != plans.CELL_MUTED else _text_color(150))
                offset = metrics.horizontalAdvance(label) + 6
                value_width = cell_width - offset - 6
                painter.drawText(
                    QRectF(x + offset, y, value_width, _ADDRESS_HEIGHT),
                    Qt.AlignmentFlag.AlignVCenter,
                    metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(value_width)),
                )
                x += cell_width


class ServerCardsView(ListView):
    """Карточки серверов: во всю высоту содержимого, на широком окне — в несколько колонок."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all: tuple[plans.ServerCard, ...] = ()
        self._filter = FILTER_ALL
        self._hovered = -1
        self._reveal = 1.0
        self._model = _CardsModel(self)
        self.setModel(self._model)
        self.setItemDelegate(_CardDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Своей прокрутки нет: список вытянут по содержимому, прокручивает страница.
        self.scrollDelegate.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Карточки идут слева направо и переносятся на новую строку: так получаются колонки.
        self.setFlow(ListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(ListView.ResizeMode.Adjust)
        self.setSpacing(0)
        self._laid_out_width = -1
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.viewport().installEventFilter(self)
        self.clicked.connect(self.toggle)
        self.activated.connect(self.toggle)
        install_fluent_item_tooltips(self)
        self._reveal_anim = QVariantAnimation(self)
        self._reveal_anim.setStartValue(0.0)
        self._reveal_anim.setEndValue(1.0)
        self._reveal_anim.setDuration(_REVEAL_MS + _REVEAL_STEP_MS * _REVEAL_ROWS)
        self._reveal_anim.valueChanged.connect(self._on_reveal)
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.viewport().update())
        set_control_accessibility(
            self,
            name="DNS-серверы",
            description=(
                "По карточке на сервер: что с ним в целом и какие способы связи работают. "
                "Enter или нажатие раскрывает адреса с временем ответа."
            ),
        )
        self._fit_height()

    # --- данные ---------------------------------------------------------------

    def cards(self) -> tuple[plans.ServerCard, ...]:
        return self._model.cards()

    def filter(self) -> str:
        return self._filter

    def set_cards(self, cards) -> None:
        self._all = tuple(cards)
        self._apply()

    def set_filter(self, key: str) -> None:
        key = key if key in plans.CARD_ORDER else FILTER_ALL
        if key == self._filter:
            return
        self._filter = key
        self._apply()
        self.scrollToTop()

    def _apply(self) -> None:
        shown = tuple(card for card in self._all if self._filter in (FILTER_ALL, card.status))
        self._model.set_cards(shown)
        self._fit_height()
        counts = plans.count_cards(self._all)
        problems = counts[plans.CARD_NETWORK] + counts[plans.CARD_PARTIAL]
        set_state_text(self, f"DNS-серверы: показано {len(shown)} из {len(self._all)}, с проблемами {problems}")

    def toggle(self, index) -> None:
        self._model.toggle(index)
        self.itemDelegate().sizeHintChanged.emit(index)
        self._fit_height()

    def is_expanded(self, row: int) -> bool:
        return bool(self._model.index(row).data(EXPANDED_ROLE))

    # --- колонки и высота -----------------------------------------------------

    def _inner_width(self) -> int:
        # Именно окошко списка: оформление библиотеки оставляет поля по бокам,
        # и по ширине самого виджета последняя колонка не поместилась бы.
        return max(1, self.viewport().width())

    def columns(self) -> int:
        """Сколько колонок помещается: каждая не уже ``_COLUMN_MIN_WIDTH``."""
        fit = (self._inner_width() + _COLUMN_GAP) // (_COLUMN_MIN_WIDTH + _COLUMN_GAP)
        return max(1, min(_MAX_COLUMNS, fit))

    def column_width(self) -> int:
        # На точку уже окошка: Qt переносит карточку на новую строку, если ряд
        # занял окошко ровно до края.
        return max(1, (self._inner_width() - 1) // self.columns())

    def is_last_column(self, row: int) -> bool:
        return row % self.columns() == self.columns() - 1

    def card_height(self, index) -> int:
        card = index.data(CARD_ROLE)
        if card is not None and index.data(EXPANDED_ROLE):
            return _CARD_HEIGHT + _EXPANDED_PADDING + _ADDRESS_HEIGHT * len(card.addresses)
        return _CARD_HEIGHT

    def _fit_height(self) -> None:
        """Высота ровно по содержимому: строка карточек высотой с самую высокую в ней."""
        columns = self.columns()
        heights = [self.card_height(self._model.index(row)) for row in range(self._model.rowCount())]
        height = 2 * self.frameWidth() + sum(
            max(heights[start : start + columns]) for start in range(0, len(heights), columns)
        )
        if height != self.maximumHeight() or height != self.minimumHeight():
            self.setFixedHeight(height)

    def _relayout_for_width(self) -> None:
        if self._inner_width() != self._laid_out_width:
            # Ширина колонки зависит от ширины списка: размеры карточек надо пересчитать.
            self._laid_out_width = self._inner_width()
            self.scheduleDelayedItemsLayout()
            self._fit_height()

    # --- появление ------------------------------------------------------------

    def play_reveal(self) -> bool:
        """Карточки выплывают по очереди. False — анимации выключены или список не виден."""
        if not are_live_animations_enabled() or not self.isVisible() or not self._model.rowCount():
            return False
        self.scrollToTop()
        self._reveal_anim.stop()
        self._reveal_anim.start()
        return True

    def reveal_of(self, row: int) -> float:
        if self._reveal >= 1.0:
            return 1.0
        elapsed = self._reveal * self._reveal_anim.duration() - min(row, _REVEAL_ROWS) * _REVEAL_STEP_MS
        linear = max(0.0, min(1.0, elapsed / _REVEAL_MS))
        return 1.0 - (1.0 - linear) ** 3

    def _on_reveal(self, value) -> None:
        self._reveal = float(value)
        self.viewport().update()

    # --- мышь -----------------------------------------------------------------

    def hovered_row(self) -> int:
        return self._hovered

    def set_hovered_row(self, row: int) -> None:
        self._set_hovered(row)

    def _set_hovered(self, row: int) -> None:
        if row == self._hovered:
            return
        previous, self._hovered = self._hovered, row
        for changed in (previous, row):
            if changed >= 0:
                self.viewport().update(self.visualRect(self._model.index(changed)))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.viewport():
            if event.type() == QEvent.Type.Resize:
                self._relayout_for_width()
            elif event.type() == QEvent.Type.MouseMove:
                self._set_hovered(self.indexAt(event.position().toPoint()).row())
            elif event.type() in (QEvent.Type.Leave, QEvent.Type.Hide):
                self._set_hovered(-1)
        return super().eventFilter(obj, event)


__all__ = [
    "FILTER_ALL",
    "SeverityBar",
    "ServerCardsView",
    "StatusChip",
    "StatusFilter",
    "status_color",
]
