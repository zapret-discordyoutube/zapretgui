"""Страница подробностей по одному DNS-серверу: открывается нажатием на карточку.

Карточка показывает вывод коротко; здесь — всё, что о сервере узнали. Страница
занимает место вкладки «DNS-серверы», назад ведёт строка пути наверху.

Сверху вниз:

- шапка: значок и имя сервера, вывод, пояснение вывода простыми словами и
  замечания метками;
- сводка по способам связи: что это за способ и у скольких адресов он отвечает;
- по карточке на адрес: плитки способов связи с временем ответа и полоской
  скорости, выводы целыми фразами, кто на самом деле выполняет запросы и что
  сервер ответил про контрольные сайты обычным и шифрованным путём.

Что показывать, решает ``dns.server_check_plans``; здесь только рисование.
Вид строгий: подложки нейтральные, цвет — только маленькая точка у вывода и
значения, которые отклоняются от нормы (закрыт, отвечает через раз). То, что
работает как надо, написано обычным текстом и не спорит с тем, что сломано.
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPainter
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    BreadcrumbBar,
    CaptionLabel,
        PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)

import dns.server_check_plans as plans
import dns.server_check_verdict as verdicts
from dns.ui.server_check_cards import _cell_color, _text_color, status_color
from dns.ui.server_check_widgets import _FindingRow
from profile.ui.profile_icon import profile_icon_pixmap
from ui.accessibility import set_breadcrumb_accessibility, set_control_accessibility, set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.theme import get_theme_tokens, to_qcolor
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.stagger_float_in import float_in
from ui.widgets.tone_group import ToneDot, mute

_RADIUS = 8
_GAP = 10
# С этой ширины карточки адресов встают в два столбца.
_TWO_COLUMNS_FROM = 980
_REVEAL_MS = 700
_FLOAT_STEP_MS = 60
_ICON_TILE = 56
_ICON = 30
_DOT = 8
_SELECTABLE = Qt.TextInteractionFlag.TextSelectableByMouse


def _status_color(status: str) -> QColor:
    """Цвет вывода непрозрачным: им красят и текст, и метки."""
    if status == plans.CARD_SILENT:
        return _cell_color(plans.CELL_MUTED)
    return status_color(status)


def _solid(color: QColor) -> QColor:
    """Тот же цвет без прозрачности — для текста меток (у QLabel прозрачности цвета нет)."""
    base = _text_color()
    alpha = color.alphaF()
    if alpha >= 1.0:
        return QColor(color)
    back = 255 - base.red()
    return QColor(*(int(round(part * alpha + back * (1.0 - alpha))) for part in (color.red(), color.green(), color.blue())))


def _tint(label, color: QColor) -> None:
    solid = _solid(color)
    label.setTextColor(solid, solid)


def _paint_value(label, level: str) -> None:
    """Цветом выделяется только отклонение; обычный ответ остаётся обычным текстом."""
    if level in (plans.CELL_WARN, plans.CELL_FAIL):
        _tint(label, _cell_color(level))
    elif level == plans.CELL_MUTED:
        mute(label)


class _Surface(QWidget):
    """Нейтральная подложка со скруглением — одна на все блоки страницы."""

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(to_qcolor(get_theme_tokens().surface_bg, "#0affffff"))
        painter.drawRoundedRect(self.rect(), _RADIUS, _RADIUS)
        painter.end()


class _StatusMark(QWidget):
    """Вывод словами с цветной точкой: точка — единственное цветное пятно."""

    def __init__(self, status: str, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        # Кольцо вместо заливки: «не полностью» отличается от «блокируется» не только цветом.
        hollow = status in (plans.CARD_PARTIAL, plans.CARD_SILENT)
        dot = ToneDot(lambda _tokens: _solid(_status_color(status)).name(), self, size=9, hollow=hollow)
        layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = StrongBodyLabel(plans.CARD_TITLES[status], self)
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)

    def text(self) -> str:
        return self.label.text()


class _ServerIcon(QWidget):
    """Значок сервера в фирменном цвете на нейтральном кружке."""

    def __init__(self, card: plans.ServerCard, parent=None) -> None:
        super().__init__(parent)
        self._card = card
        self.setFixedSize(_ICON_TILE, _ICON_TILE)

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        accent = _status_color(self._card.status)
        tint = to_qcolor(self._card.color, accent.name()) if self._card.color else QColor(accent)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_text_color(16))
        painter.drawEllipse(self.rect())
        icon = profile_icon_pixmap(self._card.icon or "fa5s.server", color=tint.name(), size=_ICON)
        if not icon.isNull():
            painter.drawPixmap((self.width() - _ICON) // 2, (self.height() - _ICON) // 2, icon)
        painter.end()


class _Dots(QWidget):
    """По точке на адрес: зелёная — отвечает, оранжевая — через раз, красная — закрыт, пустая — не проверялся."""

    def __init__(self, levels: tuple[str, ...], parent=None) -> None:
        super().__init__(parent)
        self._levels = levels
        self.setFixedSize(max(1, len(levels) * (_DOT + 4)), _DOT + 2)

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() / 2
        for index, level in enumerate(self._levels):
            x = index * (_DOT + 4) + _DOT / 2 + 1
            color = _cell_color(level)
            if level == plans.CELL_MUTED:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(color)
                painter.drawEllipse(QRectF(x - _DOT / 2 + 0.6, y - _DOT / 2 + 0.6, _DOT - 1.2, _DOT - 1.2))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(QRectF(x - _DOT / 2, y - _DOT / 2, _DOT, _DOT))
        painter.end()


class _SummaryTile(_Surface):
    """Один способ связи по всем адресам: что это и у скольких адресов отвечает."""

    def __init__(self, item: plans.TransportSummary, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(StrongBodyLabel(item.title, self), 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        head.addWidget(_Dots(item.dots, self), 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)

        self.value_label = TitleLabel(self._value_text(1.0), self)
        if item.level == plans.CELL_MUTED:
            mute(self.value_label)
        layout.addWidget(self.value_label)
        self.note_label = BodyLabel(item.note, self)
        self.note_label.setWordWrap(True)
        _paint_value(self.note_label, item.level)
        layout.addWidget(self.note_label)
        self.about_label = CaptionLabel(item.about, self)
        self.about_label.setWordWrap(True)
        mute(self.about_label)
        layout.addWidget(self.about_label)
        layout.addStretch(1)
        set_state_text(self, f"{item.title}: отвечает адресов {item.answered} из {item.total}, {item.note}. {item.about}")

    def _value_text(self, progress: float) -> str:
        item = self.item
        if item.level == plans.CELL_MUTED:
            return "—"
        return f"{int(round(item.answered * progress))} из {item.total}"

    def set_reveal(self, progress: float) -> None:
        self.value_label.setText(self._value_text(progress))


class _SpeedBar(QWidget):
    """Полоска скорости: чем она длиннее, тем дольше сервер отвечал этим способом."""

    def __init__(self, share: float, level: str, parent=None) -> None:
        super().__init__(parent)
        self._share = max(0.0, min(1.0, share))
        self._level = level
        self._progress = 1.0
        self.setFixedHeight(4)

    def set_reveal(self, progress: float) -> None:
        self._progress = progress
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_text_color(22))
        painter.drawRoundedRect(self.rect(), 2, 2)
        if self._share > 0.0:
            # Даже самый быстрый ответ оставляет видимую чёрточку.
            width = max(4.0, self.width() * self._share) * self._progress
            # Обычный ответ — спокойной полоской; цветной она становится только при сбоях.
            calm = self._level == plans.CELL_OK
            painter.setBrush(_text_color(120) if calm else _cell_color(self._level))
            painter.drawRoundedRect(QRectF(0, 0, width, self.height()), 2, 2)
        painter.end()


class _CellTile(_Surface):
    """Один способ связи с одним адресом: время ответа или причина отказа."""

    def __init__(self, cell: tuple[str, str, str, str], time_ms: float | None, slowest_ms: float, parent=None) -> None:
        super().__init__(parent)
        title, text, level, reason = cell
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)
        caption = CaptionLabel(title, self)
        mute(caption)
        layout.addWidget(caption)
        # «84 мс · 2 из 3»: время — крупно, а сколько запросов дошло — строкой ниже, к причине.
        value, _dot, tail = text.partition(" · ")
        self.value_label = StrongBodyLabel(value, self)
        self.value_label.setWordWrap(True)
        self.value_label.setTextInteractionFlags(_SELECTABLE)
        _paint_value(self.value_label, level)
        layout.addWidget(self.value_label)
        share = time_ms / slowest_ms if time_ms is not None and slowest_ms > 0 else 0.0
        self.bar = _SpeedBar(share, level, self)
        layout.addWidget(self.bar)
        # Причину не повторяем, если она уже стоит в самой ячейке.
        extra = " · ".join(part for part in (tail, reason if reason != text else "") if part)
        self.reason_label = CaptionLabel(extra, self)
        self.reason_label.setWordWrap(True)
        mute(self.reason_label)
        self.reason_label.setVisible(bool(extra))
        layout.addWidget(self.reason_label)
        layout.addStretch(1)
        set_state_text(self, ". ".join(part for part in (f"{title}: {text}", extra) if part))


class _DomainsTable(QWidget):
    """Что сервер ответил про контрольные сайты обычным и шифрованным путём."""

    def __init__(self, domains, parent=None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(5)
        for column, (title, stretch) in enumerate((("Сайт", 2), ("Обычным путём", 3), ("Шифрованным", 3))):
            head = CaptionLabel(title, self)
            mute(head)
            grid.addWidget(head, 0, column)
            grid.setColumnStretch(column, stretch)
        self.rows: list[tuple[BodyLabel, BodyLabel, BodyLabel]] = []
        for row, (domain, plain, secure, different) in enumerate(domains, start=1):
            labels = (BodyLabel(domain, self), BodyLabel(plain, self), BodyLabel(secure, self))
            for column, label in enumerate(labels):
                label.setWordWrap(True)
                label.setTextInteractionFlags(_SELECTABLE)
                grid.addWidget(label, row, column, Qt.AlignmentFlag.AlignTop)
            if different:
                # Расхождение — это и есть улика: обычный путь отвечает не то, что шифрованный.
                _tint(labels[1], status_color(plans.CARD_NETWORK))
                font = labels[1].font()
                font.setBold(True)
                labels[1].setFont(font)
            self.rows.append(labels)
        differing = sum(1 for item in domains if item[3])
        set_state_text(self, f"Контрольные сайты: {len(domains)}, расходятся ответы: {differing}")


class AddressCard(_Surface):
    """Всё об одном адресе сервера."""

    def __init__(self, address: plans.AddressDetails, slowest_ms: float, parent=None) -> None:
        super().__init__(parent)
        self.address = address
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 16, 16)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.address_label = SubtitleLabel(address.address, self)
        self.address_label.setTextInteractionFlags(_SELECTABLE)
        head.addWidget(self.address_label, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        status = address.status
        self.status_mark = _StatusMark(status, self)
        head.addWidget(self.status_mark, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)

        tiles = QHBoxLayout()
        tiles.setSpacing(6)
        times = address.times_ms or (None,) * len(address.cells)
        self.tiles = [_CellTile(cell, time_ms, slowest_ms, self) for cell, time_ms in zip(address.cells, times)]
        for tile in self.tiles:
            tiles.addWidget(tile, 1)
        layout.addLayout(tiles)

        self.finding_rows = [
            _FindingRow(verdicts.VerdictItem(level=level, title=text), self) for level, text in address.findings
        ]
        for row in self.finding_rows:
            layout.addWidget(row)

        self.who_labels: list[CaptionLabel] = []
        for line in address.who:
            label = CaptionLabel(line, self)
            label.setWordWrap(True)
            label.setTextInteractionFlags(_SELECTABLE)
            mute(label)
            layout.addWidget(label)
            self.who_labels.append(label)

        self.domains_table: _DomainsTable | None = None
        if address.domains:
            title = StrongBodyLabel("Что сервер ответил про контрольные сайты", self)
            title.setWordWrap(True)
            layout.addWidget(title)
            self.domains_table = _DomainsTable(address.domains, self)
            layout.addWidget(self.domains_table)
        layout.addStretch(1)
        set_state_text(self, f"Адрес {address.address}: {plans.CARD_TITLES[status]}")

    def set_reveal(self, progress: float) -> None:
        for tile in self.tiles:
            tile.bar.set_reveal(progress)


class ServerDetailView(QWidget):
    """Подробности сервера на всю страницу: путь «DNS-серверы → сервер», шапка, сводка и адреса."""

    closed = pyqtSignal()
    ROOT_KEY = "servers"
    SERVER_KEY = "server"
    ROOT_TITLE = "DNS-серверы"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._details: plans.ServerDetails | None = None
        self._columns = 0
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(_GAP)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.breadcrumb = BreadcrumbBar(self)
        self.breadcrumb.currentItemChanged.connect(self._on_breadcrumb)
        self._layout.addWidget(self.breadcrumb)

        self._body: QWidget | None = None
        self._grid: QGridLayout | None = None
        self.copy_button: PushButton | None = None
        self.hero: QWidget | None = None
        self.summary_tiles: list[_SummaryTile] = []
        self.address_cards: list[AddressCard] = []
        self.notes_label: BodyLabel | None = None

        self._reveal = QVariantAnimation(self)
        self._reveal.setStartValue(0.0)
        self._reveal.setEndValue(1.0)
        self._reveal.setDuration(_REVEAL_MS)
        self._reveal.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reveal.valueChanged.connect(self._on_reveal)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)

    # ── что показано ────────────────────────────────────────

    def details(self) -> plans.ServerDetails | None:
        return self._details

    def columns(self) -> int:
        return self._columns

    def show_details(self, details: plans.ServerDetails, *, animate: bool = True) -> None:
        self._details = details
        card = details.card
        self.breadcrumb.blockSignals(True)
        try:
            self.breadcrumb.clear()
            self.breadcrumb.addItem(self.ROOT_KEY, self.ROOT_TITLE)
            self.breadcrumb.addItem(self.SERVER_KEY, card.server)
            set_breadcrumb_accessibility(self.breadcrumb, [self.ROOT_TITLE, card.server])
        finally:
            self.breadcrumb.blockSignals(False)

        self._reveal.stop()
        if self._body is not None:
            self._layout.removeWidget(self._body)
            self._body.setParent(None)
            self._body.deleteLater()
        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(_GAP)

        self.hero = self._build_hero(details)
        body.addWidget(self.hero)
        summary = self._build_summary(details)
        body.addWidget(summary)

        addresses_title = StrongBodyLabel(f"Адреса сервера: {len(details.addresses)}", self._body)
        body.addWidget(addresses_title)
        grid_host = QWidget(self._body)
        self._grid = QGridLayout(grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(_GAP)
        slowest = max(
            (value for address in details.addresses for value in address.times_ms if value is not None), default=0.0
        )
        self.address_cards = [AddressCard(address, slowest, grid_host) for address in details.addresses]
        self._columns = 0
        self._place_addresses()
        body.addWidget(grid_host)
        self._layout.addWidget(self._body)

        set_state_text(
            self,
            f"Подробности: {card.server}, {plans.CARD_TITLES[card.status]}, адресов {len(details.addresses)}",
        )
        if animate and are_live_animations_enabled():
            for order, block in enumerate((self.hero, summary, addresses_title, *self.address_cards)):
                float_in(block, delay_ms=min(order, 8) * _FLOAT_STEP_MS)
            self._on_reveal(0.0)
            self._reveal.start()
        else:
            self._on_reveal(1.0)
        self._sync_height()
        # Высота текста с переносом известна только после первого прохода раскладки.
        QTimer.singleShot(0, self._sync_height)

    # ── сборка блоков ───────────────────────────────────────

    def _build_hero(self, details: plans.ServerDetails) -> QWidget:
        card = details.card
        hero = _Surface(self._body)
        layout = QVBoxLayout(hero)
        layout.setContentsMargins(20, 18, 18, 18)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(_ServerIcon(card, hero), 0, Qt.AlignmentFlag.AlignVCenter)
        names = QVBoxLayout()
        names.setSpacing(2)
        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        self.title_label = TitleLabel(card.server, hero)
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        name_row.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        name_row.addStretch(1)
        names.addLayout(name_row)
        facts_row = QHBoxLayout()
        facts_row.setSpacing(12)
        self.status_mark = _StatusMark(card.status, hero)
        facts_row.addWidget(self.status_mark, 0, Qt.AlignmentFlag.AlignVCenter)
        facts = " · ".join(part for part in (f"адресов: {len(details.addresses)}", card.best) if part)
        self.facts_label = CaptionLabel(facts, hero)
        mute(self.facts_label)
        facts_row.addWidget(self.facts_label, 0, Qt.AlignmentFlag.AlignVCenter)
        facts_row.addStretch(1)
        names.addLayout(facts_row)
        head.addLayout(names, 1)

        self.copy_button = PushButton("Скопировать", hero)
        set_control_accessibility(
            self.copy_button,
            name="Скопировать подробности",
            description="Кладёт всё, что узнали о сервере, в буфер обмена обычным текстом.",
        )
        self.copy_button.clicked.connect(self._copy)
        head.addWidget(self.copy_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        # Вывод простыми словами: что это значит и кто в этом виноват.
        self.hint_label = BodyLabel(plans.CARD_HINTS[card.status], hero)
        self.hint_label.setWordWrap(True)
        self.hint_label.setTextInteractionFlags(_SELECTABLE)
        layout.addWidget(self.hint_label)

        # Замечания — отдельным блоком: заголовок и сам текст на разных строках.
        self.notes_label: BodyLabel | None = None
        notes = [note for note in card.note.split(" · ") if note]
        if notes:
            block = QVBoxLayout()
            block.setSpacing(2)
            block.addWidget(mute(CaptionLabel("Что замечено", hero)))
            self.notes_label = BodyLabel(" · ".join(notes), hero)
            self.notes_label.setWordWrap(True)
            self.notes_label.setTextInteractionFlags(_SELECTABLE)
            block.addWidget(self.notes_label)
            layout.addLayout(block)
        return hero

    def _build_summary(self, details: plans.ServerDetails) -> QWidget:
        host = QWidget(self._body)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(StrongBodyLabel("Способы связи", host))
        intro = CaptionLabel(
            "Сервер спрашивают пятью способами. Провайдер может закрыть любой из них отдельно, "
            "поэтому каждый проверен на каждом адресе. Точки справа — по одной на адрес.",
            host,
        )
        intro.setWordWrap(True)
        mute(intro)
        layout.addWidget(intro)
        row = QHBoxLayout()
        row.setSpacing(_GAP)
        self.summary_tiles = [_SummaryTile(item, host) for item in plans.build_transport_summary(details)]
        for tile in self.summary_tiles:
            row.addWidget(tile, 1)
        layout.addLayout(row)
        return host

    def _place_addresses(self) -> None:
        if self._grid is None:
            return
        wide = self.width() >= _TWO_COLUMNS_FROM and len(self.address_cards) > 1
        columns = 2 if wide else 1
        if columns == self._columns:
            return
        self._columns = columns
        for card in self.address_cards:
            self._grid.removeWidget(card)
        for index, card in enumerate(self.address_cards):
            self._grid.addWidget(card, index // columns, index % columns)
        for column in range(2):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)

    # ── поведение ───────────────────────────────────────────

    def _on_reveal(self, value) -> None:
        progress = float(value)
        for tile in self.summary_tiles:
            tile.set_reveal(progress)
        for card in self.address_cards:
            card.set_reveal(progress)

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = tokens, force
        # Цвета текста зависят от темы: проще собрать заново, чем перекрашивать каждую метку.
        if self._details is not None:
            self.show_details(self._details, animate=False)

    def _on_breadcrumb(self, key: str) -> None:
        if key == self.ROOT_KEY:
            self.closed.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.closed.emit()
            return
        super().keyPressEvent(event)

    def _copy(self) -> None:
        if self._details is None or self.copy_button is None:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._details.text)
        self.copy_button.setText("Скопировано")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_addresses()
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


__all__ = ["AddressCard", "ServerDetailView"]
