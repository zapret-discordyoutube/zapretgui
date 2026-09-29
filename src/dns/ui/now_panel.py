"""Панель «Сейчас» страницы DNS.

Сверху страницы: какой DNS стоит прямо сейчас на отмеченных адаптерах,
сами адаптеры (кнопки-таблетки: отмеченные получат выбранный DNS) и
действия — вернуть DNS автоматически, замерить скорость серверов,
сбросить кэш DNS Windows.

Панель ничего не делает сама: она только показывает то, что ей передала
страница, и сообщает о нажатиях сигналами.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FlowLayout,
    FluentIcon,
    IndeterminateProgressRing,
    PillPushButton,
    PushButton,
    SimpleCardWidget,
    SubtitleLabel,
    isDarkTheme,
)

from dns.ui.provider_grid import badge_color
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import set_tooltip, style_semantic_caption_label
from ui.theme import get_cached_qta_pixmap, get_theme_tokens


@dataclass(frozen=True, slots=True)
class NowState:
    """Что показать в панели: заголовок, строка адресов и значок."""

    title: str
    detail: str = ""
    icon_name: str = "fa5s.globe"
    color: str = ""
    busy: bool = False


@dataclass(frozen=True, slots=True)
class AdapterChip:
    """Таблетка адаптера. key — GUID адаптера, text — что написано на кнопке."""

    key: str
    text: str
    kind: str = "ethernet"
    tooltip: str = ""
    checked: bool = True


class _Badge(QWidget):
    """Крупный значок текущего DNS в круге его цвета."""

    SIZE = 52

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._icon_name = "fa5s.globe"
        self._color = ""
        self.setFixedSize(self.SIZE, self.SIZE)

    def set_icon(self, icon_name: str, color: str) -> None:
        self._icon_name = icon_name or "fa5s.globe"
        self._color = color or ""
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        tokens = get_theme_tokens()
        color = badge_color(self._color, tokens, isDarkTheme())
        back = QColor(color)
        back.setAlpha(52 if isDarkTheme() else 38)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(back)
        painter.drawEllipse(QRectF(0, 0, self.SIZE, self.SIZE))
        icon_size = 24
        pixmap = get_cached_qta_pixmap(self._icon_name, color=color.name(), size=icon_size)
        offset = (self.SIZE - icon_size) // 2
        painter.drawPixmap(offset, offset, pixmap)
        painter.end()


class DnsNowPanel(SimpleCardWidget):
    reset_clicked = pyqtSignal()
    measure_clicked = pyqtSignal()
    flush_clicked = pyqtSignal()
    adapters_changed = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("dnsNowPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._chips: dict[str, PillPushButton] = {}
        self._adapters_caption_text = "Применять к:"
        self._adapters_empty_text = "Сетевые адаптеры не найдены"

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        # ── что стоит сейчас ──
        head = QHBoxLayout()
        head.setSpacing(16)
        self.badge = _Badge(self)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.eyebrow_label = CaptionLabel("Сейчас на отмеченных адаптерах", self)
        self.eyebrow_label.setTextColor(QColor(0, 0, 0, 115), QColor(255, 255, 255, 125))
        text.addWidget(self.eyebrow_label)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.title_label = SubtitleLabel("", self)
        title_row.addWidget(self.title_label)
        self.busy_ring = IndeterminateProgressRing(self, start=False)
        self.busy_ring.setFixedSize(18, 18)
        self.busy_ring.setStrokeWidth(2)
        self.busy_ring.hide()
        title_row.addWidget(self.busy_ring, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        text.addLayout(title_row)
        self.detail_label = BodyLabel("", self)
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_label.setTextColor(QColor(0, 0, 0, 160), QColor(255, 255, 255, 170))
        text.addWidget(self.detail_label)
        head.addLayout(text, 1)
        root.addLayout(head)

        # ── адаптеры и действия ──
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        adapters_box = QHBoxLayout()
        adapters_box.setSpacing(10)
        self.adapters_caption = CaptionLabel("Применять к:", self)
        adapters_box.addWidget(self.adapters_caption, 0, Qt.AlignmentFlag.AlignTop)
        self.adapters_caption.setFixedHeight(32)
        self.adapters_host = QWidget(self)
        self.adapters_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.adapters_flow = FlowLayout(self.adapters_host, needAni=False)
        self.adapters_flow.setContentsMargins(0, 0, 0, 0)
        self.adapters_flow.setHorizontalSpacing(6)
        self.adapters_flow.setVerticalSpacing(6)
        adapters_box.addWidget(self.adapters_host, 1)
        bottom.addLayout(adapters_box, 1)

        self.reset_button = PushButton(FluentIcon.RETURN, "Вернуть автоматически", self)
        self.measure_button = PushButton(FluentIcon.SPEED_HIGH, "Замерить скорость", self)
        self.flush_button = PushButton(FluentIcon.BROOM, "Сбросить кэш", self)
        for button in (self.reset_button, self.measure_button, self.flush_button):
            bottom.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(bottom)

        self.notice_label = CaptionLabel("", self)
        self.notice_label.setWordWrap(True)
        style_semantic_caption_label(self.notice_label, tone="warning")
        self.notice_label.hide()
        root.addWidget(self.notice_label)

        self.reset_button.clicked.connect(self.reset_clicked)
        self.measure_button.clicked.connect(self.measure_clicked)
        self.flush_button.clicked.connect(self.flush_clicked)
        self._describe_buttons()

    def _describe_buttons(self) -> None:
        reset_text = (
            "DNS снова будет получаться автоматически от роутера или провайдера (DHCP). "
            "Помогает, если после ручной настройки интернет работает нестабильно."
        )
        measure_text = "Отправляет каждому серверу DNS-запрос и показывает на плитках время ответа."
        flush_text = "Очищает кэш DNS Windows, если сайты открываются по старым адресам."
        for button, name, description in (
            (self.reset_button, "Вернуть DNS автоматически", reset_text),
            (self.measure_button, "Замерить скорость DNS-серверов", measure_text),
            (self.flush_button, "Сбросить кэш DNS", flush_text),
        ):
            set_tooltip(button, description)
            set_control_accessibility(button, name=name, description=description)
            set_state_text(button, name)

    # ── состояние ───────────────────────────────────────────

    def set_state(self, state: NowState) -> None:
        self.title_label.setText(state.title)
        self.detail_label.setText(state.detail)
        self.detail_label.setVisible(bool(state.detail))
        self.badge.set_icon(state.icon_name, state.color)
        self.busy_ring.setVisible(state.busy)
        if state.busy:
            self.busy_ring.start()
        else:
            self.busy_ring.stop()
        summary = f"Сейчас DNS: {state.title}" + (f", {state.detail}" if state.detail else "")
        set_control_accessibility(self, name="Текущий DNS", description=summary)
        set_state_text(self.title_label, summary)

    def set_measuring(self, measuring: bool, *, idle_text: str, busy_text: str) -> None:
        self.measure_button.setEnabled(not measuring)
        self.measure_button.setText(busy_text if measuring else idle_text)

    def set_adapters_caption(self, caption: str, empty_text: str) -> None:
        self._adapters_caption_text = caption
        self._adapters_empty_text = empty_text
        self.adapters_caption.setText(caption if self._chips else empty_text)

    def set_notice(self, text: str) -> None:
        self.notice_label.setText(text)
        self.notice_label.setVisible(bool(text))

    # ── адаптеры ────────────────────────────────────────────

    def set_adapters(self, chips: list[AdapterChip]) -> None:
        """Пересобирает таблетки; отметки уже известных адаптеров сохраняются."""
        previous = {key: chip.isChecked() for key, chip in self._chips.items()}
        for chip in self._chips.values():
            self.adapters_flow.removeWidget(chip)
            chip.deleteLater()
        self._chips = {}
        for item in chips:
            icon = FluentIcon.WIFI if item.kind == "wifi" else FluentIcon.CONNECT
            chip = PillPushButton(icon, item.text, self.adapters_host)
            chip.setCheckable(True)
            chip.setChecked(previous.get(item.key, item.checked))
            chip.setProperty("adapterName", item.text)
            set_tooltip(chip, item.tooltip)
            chip.toggled.connect(self._on_chip_toggled)
            self._sync_chip_accessibility(chip, item.tooltip)
            self.adapters_flow.addWidget(chip)
            self._chips[item.key] = chip
        self.adapters_caption.setText(self._adapters_caption_text if chips else self._adapters_empty_text)

    def update_adapter_tooltips(self, tooltips: dict[str, str]) -> None:
        for key, chip in self._chips.items():
            tooltip = tooltips.get(key, "")
            set_tooltip(chip, tooltip)
            self._sync_chip_accessibility(chip, tooltip)

    def selected_adapters(self) -> list[str]:
        """GUID отмеченных адаптеров."""
        return [key for key, chip in self._chips.items() if chip.isChecked()]

    def adapter_keys(self) -> list[str]:
        return list(self._chips)

    def chip(self, key: str) -> PillPushButton | None:
        return self._chips.get(key)

    def _on_chip_toggled(self, _checked: bool) -> None:
        chip = self.sender()
        if isinstance(chip, PillPushButton):
            self._sync_chip_accessibility(chip, str(chip.property("dnsTooltip") or ""))
        self.adapters_changed.emit(self.selected_adapters())

    @staticmethod
    def _sync_chip_accessibility(chip: PillPushButton, tooltip: str) -> None:
        chip.setProperty("dnsTooltip", tooltip)
        state = "отмечен" if chip.isChecked() else "не отмечен"
        set_control_accessibility(
            chip,
            name=f"Адаптер {chip.text()}, {state}",
            description=tooltip or "Отмеченные адаптеры получат выбранный DNS.",
        )

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(640, super().sizeHint().height())


__all__ = ["AdapterChip", "DnsNowPanel", "NowState"]
