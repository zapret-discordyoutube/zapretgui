"""Панель итога для вкладки «DNS-серверы».

Как на соседних вкладках BlockCheck: медоед-талисман, одна главная фраза,
пояснение простыми словами и находки строками — заголовок и подробности под ним.
Пока идёт проверка, здесь же полоса хода, счётчики и бегущие фразы.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, ProgressBar, PushButton, SimpleCardWidget, SubtitleLabel

import dns.server_check_verdict as verdicts
from blockcheck.ui.check_results import _HeightKeeper, tone_color
from dns.server_check import LEVEL_FAIL, LEVEL_OK, LEVEL_WARN
from ui.accessibility import set_control_accessibility, set_state_text
from ui.theme import get_cached_qta_pixmap
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.fun import CounterBadge, FunTicker, Mascot, burst_confetti
from ui.widgets.fun.mascot import MOOD_ALARM, MOOD_BUSY, MOOD_HAPPY, MOOD_IDLE, MOOD_SAD
from ui.widgets.stagger_float_in import float_in

_LEVEL_ICONS = {
    LEVEL_FAIL: ("fa5s.times-circle", "error"),
    LEVEL_WARN: ("fa5s.exclamation-triangle", "warning"),
    LEVEL_OK: ("fa5s.check-circle", "success"),
}
_INFO_ICON = ("fa5s.info-circle", "muted")
# Вид итога → значок заголовка, его тон и настроение медоеда.
_KIND_VIEW = {
    "idle": ("fa5s.search", "muted", MOOD_IDLE),
    "pending": ("fa5s.hourglass-half", "muted", MOOD_BUSY),
    "error": ("fa5s.exclamation-triangle", "warning", MOOD_ALARM),
    verdicts.KIND_OK: ("fa5s.check-circle", "success", MOOD_HAPPY),
    verdicts.KIND_NOTES: ("fa5s.check-circle", "success", MOOD_IDLE),
    verdicts.KIND_WARN: ("fa5s.exclamation-triangle", "warning", MOOD_SAD),
    verdicts.KIND_FAIL: ("fa5s.times-circle", "error", MOOD_ALARM),
    verdicts.KIND_STOPPED: ("fa5s.question-circle", "muted", MOOD_IDLE),
    verdicts.KIND_EMPTY: ("fa5s.question-circle", "muted", MOOD_IDLE),
}
_FINDING_STEP_MS = 90


class _FindingRow(QWidget):
    """Одна находка: цветной значок, заголовок обычным цветом и подробности мелко."""

    def __init__(self, item: verdicts.VerdictItem, parent=None) -> None:
        super().__init__(parent)
        self._level = item.level
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(10)
        self._icon = QLabel(self)
        self._icon.setFixedSize(18, 18)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title_label = BodyLabel(item.title, self)
        self.title_label.setWordWrap(True)
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        texts.addWidget(self.title_label)
        self.detail_label = CaptionLabel(item.detail, self)
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_label.setVisible(bool(item.detail))
        texts.addWidget(self.detail_label)
        layout.addLayout(texts, 1)
        set_state_text(self, f"{item.title}. {item.detail}".strip())
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        icon_name, tone = _LEVEL_ICONS.get(self._level, _INFO_ICON)
        color = tone_color(tone, tokens) or None
        try:
            self._icon.setPixmap(get_cached_qta_pixmap(icon_name, color=color, size=16, muted_fallback=color is None))
        except Exception:
            pass


class ServerCheckVerdictPanel(_HeightKeeper, SimpleCardWidget):
    """Итог проверки DNS-серверов и её ход."""

    def __init__(self, on_open_dns_settings: Callable[[], None] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._kind = "idle"
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(14)
        self.mascot = Mascot(self, size=56)
        header.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self._icon = QLabel(self)
        self._icon.setFixedSize(24, 24)
        title_row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        # Главная фраза крупнее остального: её читают первой.
        self.title_label = SubtitleLabel("", self)
        self.title_label.setWordWrap(True)
        title_row.addWidget(self.title_label, 1)
        titles.addLayout(title_row)
        self.detail_label = BodyLabel("", self)
        self.detail_label.setWordWrap(True)
        titles.addWidget(self.detail_label)
        self.ticker = FunTicker(self)
        self.ticker.setVisible(False)
        titles.addWidget(self.ticker)

        counters = QHBoxLayout()
        counters.setContentsMargins(0, 2, 0, 0)
        counters.setSpacing(8)
        self.good_badge = CounterBadge("отвечают", self, tone="success", mark="✓")
        self.silent_badge = CounterBadge("молчат", self, tone="muted", mark="✗")
        for badge in (self.good_badge, self.silent_badge):
            badge.setVisible(False)
            counters.addWidget(badge)
        counters.addStretch(1)
        titles.addLayout(counters)
        header.addLayout(titles, 1)
        root.addLayout(header)

        self.progress_bar = ProgressBar(self)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        self._findings_host = QWidget(self)
        self._findings_layout = QVBoxLayout(self._findings_host)
        self._findings_layout.setContentsMargins(70, 2, 0, 0)
        self._findings_layout.setSpacing(8)
        self._findings_host.setVisible(False)
        root.addWidget(self._findings_host)

        # Кнопки страницы («Проверить», «Остановить», «Отчёт») живут здесь же.
        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(70, 2, 0, 0)
        self.actions.setSpacing(10)
        root.addLayout(self.actions)
        self.open_settings_btn = None
        if on_open_dns_settings is not None:
            button = PushButton("Открыть «Настройка DNS»", self)
            set_control_accessibility(
                button,
                name="Открыть страницу «Настройка DNS»",
                description="Там включается шифрованный DNS, который по дороге не перехватить и не подменить.",
            )
            button.clicked.connect(lambda _checked=False: on_open_dns_settings())
            button.setVisible(False)
            self.open_settings_btn = button

        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    @property
    def kind(self) -> str:
        return self._kind

    def finding_rows(self) -> list[_FindingRow]:
        return [
            self._findings_layout.itemAt(index).widget()
            for index in range(self._findings_layout.count())
            if isinstance(self._findings_layout.itemAt(index).widget(), _FindingRow)
        ]

    def add_actions(self, *buttons) -> None:
        """Кнопки страницы в один ряд; кнопка «Настройка DNS» встаёт после них."""
        for button in buttons:
            self.actions.addWidget(button)
        if self.open_settings_btn is not None:
            self.actions.addWidget(self.open_settings_btn)
        self.actions.addStretch(1)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_min_height()

    # --- состояния ------------------------------------------------------------

    def _clear_findings(self) -> None:
        for row in self.finding_rows():
            self._findings_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._findings_host.setVisible(False)

    def _set(self, kind: str, title: str, detail: str) -> None:
        self._kind = kind if kind in _KIND_VIEW else "error"
        self.title_label.setText(title)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        pending = self._kind == "pending"
        if not pending:
            self.ticker.stop()
        self.ticker.setVisible(pending)
        self.progress_bar.setVisible(pending)
        self.mascot.set_mood(_KIND_VIEW[self._kind][2])
        self._apply_theme_refresh()
        set_state_text(self, f"Итог проверки DNS-серверов: {title}")
        self._schedule_min_height_sync()

    def _show_tally(self, tally: verdicts.Tally) -> None:
        for badge, value in ((self.good_badge, tally.good), (self.silent_badge, tally.silent)):
            # Сначала показать, потом менять число: спрятанный счётчик не подпрыгивает.
            badge.setVisible(value > 0)
            badge.set_value(value)

    def _hide_extras(self) -> None:
        self._clear_findings()
        self._show_tally(verdicts.Tally())
        if self.open_settings_btn is not None:
            self.open_settings_btn.setVisible(False)

    def set_idle(self, title: str, detail: str) -> None:
        self._hide_extras()
        self._set("idle", title, detail)

    def set_failed(self, title: str, detail: str = "") -> None:
        self._hide_extras()
        self._set("error", title, detail)

    def set_pending(self, title: str, phrases) -> None:
        self._hide_extras()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self._set("pending", title, "")
        self.ticker.set_phrases(phrases)
        self.ticker.start()

    def show_progress(self, title: str, done: int, total: int, tally: verdicts.Tally) -> None:
        """Ход проверки: полоса, заголовок и счётчики. Остальное не трогаем — это частый вызов."""
        if self._kind != "pending":
            return
        self.title_label.setText(title)
        self.progress_bar.setRange(0, max(1, int(total)))
        self.progress_bar.setValue(int(done))
        self._show_tally(tally)
        set_state_text(self.progress_bar, f"Проверка DNS-серверов: готово {done} из {total}")

    def show_verdict(self, verdict: verdicts.Verdict, *, celebrate: bool = True) -> None:
        self._clear_findings()
        rows = [_FindingRow(item, self._findings_host) for item in verdict.items]
        for row in rows:
            self._findings_layout.addWidget(row)
        self._findings_host.setVisible(bool(rows))
        if self.open_settings_btn is not None:
            self.open_settings_btn.setVisible(verdict.suggests_encrypted_dns)
        # Счётчики нужны, пока проверка идёт; в итоге их сменяют полоса и фильтр над карточками.
        self._show_tally(verdicts.Tally())
        self._set(verdict.kind, verdict.title, verdict.detail)
        if not celebrate:
            return
        # Находки выплывают по очереди, а если всё хорошо — салют.
        for order, row in enumerate(rows):
            float_in(row, delay_ms=120 + order * _FINDING_STEP_MS)
        if verdict.kind == verdicts.KIND_OK:
            burst_confetti(self)

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        icon_name, tone, _mood = _KIND_VIEW.get(self._kind, _KIND_VIEW["error"])
        color = tone_color(tone, tokens) or None
        try:
            self._icon.setPixmap(get_cached_qta_pixmap(icon_name, color=color, size=20, muted_fallback=color is None))
        except Exception:
            pass


__all__ = ["ServerCheckVerdictPanel"]
