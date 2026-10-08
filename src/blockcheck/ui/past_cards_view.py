"""Прошлая проверка любой вкладки на всю страницу: путь, одна фраза итога и те же карточки, что у свежей.

Главная вкладка BlockCheck показывает прошлую проверку своим видом
(``past_check_view``: итог с группами проблем). Остальным вкладкам — «Проверке
домена» и подобным — хватает карточек: их и показывает эта страница. Сырой
текст проверки открывается только кнопкой «Отчёт».
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, BreadcrumbBar, FluentIcon, PushButton, SubtitleLabel

from blockcheck.ui.result_cards import CardsGrid
from ui.accessibility import set_breadcrumb_accessibility, set_control_accessibility, set_state_text
from ui.fluent_widgets import set_tooltip
from ui.widgets.tone_group import mute

_NO_TEXT = "Текст этой проверки не сохранился."


class PastCardsView(QWidget):
    """Одна прошлая проверка: строка пути, название, итог одной фразой и карточки."""

    closed = pyqtSignal()
    # Нажали карточку: её подробности страницей.
    card_opened = pyqtSignal(object)
    # Просят открыть текст той проверки страницей-редактором: (название, текст).
    text_opened = pyqtSignal(str, str)
    ROOT_KEY = "root"
    RUN_KEY = "run"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._title = ""
        self._text = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.breadcrumb = BreadcrumbBar(self)
        self.breadcrumb.currentItemChanged.connect(self._on_breadcrumb)
        layout.addWidget(self.breadcrumb)

        header = QHBoxLayout()
        header.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = SubtitleLabel("", self)
        titles.addWidget(self.title_label)
        self.headline_label = mute(BodyLabel("", self))
        self.headline_label.setWordWrap(True)
        titles.addWidget(self.headline_label)
        header.addLayout(titles, 1)
        self.report_button = PushButton(FluentIcon.DOCUMENT, "Отчёт", self)
        self.report_button.clicked.connect(lambda _checked=False: self.text_opened.emit(f"Отчёт: {self._title}", self._text))
        header.addWidget(self.report_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.cards = CardsGrid(300, self)
        self.cards.opened.connect(self.card_opened)
        layout.addWidget(self.cards)
        # Вместо карточек — готовый вид вкладки (итог и её собственные карточки), см. show_content.
        self._content: QWidget | None = None
        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._content_layout)
        layout.addStretch(1)

    def title(self) -> str:
        return self._title

    def content(self) -> QWidget | None:
        return self._content

    def show_content(self, title: str, headline: str, widget: QWidget, text: str = "", *, root_title: str = "BlockCheck") -> None:
        """Прошлая проверка тем же видом, что у её вкладки: ``widget`` собирает сама вкладка по сохранённому отчёту."""
        self.show_run(title, headline, [], text, root_title=root_title)
        self.cards.setVisible(False)
        self._content = widget
        self._content_layout.addWidget(widget)
        widget.show()

    def show_run(self, title: str, headline: str, cards: list, text: str = "", *, root_title: str = "BlockCheck") -> None:
        """``cards`` — карточки той проверки; ``text`` — её полный текст для кнопки «Отчёт»."""
        if self._content is not None:
            self._content_layout.removeWidget(self._content)
            self._content.setParent(None)
            self._content.deleteLater()
            self._content = None
        self.cards.setVisible(True)
        self._title = str(title)
        self._text = str(text or "")
        self.breadcrumb.blockSignals(True)
        try:
            self.breadcrumb.clear()
            self.breadcrumb.addItem(self.ROOT_KEY, root_title)
            self.breadcrumb.addItem(self.RUN_KEY, self._title)
            set_breadcrumb_accessibility(self.breadcrumb, [root_title, self._title])
        finally:
            self.breadcrumb.blockSignals(False)
        self.title_label.setText(self._title)
        self.headline_label.setText(str(headline or ""))
        self.headline_label.setVisible(bool(headline))
        self.cards.show_cards(list(cards), animate=True)
        self.report_button.setEnabled(bool(self._text))
        hint = "Открыть полный текст той проверки." if self._text else _NO_TEXT
        set_tooltip(self.report_button, hint)
        set_control_accessibility(self.report_button, name="Отчёт прошлой проверки", description=hint)
        set_state_text(self, f"Прошлая проверка {self._title}: {headline}")

    def _on_breadcrumb(self, key: str) -> None:
        if key == self.ROOT_KEY:
            self.closed.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.closed.emit()
            return
        super().keyPressEvent(event)


__all__ = ["PastCardsView"]
