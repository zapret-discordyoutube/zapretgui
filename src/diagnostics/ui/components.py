"""Компоненты страницы диагностики соединений."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, SimpleCardWidget, StrongBodyLabel, TextEdit

from ui.accessibility import set_state_text
from ui.fluent_widgets import SemanticNotice
from ui.smooth_scroll import apply_editor_smooth_scroll_preference
from ui.theme import get_cached_qta_pixmap
from ui.theme_refresh import ThemeRefreshBinding


_STATUS_MARKERS = (
    "🔄",
    "✅",
    "⏹",
    "⚠",
    "❌",
    "❔",
    "ℹ",
    "🚀",
    "🌐",
    "🎮",
    "🎬",
    "🎉",
    "💡",
    "📦",
    "📋",
    "📁",
    "👉",
    "️",
)


def clean_connection_status_text(text: object) -> str:
    value = " ".join(str(text or "").strip().split())
    for marker in _STATUS_MARKERS:
        value = value.replace(marker, "")
    return " ".join(value.split())


class ScrollBlockingConnectionTextEdit(TextEdit):
    """TextEdit, который не прокручивает родительскую страницу колесом мыши."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("noDrag", True)
        apply_editor_smooth_scroll_preference(self)

    def wheelEvent(self, event):
        scrollbar = self.verticalScrollBar()
        delta = event.angleDelta().y()
        if delta > 0 and scrollbar.value() == scrollbar.minimum():
            event.accept()
            return
        if delta < 0 and scrollbar.value() == scrollbar.maximum():
            event.accept()
            return
        super().wheelEvent(event)
        event.accept()


# Уровень итога → (значок, семантический тон).
_LEVEL_ICONS = {
    "ok": ("fa5s.check-circle", "success"),
    "warn": ("fa5s.exclamation-triangle", "warning"),
    "fail": ("fa5s.times-circle", "error"),
    "unknown": ("fa5s.question-circle", "muted"),
    "pending": ("fa5s.hourglass-half", "muted"),
    "idle": ("fa5s.stethoscope", "muted"),
}
_LEVEL_WORDS = {
    "ok": "работает",
    "warn": "есть проблемы",
    "fail": "не работает",
    "unknown": "неизвестно",
    "pending": "проверяется",
    "idle": "не проверялся",
}


def _tone_color(tone: str, tokens=None) -> str:
    try:
        from ui.theme_semantic import get_semantic_palette

        palette = get_semantic_palette(getattr(tokens, "theme_name", None))
        return {
            "success": palette.success_text,
            "warning": palette.warning_text,
            "error": palette.error_text,
        }.get(tone, "")
    except Exception:
        return {"success": "#6ccb5f", "warning": "#ff9800", "error": "#ff6b6b"}.get(tone, "")


class ServiceResultCard(SimpleCardWidget):
    """Итог по одному сервису: значок, одна фраза, что делать, по частям."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._label = str(label or "")
        self._level = "idle"

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(12)
        self._icon = QLabel(self)
        self._icon.setFixedSize(28, 28)
        header.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        self._title = StrongBodyLabel(self._label, self)
        self._headline = BodyLabel("", self)
        self._headline.setWordWrap(True)
        titles.addWidget(self._title)
        titles.addWidget(self._headline)
        header.addLayout(titles, 1)
        root.addLayout(header)

        self._dns_notice = SemanticNotice("", tone="warning", parent=self)
        self._dns_notice.setVisible(False)
        root.addWidget(self._dns_notice)

        self._advice_host = QWidget(self)
        self._advice_layout = QVBoxLayout(self._advice_host)
        self._advice_layout.setContentsMargins(40, 0, 0, 0)
        self._advice_layout.setSpacing(4)
        root.addWidget(self._advice_host)

        self._parts_host = QWidget(self)
        self._parts_layout = QVBoxLayout(self._parts_host)
        self._parts_layout.setContentsMargins(40, 4, 0, 0)
        self._parts_layout.setSpacing(2)
        root.addWidget(self._parts_host)

        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self.set_idle()

    @property
    def level(self) -> str:
        return self._level

    # Текст с переносом строк не передаёт свою высоту через вложенные области
    # прокрутки (страница диагностики встроена во вкладки BlockCheck): на
    # Windows карточке доставалось меньше места, и строки наезжали друг на
    # друга. Поэтому карточка сама держит высоту, нужную тексту при её ширине.
    def _sync_min_height(self) -> None:
        layout = self.layout()
        if layout is None or self.width() <= 0:
            return
        needed = layout.totalHeightForWidth(self.width()) if layout.hasHeightForWidth() else -1
        if needed <= 0:
            needed = layout.totalSizeHint().height()
        if needed != self.minimumHeight():
            self.setMinimumHeight(needed)

    def _schedule_min_height_sync(self) -> None:
        self._sync_min_height()
        QTimer.singleShot(0, self._sync_min_height)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_min_height()

    def headline(self) -> str:
        return self._headline.text()

    def _clear_details(self) -> None:
        for host, layout in ((self._advice_host, self._advice_layout), (self._parts_host, self._parts_layout)):
            self._clear(layout)
            host.setVisible(False)
        self._dns_notice.setVisible(False)

    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _set_level(self, level: str, headline: str) -> None:
        self._level = level if level in _LEVEL_ICONS else "unknown"
        self._headline.setText(headline)
        self._apply_theme_refresh()
        set_state_text(self, f"{self._label}: {_LEVEL_WORDS[self._level]}. {headline}")
        self._schedule_min_height_sync()

    def set_idle(self) -> None:
        self._clear_details()
        self._set_level("idle", "Ещё не проверялся.")

    def set_pending(self) -> None:
        self._clear_details()
        self._set_level("pending", "Проверяем…")

    def set_report(self, report: dict) -> None:
        self._clear_details()

        dns_note = str(report.get("dns_note") or "")
        self._dns_notice.setText(dns_note)
        self._dns_notice.setVisible(bool(dns_note))

        for advice in report.get("advice") or ():
            label = BodyLabel(f"→ {advice}", self._advice_host)
            label.setWordWrap(True)
            self._advice_layout.addWidget(label)
        self._advice_host.setVisible(self._advice_layout.count() > 0)

        for target in report.get("targets") or ():
            mark = "✓" if target.get("ok") else "✗"
            purpose = str(target.get("purpose") or "")
            text = str(target.get("short") or target.get("text") or "")
            line = CaptionLabel(f"{mark} {purpose[:1].upper()}{purpose[1:]}: {text}", self._parts_host)
            line.setWordWrap(True)
            line.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            line.setToolTip(f"{target.get('host') or ''}\n{target.get('text') or ''}".strip())
            self._parts_layout.addWidget(line)
        self._parts_host.setVisible(self._parts_layout.count() > 0)

        self._set_level(str(report.get("level") or "unknown"), str(report.get("headline") or ""))

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        icon_name, tone = _LEVEL_ICONS.get(self._level, _LEVEL_ICONS["unknown"])
        color = _tone_color(tone, tokens) or None
        try:
            self._icon.setPixmap(
                get_cached_qta_pixmap(icon_name, color=color, size=24, muted_fallback=color is None)
            )
        except Exception:
            pass
        # Цвет — через setTextColor: стиль Fluent-меток задаётся им, а пустой
        # setStyleSheet сбрасывал текст в чёрный на тёмной теме.
        if color:
            self._headline.setTextColor(QColor(color), QColor(color))
        else:
            self._headline.setTextColor(QColor(0, 0, 0), QColor(255, 255, 255))


class ConnectionResultsPanel(QWidget):
    """Карточки итогов по сервисам. Главный элемент страницы."""

    SERVICES = (("discord", "Discord"), ("youtube", "YouTube"))

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.cards: dict[str, ServiceResultCard] = {}
        for key, label in self.SERVICES:
            card = ServiceResultCard(label, self)
            self.cards[key] = card
            layout.addWidget(card)

    def reset(self) -> None:
        for card in self.cards.values():
            card.setVisible(True)
            card.set_idle()

    def set_pending(self, test_type: str) -> None:
        for key, card in self.cards.items():
            selected = test_type in ("all", key)
            card.setVisible(selected)
            if selected:
                card.set_pending()

    def show_report(self, report: dict) -> None:
        for item in report.get("services") or ():
            card = self.cards.get(str(item.get("key") or ""))
            if card is not None:
                card.setVisible(True)
                card.set_report(item)

    def mark_unfinished(self, headline: str) -> None:
        """После «Стопа» или сбоя карточки «Проверяем…» не должны висеть."""
        for card in self.cards.values():
            if card.level == "pending":
                card._set_level("unknown", headline)
