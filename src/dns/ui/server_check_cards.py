"""Карточки серверов для вкладки «DNS-серверы».

Вместо таблицы «строка на адрес» — карточка на сервер: его значок, что с ним
в целом (работает, блокируется по дороге, работает не полностью, сам
фильтрует, молчит), короткое замечание и точки по способам связи. Нажатие на
карточку открывает окно подробностей по адресам.

На каждой карточке — «дорожка» от компьютера до сервера, по которой бегут
запросы: доходят до сервера, упираются в преграду посреди дороги или
возвращаются от самого сервера (он отказал сам). Так видно, кто мешает, ещё
до чтения текста. Дорожки идут от общего такта кадров (``ui.frame_clock``),
и на кадр перерисовывается только полоска дорожки, а не карточка целиком.

Карточки рисует делегат списка, а не отдельные виджеты: Qt рисует только то,
что попало в видимую часть страницы, и сотня адресов ничего не стоит.

Своей прокрутки у списка нет: он вытянут на всю высоту содержимого, а
прокручивает его страница (иначе получалась полоса прокрутки внутри полосы
прокрутки). На широком окне карточки идут в несколько колонок.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QRegion
from PyQt6.QtWidgets import QAbstractButton, QAbstractItemView, QHBoxLayout, QSizePolicy, QStyledItemDelegate, QWidget
from qfluentwidgets import ListView

import dns.server_check_plans as plans
from profile.ui.profile_icon import profile_icon_pixmap
from ui.accessibility import set_control_accessibility, set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.frame_clock import frame_clock
from ui.theme import get_theme_tokens, to_qcolor
from ui.theme_refresh import ThemeRefreshBinding
from ui.theme_semantic import get_semantic_palette
from ui.widgets.fluent_item_tooltip import FLUENT_ITEM_TOOLTIP_ROLE, install_fluent_item_tooltips
from ui.widgets.share_bar import ShareBar

CARD_ROLE = Qt.ItemDataRole.UserRole + 1
FILTER_ALL = "all"

_CARD_HEIGHT = 156
# Уже этого карточка плохо читается: плашка с выводом начинает теснить имя сервера.
_COLUMN_MIN_WIDTH = 420
_COLUMN_GAP = 8
_MAX_COLUMNS = 3
_SIDE = 16
_DOT = 8
_ICON_TILE = 40
_ICON = 22
_TRANSPORT_LABELS = ("UDP", "TCP", "DoT", "DoH")
# Полоса «насколько всё плохо» читается слева направо: от рабочих к молчащим.
_BAR_ORDER = (plans.CARD_OK, plans.CARD_SELF, plans.CARD_PARTIAL, plans.CARD_NETWORK, plans.CARD_SILENT)
_REVEAL_MS = 420
_REVEAL_STEP_MS = 55
_REVEAL_ROWS = 9
_REVEAL_RISE = 12.0
# Дорожка «компьютер → сервер»: полоска внутри карточки и запросы на ней.
_ROAD_TOP = 64
_ROAD_HEIGHT = 30
_ROAD_FRAME_MS = 33
_ROAD_PERIOD_S = 2.6
_ROAD_PACKETS = 3
# Где на дороге стоит преграда (доля пути).
_ROAD_BARRIER = 0.56
# Кадр для неподвижной картинки, когда живые анимации выключены.
_ROAD_STILL = 0.3


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

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._cards)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._cards):
            return None
        card = self._cards[index.row()]
        if role == CARD_ROLE:
            return card
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


def _road_rect(box: QRectF) -> QRectF:
    return QRectF(box.left() + _SIDE, box.top() + _ROAD_TOP, box.width() - 2 * _SIDE, _ROAD_HEIGHT)


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
        return QSize(self._view.column_width(), _CARD_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        card = index.data(CARD_ROLE)
        if card is None:
            return
        hovered = self._view.hovered_row() == index.row()
        box = self._view.card_box(option.rect, index.row())
        accent = status_color(card.status)
        painter.save()
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        if self._view.road_pass():
            # Кадр дорожки: перерисовываем только её полоску, остальная карточка не менялась.
            road = _road_rect(box)
            painter.setClipRect(road.adjusted(-1, -1, 1, 1), Qt.ClipOperation.IntersectClip)
            self._background(painter, box, hovered)
            self._road(painter, road, card.status, accent, self._view.road_phase(index.row()))
            painter.restore()
            return
        reveal = self._view.reveal_of(index.row())
        if reveal < 1.0:
            painter.setOpacity(reveal)
            painter.translate(0.0, (1.0 - reveal) * _REVEAL_RISE)
        self._background(painter, box, hovered)

        base = QFont(option.font)
        title_font = QFont(base)
        title_font.setBold(True)
        title_font.setPixelSize(15)
        small = QFont(base)
        small.setPixelSize(12)
        metrics = QFontMetrics(small)
        left, right = box.left() + _SIDE, box.right() - _SIDE

        # Значок сервера в цветном кружке.
        tint = to_qcolor(card.color, accent.name()) if card.color else QColor(accent)
        tile = QRectF(left, box.top() + 14, _ICON_TILE, _ICON_TILE)
        back = QColor(tint)
        back.setAlpha(46)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(back)
        painter.drawEllipse(tile)
        icon = profile_icon_pixmap(card.icon or "fa5s.server", color=tint.name(), size=_ICON)
        if not icon.isNull():
            painter.drawPixmap(int(tile.center().x() - _ICON / 2), int(tile.center().y() - _ICON / 2), icon)

        # Справа вверху — плашка с выводом.
        painter.setFont(small)
        pill_text = plans.CARD_TITLES[card.status]
        pill_width = metrics.horizontalAdvance(pill_text) + 20
        pill = QRectF(right - pill_width, box.top() + 14, pill_width, 22)
        fill = QColor(accent)
        fill.setAlpha(40)
        painter.setBrush(fill)
        painter.drawRoundedRect(pill, 11, 11)
        painter.setPen(accent)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, pill_text)

        # Имя сервера и под ним — сколько адресов и лучшее время.
        text_left = tile.right() + 12
        name_width = int(max(40.0, pill.left() - 10 - text_left))
        painter.setFont(title_font)
        painter.setPen(_text_color())
        painter.drawText(
            QRectF(text_left, box.top() + 12, name_width, 22),
            Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(title_font).elidedText(card.server, Qt.TextElideMode.ElideRight, name_width),
        )
        painter.setFont(small)
        painter.setPen(_text_color(140))
        facts = " · ".join(part for part in (f"адресов: {len(card.addresses)}", card.best) if part)
        painter.drawText(QRectF(text_left, box.top() + 34, right - text_left, 20), Qt.AlignmentFlag.AlignVCenter, facts)

        self._road(painter, _road_rect(box), card.status, accent, self._view.road_phase(index.row()))

        # Что не так — одной строкой; целиком — в подробностях по нажатию.
        note = card.note or ("Без замечаний" if card.status == plans.CARD_OK else plans.CARD_TITLES[card.status])
        painter.setPen(_text_color(220 if card.note else 140))
        width = int(right - left)
        painter.drawText(
            QRectF(left, box.top() + 100, width, 20),
            Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(note, Qt.TextElideMode.ElideRight, width),
        )

        # По точке на адрес для каждого способа связи.
        x, y = left, box.top() + 132
        for label, levels in zip(_TRANSPORT_LABELS, card.dots):
            painter.setPen(_text_color(130))
            painter.drawText(QRectF(x, y - 10, 40, 20), Qt.AlignmentFlag.AlignVCenter, label)
            x += metrics.horizontalAdvance(label) + 7
            for level in levels:
                self._dot(painter, QPointF(x + _DOT / 2, y), level)
                x += _DOT + 3
            x += 12
        painter.restore()

    def _background(self, painter: QPainter, box: QRectF, hovered: bool) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_text_color(24 if hovered else 13))
        painter.drawRoundedRect(box, 10, 10)

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

    # --- дорожка «компьютер → сервер» -------------------------------------------

    def _road(self, painter: QPainter, rect: QRectF, status: str, accent: QColor, phase: float) -> None:
        y = rect.center().y()
        start, end = rect.left() + 30, rect.right() - 30
        silent = status == plans.CARD_SILENT

        # Компьютер слева, сервер справа.
        outline = QPen(_text_color(150), 1.4)
        outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(outline)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(rect.left() + 2, y - 8, 18, 12), 2.5, 2.5)
        painter.drawLine(QPointF(rect.left() + 7, y + 8), QPointF(rect.left() + 15, y + 8))
        server = QPen(_text_color(70) if silent else accent, 1.4)
        if silent:
            server.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(server)
        for offset in (-9.0, 1.0):
            painter.drawRoundedRect(QRectF(rect.right() - 20, y + offset, 18, 8), 2.5, 2.5)

        # Сама дорога — пунктиром.
        line = QPen(_text_color(46), 1.4)
        line.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(line)
        painter.drawLine(QPointF(start, y), QPointF(end, y))

        blocked = status in (plans.CARD_NETWORK, plans.CARD_PARTIAL)
        barrier_x = start + (end - start) * _ROAD_BARRIER
        if blocked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(barrier_x - 2, y - 10, 4, 20), 2, 2)

        good = status_color(plans.CARD_OK)
        painter.setPen(Qt.PenStyle.NoPen)
        for number in range(_ROAD_PACKETS):
            t = (phase + number / _ROAD_PACKETS) % 1.0
            # В «работает не полностью» через преграду проходит каждый второй запрос.
            stopped = blocked and (status == plans.CARD_NETWORK or number % 2 == 0)
            color, alpha, position = QColor(good), 1.0, t
            if stopped:
                # Доехал до преграды и гаснет возле неё.
                position = _ROAD_BARRIER * min(1.0, t / 0.7) - 0.02
                if t > 0.7:
                    color, alpha = QColor(accent), 1.0 - (t - 0.7) / 0.3
            elif status == plans.CARD_SELF:
                # Доехал до сервера — и сервер вернул отказ: обратно едет уже его цветом.
                if t > 0.72:
                    color, position = QColor(accent), 1.0 - (t - 0.72) / 0.28 * 0.3
                else:
                    position = t / 0.72
            elif silent:
                # Уходит в пустоту: бледнеет и не доезжает.
                color, alpha, position = _text_color(200), max(0.0, 1.0 - t * 1.25), t * 0.85
            # Плавное появление у компьютера и исчезновение в конце пути.
            alpha *= min(1.0, t / 0.08)
            if not stopped:
                alpha *= min(1.0, (1.0 - t) / 0.08)
            color.setAlphaF(max(0.0, min(1.0, alpha)) * color.alphaF())
            painter.setBrush(color)
            painter.drawEllipse(QPointF(start + (end - start) * position, y), 3.2, 3.2)


class ServerCardsView(ListView):
    """Карточки серверов: во всю высоту содержимого, на широком окне — в несколько колонок.

    Нажатие на карточку (или Enter) — сигнал ``opened(имя сервера)``: страница
    открывает по нему окно подробностей.
    """

    opened = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all: tuple[plans.ServerCard, ...] = ()
        self._filter = FILTER_ALL
        self._hovered = -1
        self._reveal = 1.0
        self._road_pending = False
        self._road_pass = False
        self._road_region = QRegion()
        self._road_started = time.monotonic()
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
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.viewport().installEventFilter(self)
        self.clicked.connect(self._open)
        self.activated.connect(self._open)
        install_fluent_item_tooltips(self)
        self._reveal_anim = QVariantAnimation(self)
        self._reveal_anim.setStartValue(0.0)
        self._reveal_anim.setEndValue(1.0)
        self._reveal_anim.setDuration(_REVEAL_MS + _REVEAL_STEP_MS * _REVEAL_ROWS)
        self._reveal_anim.valueChanged.connect(self._on_reveal)
        self._road_frames = frame_clock().subscribe(self._on_road_frame, interval_ms=_ROAD_FRAME_MS, owner=self)
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.viewport().update())
        set_control_accessibility(
            self,
            name="DNS-серверы",
            description=(
                "По карточке на сервер: что с ним в целом и какие способы связи работают. "
                "Enter или нажатие открывает подробности по адресам."
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
        self._sync_road()
        counts = plans.count_cards(self._all)
        problems = counts[plans.CARD_NETWORK] + counts[plans.CARD_PARTIAL]
        set_state_text(self, f"DNS-серверы: показано {len(shown)} из {len(self._all)}, с проблемами {problems}")

    def _open(self, index) -> None:
        card = index.data(CARD_ROLE)
        if card is not None:
            self.opened.emit(card.server)

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

    def card_box(self, rect: QRect, row: int) -> QRectF:
        """Сама карточка внутри ячейки списка."""
        # Зазор между колонками; у последней колонки его нет — край ровно по полосе сверху.
        gap = 0 if self.is_last_column(row) else _COLUMN_GAP
        return QRectF(rect).adjusted(0, 4, -gap, -4)

    def _fit_height(self) -> None:
        """Высота ровно по содержимому: все карточки одного роста, рядов — сколько нужно."""
        rows = -(-self._model.rowCount() // self.columns())
        height = 2 * self.frameWidth() + rows * _CARD_HEIGHT
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

    # --- дорожки --------------------------------------------------------------

    def road_pass(self) -> bool:
        """Идёт ли сейчас перерисовка одних только дорожек."""
        return self._road_pass

    def road_phase(self, row: int) -> float:
        """Где на дорожке запросы (0..1). У соседних карточек — со сдвигом, чтобы не бежали строем."""
        if not self._road_frames.isActive():
            return _ROAD_STILL
        return ((time.monotonic() - self._road_started) / _ROAD_PERIOD_S + row * 0.37) % 1.0

    def is_road_running(self) -> bool:
        return self._road_frames.isActive()

    def _sync_road(self) -> None:
        wanted = self.isVisible() and bool(self._model.rowCount()) and are_live_animations_enabled()
        if wanted and not self._road_frames.isActive():
            self._road_frames.start()
        elif not wanted and self._road_frames.isActive():
            self._road_frames.stop()
            self.viewport().update()

    def _visible_roads(self) -> QRegion:
        """Полоски дорожек у карточек, которые сейчас видны на экране.

        Список вытянут на всё содержимое, а видна только часть, попавшая в
        окно страницы: остальные карточки не трогаем.
        """
        seen = self.viewport().visibleRegion().boundingRect()
        region = QRegion()
        if seen.isEmpty():
            return region
        for row in range(self._model.rowCount()):
            rect = self.visualRect(self._model.index(row))
            if rect.intersects(seen):
                region += _road_rect(self.card_box(rect, row)).toAlignedRect()
        return region

    def _on_road_frame(self) -> None:
        if not are_live_animations_enabled():
            self._sync_road()
            return
        self._road_region = self._visible_roads()
        if self._road_region.isEmpty():
            return
        self._road_pending = True
        self.viewport().update(self._road_region)

    def paintEvent(self, event) -> None:  # noqa: N802
        # Кадр дорожек — это когда перерисовать просили только их полоски. Если в ту же
        # перерисовку попало что-то ещё (наведение, прокрутка), рисуем карточки целиком.
        self._road_pass = self._road_pending and event.region().subtracted(self._road_region).isEmpty()
        self._road_pending = False
        try:
            super().paintEvent(event)
        finally:
            self._road_pass = False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_road()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._sync_road()

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
