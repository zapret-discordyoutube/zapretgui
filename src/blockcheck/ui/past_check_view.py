"""Прошлая проверка BlockCheck на всю страницу: путь «BlockCheck → проверка», её итог и карточки.

Страница показывает тот же итог и те же карточки, что были сразу после
проверки: отчёт каждого прогона программа сохраняет рядом с его журналом.
Если файл отчёта не сохранился (старая запись, журнал удалён), показывается
то, что записано в самой истории: какие сайты открывались и список проблем.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BreadcrumbBar, CaptionLabel, FluentIcon, PushButton

from blockcheck.ui.check_results import ActionHandler, BlockcheckSummaryPanel
from blockcheck.ui.result_cards import ResultCardsView
from ui.accessibility import set_breadcrumb_accessibility, set_control_accessibility, set_state_text
from ui.fluent_widgets import set_tooltip
from ui.widgets.tone_group import mute

_NOT_SAVED = (
    "Полный отчёт этой проверки не сохранился — показано то, что записано в истории: "
    "какие сайты открывались и список проблем."
)


_NO_TEXT = "Текст этой проверки не сохранялся: она сделана до того, как текст стали класть в отчёт."


def report_from_history(run: dict) -> dict:
    """Отчёт для показа из записи истории, когда файла отчёта нет."""
    level = str(run.get("level") or "unknown")
    return {
        "problems": [
            {"level": level, "text": str(text), "kind": "other", "title": "", "target": "", "advice": [], "evidence": [], "action": ""}
            for text in run.get("problems") or ()
        ],
        "working": [name for name, state in (run.get("states") or {}).items() if state in ("ok", "warn")],
        "services": [
            {"key": str(name), "label": str(name), "level": str(state), "kind": "", "targets": []}
            for name, state in (run.get("states") or {}).items()
        ],
    }


class PastCheckView(QWidget):
    """Одна прошлая проверка: строка пути, итог и карточки."""

    closed = pyqtSignal()
    # Нажали карточку или находку прошлой проверки: её страница.
    card_opened = pyqtSignal(object)
    # Просят открыть текст отчёта той проверки страницей-редактором: (название, текст).
    text_opened = pyqtSignal(str, str)
    ROOT_KEY = "blockcheck"
    RUN_KEY = "run"

    def __init__(self, on_action: ActionHandler | None = None, parent=None) -> None:
        super().__init__(parent)
        self._title = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.breadcrumb = BreadcrumbBar(self)
        self.breadcrumb.currentItemChanged.connect(self._on_breadcrumb)
        layout.addWidget(self.breadcrumb)
        self.note_label = mute(CaptionLabel(_NOT_SAVED, self))
        self.note_label.setWordWrap(True)
        self.note_label.setVisible(False)
        layout.addWidget(self.note_label)
        self.summary = BlockcheckSummaryPanel(
            on_action=on_action, parent=self, on_open=self._open_card_by_key, on_open_page=self.card_opened.emit
        )
        layout.addWidget(self.summary)
        # Текст той проверки лежит в её отчёте; у проверок, сохранённых раньше, его нет.
        self._text = ""
        self.report_button = PushButton(FluentIcon.DOCUMENT, "Отчёт", self)
        self.report_button.clicked.connect(lambda _checked=False: self.text_opened.emit(f"Отчёт: {self._title}", self._text))
        self.summary.actions.addWidget(self.report_button)
        self.summary.actions.addStretch(1)
        self.cards = ResultCardsView(self)
        self.cards.opened.connect(self.card_opened)
        layout.addWidget(self.cards)
        layout.addStretch(1)

    def title(self) -> str:
        return self._title

    def show_run(self, run: dict, report: dict | None) -> None:
        """``report`` — сохранённый отчёт прогона; ``None`` — файла нет, показываем запись истории."""
        from diagnostics.history import format_time

        title = " · ".join(part for part in (format_time(str(run.get("time") or "")), str(run.get("title") or "")) if part)
        self.breadcrumb.blockSignals(True)
        try:
            self.breadcrumb.clear()
            self.breadcrumb.addItem(self.ROOT_KEY, "BlockCheck")
            self._title = f"Проверка {title}"
            self.breadcrumb.addItem(self.RUN_KEY, self._title)
            set_breadcrumb_accessibility(self.breadcrumb, ["BlockCheck", self._title])
        finally:
            self.breadcrumb.blockSignals(False)
        self.note_label.setVisible(report is None)
        shown = report if report is not None else report_from_history(run)
        self.summary.show_report(shown)
        self.cards.show_report(shown)
        lines = (report or {}).get("text")
        self._text = "\n".join(str(line) for line in lines) if isinstance(lines, (list, tuple)) else ""
        self.report_button.setEnabled(bool(self._text))
        hint = "Открыть полный текст той проверки." if self._text else _NO_TEXT
        set_tooltip(self.report_button, hint)
        set_control_accessibility(self.report_button, name="Отчёт прошлой проверки", description=hint)
        set_state_text(self, f"Прошлая проверка {title}: {self.summary.title_label.text()}")

    def _open_card_by_key(self, key: str) -> None:
        widget = self.cards.card(key)
        if widget is not None:
            self.card_opened.emit(widget.card)

    def _on_breadcrumb(self, key: str) -> None:
        if key == self.ROOT_KEY:
            self.closed.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.closed.emit()
            return
        super().keyPressEvent(event)


__all__ = ["PastCheckView", "report_from_history"]
