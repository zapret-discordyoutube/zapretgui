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

import math
import time

from PyQt6.QtCore import QPointF, QSize, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FlowLayout,
    FluentIcon,
    PillPushButton,
    PushButton,
    SimpleCardWidget,
    SubtitleLabel,
    isDarkTheme,
    themeColor,
)

from dns.ui.provider_grid import CHARGE_MS, FRAME_MS, badge_color, comet_geometry, ease_out_cubic, paint_comet, paint_glow
from profile.ui.profile_icon import profile_icon_pixmap
from ui.animation_policy import are_live_animations_enabled
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import set_tooltip, style_semantic_caption_label
from ui.theme import get_theme_tokens


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
    """Крупный значок текущего DNS в круге его цвета.

    Светится мягким ореолом своего цвета. Пока DNS применяется, вокруг бежит
    комета и всегда замыкает круг, даже если DNS встал мгновенно. При смене
    сервера значок переворачивается, как монетка (на обороте уже новый), и
    от него расходится вспышка свечения.
    """

    CIRCLE = 52
    BOX = 72
    FLIP_MS = 900

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._icon_name = "fa5s.globe"
        self._color = ""
        self._old_icon_name = ""
        self._old_color = ""
        self._flip = -1.0
        self._clock = time.monotonic
        self._busy_since: float | None = None
        self._busy_wanted = False
        self.setFixedSize(self.BOX, self.BOX)
        self._flip_anim = QVariantAnimation(self)
        self._flip_anim.setStartValue(0.0)
        self._flip_anim.setEndValue(1.0)
        self._flip_anim.setDuration(self.FLIP_MS)
        self._flip_anim.valueChanged.connect(self._on_flip_value)
        self._flip_anim.finished.connect(self._on_flip_finished)
        self._ticker = QTimer(self)
        self._ticker.setInterval(FRAME_MS)
        self._ticker.timeout.connect(self._tick)

    def set_icon(self, icon_name: str, color: str) -> None:
        icon_name = icon_name or "fa5s.globe"
        color = color or ""
        if (icon_name, color) == (self._icon_name, self._color):
            return
        self._old_icon_name, self._old_color = self._icon_name, self._color
        self._icon_name, self._color = icon_name, color
        if are_live_animations_enabled() and self.isVisible():
            self._flip_anim.stop()
            self._flip_anim.start()
        self.update()

    def set_busy(self, busy: bool) -> None:
        """Комета вокруг значка; после снятия занятости она дожимает круг до конца."""
        self._busy_wanted = bool(busy) and are_live_animations_enabled()
        if self._busy_wanted and self._busy_since is None:
            self._busy_since = self._clock()
            self._ticker.start()
        self._tick()

    def is_busy(self) -> bool:
        """Видна ли сейчас комета."""
        return self._busy_since is not None

    def _tick(self) -> None:
        if self._busy_since is not None and not self._busy_wanted:
            if (self._clock() - self._busy_since) * 1000.0 >= CHARGE_MS:
                self._busy_since = None
                self._ticker.stop()
        self.update()

    def is_flipping(self) -> bool:
        return self._flip_anim.state() == QVariantAnimation.State.Running

    def _on_flip_value(self, value) -> None:
        self._flip = float(value)
        self.update()

    def _on_flip_finished(self) -> None:
        self._flip = -1.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        tokens = get_theme_tokens()
        dark = isDarkTheme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        center = QPointF(self.BOX / 2, self.BOX / 2)
        half = self.CIRCLE / 2
        flipping = self._flip >= 0.0
        # Первая половина переворота — старый значок, вторая — новый.
        first_half = flipping and self._flip < 0.5
        icon_name = self._old_icon_name if first_half else self._icon_name
        color = badge_color(self._old_color if first_half else self._color, tokens, dark)

        paint_glow(painter, center, half + 9, color, 0.45 if dark else 0.3)
        if flipping and self._flip >= 0.5:
            burst = ease_out_cubic((self._flip - 0.5) / 0.5)
            paint_glow(painter, center, half + 4 + 14 * burst, color, 1.3 * (1.0 - burst))

        painter.save()
        painter.translate(center)
        if flipping:
            # Ширина монетки: 1 → 0 (ребро) → 1, с лёгким подскоком в конце.
            width = abs(math.cos(math.pi * min(self._flip / 0.6, 1.0)))
            if self._flip >= 0.6:
                width = 1.0 + 0.08 * math.sin(math.pi * (self._flip - 0.6) / 0.4)
            painter.scale(max(width, 0.02), 1.0 + 0.04 * (1.0 - width))
        back = QColor(color)
        back.setAlpha(52 if dark else 38)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(back)
        painter.drawEllipse(QPointF(0, 0), half, half)
        icon_size = 24
        pixmap = profile_icon_pixmap(icon_name, color=color.name(), size=icon_size)
        painter.drawPixmap(-icon_size // 2, -icon_size // 2, pixmap)
        painter.restore()

        if self._busy_since is not None:
            head, tail, _looping = comet_geometry((self._clock() - self._busy_since) * 1000.0)
            paint_comet(painter, center, half + 4, head, tail, QColor(themeColor()), width=2.8, head_radius=3.2)
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

        # Вокруг круга значка 10 px под свечение: внешний отступ на столько же меньше.
        glow_pad = (_Badge.BOX - _Badge.CIRCLE) // 2
        root = QVBoxLayout(self)
        root.setContentsMargins(20 - glow_pad, 18 - glow_pad, 20, 16)
        root.setSpacing(14 - glow_pad)

        # ── что стоит сейчас ──
        head = QHBoxLayout()
        head.setSpacing(16 - glow_pad)
        self.badge = _Badge(self)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, glow_pad, 0, 0)
        text.setSpacing(2)
        self.eyebrow_label = CaptionLabel("Сейчас на отмеченных адаптерах", self)
        self.eyebrow_label.setTextColor(QColor(0, 0, 0, 115), QColor(255, 255, 255, 125))
        text.addWidget(self.eyebrow_label)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.title_label = SubtitleLabel("", self)
        title_row.addWidget(self.title_label)
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
        bottom.setContentsMargins(glow_pad, 0, 0, 0)
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
        self.notice_label.setContentsMargins(glow_pad, 0, 0, 0)
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
        self.badge.set_busy(state.busy)
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
