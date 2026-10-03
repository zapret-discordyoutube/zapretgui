"""Панель итога и список доменов для вкладки «DNS подмена».

Проверка сравнивает адрес от DNS системы с эталоном (DNS-over-HTTPS); если
адреса разные, решает сертификат сервера. Здесь это показано по-человечески:
медоед-талисман, одна фраза итога, совет и список доменов с вердиктом.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, PushButton, SimpleCardWidget, StrongBodyLabel

from blockcheck.ui.check_results import _HeightKeeper, tone_color
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import set_tooltip
from ui.theme import get_cached_qta_pixmap
from ui.theme_refresh import ThemeRefreshBinding
from ui.widgets.fun import FunTicker, Mascot, burst_confetti
from ui.widgets.fun.mascot import MOOD_ALARM, MOOD_BUSY, MOOD_HAPPY, MOOD_IDLE
from ui.widgets.stagger_float_in import float_in

# Состояния домена из diagnostics.verdict.DnsState.
_STATE_VIEW = {
    "spoofed": ("fa5s.times-circle", "error", "Подменён"),
    "local": ("fa5s.exclamation-triangle", "warning", "Адрес из файла hosts"),
    "unknown": ("fa5s.question-circle", "muted", "Не удалось проверить"),
    "ok": ("fa5s.check-circle", "success", "Честный ответ"),
}
_ORDER = {"spoofed": 0, "local": 1, "unknown": 2, "ok": 3}


def summarize_dns_results(results: dict | None) -> dict:
    """Итог проверки для панели: вид, заголовок, объяснение и число подмен."""
    results = results or {}
    if results.get("stopped"):
        return {"kind": "stopped", "title": "Проверка остановлена", "detail": ""}
    domains = dict(results.get("domains") or {})
    if not domains:
        return {
            "kind": "error",
            "title": "Проверка не удалась",
            "detail": "Подробности — в подробном логе ниже. Проверьте подключение и попробуйте ещё раз.",
        }
    spoofed = [host for host, item in domains.items() if item.get("state") == "spoofed"]
    unknown = [host for host, item in domains.items() if item.get("state") == "unknown"]
    if spoofed:
        noun = "сайта" if len(spoofed) == 1 else "сайтов"
        return {
            "kind": "spoofed",
            "title": f"Поймали провайдера за руку: подменяет адреса {len(spoofed)} {noun}",
            "detail": (
                "Программы, которые спрашивают адрес у Windows, получают неверный ответ. "
                "Включите DNS с шифрованием (DoH) на странице «Настройка DNS» — его провайдер перехватить не сможет."
            ),
            "count": len(spoofed),
        }
    if unknown:
        return {
            "kind": "partial",
            "title": "Явной подмены нет",
            "detail": (
                "Часть адресов отличается от эталона, но проверить их не удалось — для CDN это обычно нормально."
            ),
        }
    return {
        "kind": "ok",
        "title": "Ваш DNS честный 👍",
        "detail": "Адреса совпадают с эталоном. Если сайты не открываются — дело не в DNS, а в блокировке соединения.",
    }


class DnsSummaryPanel(_HeightKeeper, SimpleCardWidget):
    def __init__(self, on_open_dns_settings: Callable[[], None] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._kind = "idle"
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(14)
        self.mascot = Mascot(self, size=44)
        root.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignTop)
        body = QVBoxLayout()
        body.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self._icon = QLabel(self)
        self._icon.setFixedSize(24, 24)
        title_row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = StrongBodyLabel("", self)
        self.title_label.setWordWrap(True)
        title_row.addWidget(self.title_label, 1)
        body.addLayout(title_row)
        self.detail_label = CaptionLabel("", self)
        self.detail_label.setWordWrap(True)
        body.addWidget(self.detail_label)
        self.ticker = FunTicker(self)
        self.ticker.setVisible(False)
        body.addWidget(self.ticker)
        self.open_settings_btn = None
        if on_open_dns_settings is not None:
            actions = QHBoxLayout()
            actions.setContentsMargins(0, 4, 0, 0)
            actions.addStretch(1)
            button = PushButton("Открыть «Настройка DNS»", self)
            set_control_accessibility(
                button,
                name="Открыть страницу «Настройка DNS»",
                description="Там можно включить DNS с шифрованием, который провайдер не подменит.",
            )
            button.clicked.connect(lambda _checked=False: on_open_dns_settings())
            actions.addWidget(button)
            self._actions_host = QWidget(self)
            self._actions_host.setLayout(actions)
            self._actions_host.setVisible(False)
            body.addWidget(self._actions_host)
            self.open_settings_btn = button
        root.addLayout(body, 1)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self.set_idle()

    @property
    def kind(self) -> str:
        return self._kind

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_min_height()

    def _set(self, kind: str, title: str, detail: str, mood: str) -> None:
        self._kind = kind
        self.title_label.setText(title)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        if kind != "pending":
            self.ticker.stop()
            self.ticker.setVisible(False)
        if self.open_settings_btn is not None:
            self._actions_host.setVisible(kind == "spoofed")
        self.mascot.set_mood(mood)
        self._apply_theme_refresh()
        set_state_text(self, f"Итог проверки DNS: {title}")
        self._schedule_min_height_sync()

    def set_idle(self) -> None:
        self._set(
            "idle",
            "Медоед проверит, не врёт ли ваш DNS",
            "Сравним адреса сайтов от DNS системы с честным эталоном и посмотрим, подменяет ли провайдер адреса.",
            MOOD_IDLE,
        )

    def set_pending(self) -> None:
        from blockcheck.ui.fun_texts import phrases

        self._set("pending", "Сверяем адреса…", "", MOOD_BUSY)
        self.ticker.setVisible(True)
        self.ticker.set_phrases(phrases("dns"))
        self.ticker.start()

    def show_results(self, results: dict | None) -> dict:
        summary = summarize_dns_results(results)
        mood = {
            "ok": MOOD_HAPPY,
            "partial": MOOD_IDLE,
            "stopped": MOOD_IDLE,
        }.get(summary["kind"], MOOD_ALARM)
        self._set(summary["kind"], summary["title"], summary["detail"], mood)
        if summary["kind"] == "ok":
            burst_confetti(self)
        return summary

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        icon_name, tone = {
            "idle": ("fa5s.search", "muted"),
            "pending": ("fa5s.hourglass-half", "muted"),
            "ok": ("fa5s.check-circle", "success"),
            "partial": ("fa5s.check-circle", "success"),
            "spoofed": ("fa5s.times-circle", "error"),
            "stopped": ("fa5s.question-circle", "muted"),
        }.get(self._kind, ("fa5s.exclamation-triangle", "warning"))
        color = tone_color(tone, tokens) or None
        try:
            self._icon.setPixmap(get_cached_qta_pixmap(icon_name, color=color, size=20, muted_fallback=color is None))
        except Exception:
            pass


class _DomainRow(QWidget):
    def __init__(self, host: str, item: dict, parent=None) -> None:
        super().__init__(parent)
        self._state = str(item.get("state") or "unknown")
        if self._state not in _STATE_VIEW:
            self._state = "unknown"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(10)
        self.icon = QLabel(self)
        self.icon.setFixedSize(18, 18)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.host_label = BodyLabel(host, self)
        texts.addWidget(self.host_label)
        system_ips = ", ".join(item.get("system_ips") or ()) or "—"
        reference_ips = ", ".join(item.get("reference_ips") or ()) or "—"
        reason = str(item.get("reason") or "")
        self.detail_label = CaptionLabel(f"DNS системы: {system_ips} · эталон: {reference_ips}", self)
        self.detail_label.setWordWrap(True)
        set_tooltip(self.detail_label, reason)
        texts.addWidget(self.detail_label)
        layout.addLayout(texts, 1)
        verdict = _STATE_VIEW[self._state][2]
        self.verdict_label = CaptionLabel(verdict, self)
        set_tooltip(self.verdict_label, reason)
        layout.addWidget(self.verdict_label, 0, Qt.AlignmentFlag.AlignTop)
        set_state_text(self, f"{host}: {verdict}. {reason}".strip())
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    @property
    def state(self) -> str:
        return self._state

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        icon_name, tone, _word = _STATE_VIEW[self._state]
        color = tone_color(tone, tokens) or None
        try:
            self.icon.setPixmap(get_cached_qta_pixmap(icon_name, color=color, size=16, muted_fallback=color is None))
            if color:
                from PyQt6.QtGui import QColor

                self.verdict_label.setTextColor(QColor(color), QColor(color))
        except Exception:
            pass


class DnsDomainsView(QWidget):
    """Домены по порядку важности: подменённые сверху."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        set_control_accessibility(
            self,
            name="Домены проверки DNS",
            description="Для каждого домена: честный ли ответ DNS и какие адреса пришли.",
        )
        set_state_text(self, "Домены проверки DNS: пока нет результатов")

    def rows(self) -> list[_DomainRow]:
        return [
            self._layout.itemAt(i).widget()
            for i in range(self._layout.count())
            if isinstance(self._layout.itemAt(i).widget(), _DomainRow)
        ]

    def clear(self) -> None:
        for row in self.rows():
            self._layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        set_state_text(self, "Домены проверки DNS: пока нет результатов")

    def show_results(self, results: dict | None) -> None:
        self.clear()
        domains = dict((results or {}).get("domains") or {})
        ordered = sorted(domains.items(), key=lambda pair: (_ORDER.get(str(pair[1].get("state") or ""), 9), pair[0]))
        for order, (host, item) in enumerate(ordered):
            row = _DomainRow(host, dict(item or {}), self)
            self._layout.addWidget(row)
            float_in(row, delay_ms=min(order, 12) * 60)
        spoofed = sum(1 for row in self.rows() if row.state == "spoofed")
        set_state_text(self, f"Домены проверки DNS: {len(ordered)}, подменено {spoofed}")


__all__ = ["DnsDomainsView", "DnsSummaryPanel", "summarize_dns_results"]
