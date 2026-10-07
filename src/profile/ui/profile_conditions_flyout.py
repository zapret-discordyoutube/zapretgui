"""Всплывающая панель «Условия» на странице profile.

Панель открывается над страницей по кнопке в шапке и держит редко нужные поля:
файл списка и диапазоны пакетов. Сами поля создаёт и слушает страница — панель
только раскладывает их, подписывает словами и показывает с анимацией.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, Flyout, FlyoutAnimationType, FlyoutViewBase, StrongBodyLabel

from profile.conditions_text import range_hint


PANEL_WIDTH = 440
RANGE_VALUE_WIDTH = 96
_VALUE_SLIDE_MS = 160
# Щелчок по кнопке при открытой панели сначала закрывает её (щелчок мимо
# всплывающего окна), а потом доходит до кнопки: без паузы панель открылась бы снова.
_REOPEN_GUARD_SECONDS = 0.25


def _muted_caption(text: str = "") -> CaptionLabel:
    label = CaptionLabel(text)
    label.setWordWrap(True)
    label.setTextColor(QColor(0, 0, 0, 150), QColor(255, 255, 255, 150))
    return label


class _RangeRow:
    """Строка диапазона: режим, число и пояснение словами под ними."""

    def __init__(self, combo, value_edit, hint: CaptionLabel) -> None:
        self.combo = combo
        self.value_edit = value_edit
        self.hint = hint
        self._animation = QPropertyAnimation(value_edit, b"maximumWidth", value_edit)
        self._animation.setDuration(_VALUE_SLIDE_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._on_animation_finished)
        combo.currentIndexChanged.connect(lambda _index: self.sync(animated=True))
        value_edit.textChanged.connect(lambda _text: self._sync_hint())

    def _mode(self) -> str:
        return str(self.combo.itemData(self.combo.currentIndex()) or "").strip()

    def _sync_hint(self) -> None:
        self.hint.setText(range_hint(self._mode(), self.value_edit.text()))

    def _on_animation_finished(self) -> None:
        if self.value_edit.maximumWidth() == 0:
            self.value_edit.hide()

    def sync(self, *, animated: bool) -> None:
        """Поле числа нужно только режимам с числом: для «всегда» и «никогда» оно уезжает."""
        self._sync_hint()
        wanted = self._mode() not in {"a", "x"}
        target = RANGE_VALUE_WIDTH if wanted else 0
        self._animation.stop()
        if not animated or not self.value_edit.window().isVisible():
            self.value_edit.setMaximumWidth(target)
            self.value_edit.setVisible(wanted)
            return
        if wanted:
            self.value_edit.show()
        self._animation.setStartValue(self.value_edit.maximumWidth() if self.value_edit.isVisible() else 0)
        self._animation.setEndValue(target)
        self._animation.start()


class ProfileConditionsView(FlyoutViewBase):
    """Содержимое панели: раздел списка сайтов и два раздела диапазонов."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(PANEL_WIDTH)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 16, 20, 18)
        self._layout.setSpacing(14)

        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(StrongBodyLabel("Условия профиля"))
        head.addWidget(_muted_caption("Для каких сайтов и на каких пакетах работает профиль. Изменения сохраняются сразу."))
        self._layout.addLayout(head)

        self._filter_section: QWidget | None = None
        self._range_sections: list[QWidget] = []
        self._range_rows: list[_RangeRow] = []

    def _section(self, title_label, option_text: str = "") -> tuple[QWidget, QVBoxLayout]:
        section = QWidget(self)
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(title_label, 1)
        if option_text:
            # Имя ключа движка остаётся на виду: по нему строку легко найти в тексте пресета.
            title_row.addWidget(_muted_caption(option_text), 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(title_row)
        self._layout.addWidget(section)
        return section, layout

    def add_filter_section(self, title_label, combo, value_edit) -> None:
        section, layout = self._section(title_label)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(combo)
        row.addWidget(value_edit, 1)
        layout.addLayout(row)
        layout.addWidget(_muted_caption("Профиль срабатывает только для адресов из этого файла."))
        self._filter_section = section

    def add_range_section(self, title_label, option_text: str, combo, value_edit) -> None:
        section, layout = self._section(title_label, option_text)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(combo, 1)
        value_edit.setMaximumWidth(RANGE_VALUE_WIDTH)
        row.addWidget(value_edit)
        layout.addLayout(row)
        hint = _muted_caption()
        layout.addWidget(hint)
        self._range_sections.append(section)
        self._range_rows.append(_RangeRow(combo, value_edit, hint))

    def set_sections_visible(self, *, filter_visible: bool, ranges_visible: bool) -> None:
        if self._filter_section is not None:
            self._filter_section.setVisible(bool(filter_visible))
        for section in self._range_sections:
            section.setVisible(bool(ranges_visible))

    def sync_ranges(self) -> None:
        """Вызывается после загрузки profile: значения выставлены без участия пользователя."""
        for row in self._range_rows:
            row.sync(animated=False)


class ProfileConditionsFlyout(Flyout):
    """Панель условий: живёт вместе со страницей и открывается под кнопкой."""

    def __init__(self, view: ProfileConditionsView, parent=None) -> None:
        # Поля панели принадлежат странице, поэтому при закрытии её не удаляем.
        super().__init__(view, parent, isDeleteOnClose=False)
        self._closed_at = 0.0
        self.closed.connect(self._remember_close_time)

    def _remember_close_time(self) -> None:
        self._closed_at = time.monotonic()

    def toggle_under(self, button: QWidget) -> None:
        if self.isVisible():
            self.fadeOut()
            return
        if time.monotonic() - self._closed_at < _REOPEN_GUARD_SECONDS:
            return
        # Тень рисуется под текущую тему: панель переживает смену темы.
        self.setShadowEffect()
        self.setWindowOpacity(0)
        self.adjustSize()
        margins = self.layout().contentsMargins()
        # Правый край панели — под правым краем кнопки: так панель не уезжает за окно программы.
        anchor = button.mapToGlobal(QPoint(button.width(), button.height()))
        x = anchor.x() - self.sizeHint().width() + margins.right()
        y = anchor.y() - margins.top() + 10
        self.exec(QPoint(x, y), FlyoutAnimationType.DROP_DOWN)
