"""Вкладка «Список сайтов» страницы профиля: свои записи и встроенная база.

Вкладка только расставляет поля. Загрузку, проверку и автосохранение списка
ведёт страница профиля со своим контроллером (profile_list_file_editor_controller).
"""

from __future__ import annotations

from PyQt6.QtWidgets import QBoxLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel

from ui.accessibility import set_control_accessibility, set_state_text
from ui.code_editor.chunked_fill import ChunkedReadOnlyFill
from ui.code_editor.editor import CodeEditor
from ui.code_editor.find_bar import FindReplaceBar
from ui.code_editor.find_controller import FindController
from ui.code_editor.syntax import ListFileSyntaxHighlighter
from ui.fluent_widgets import set_tooltip


# С этой ширины свои записи и база стоят рядом, а не друг под другом.
SIDE_BY_SIDE_MIN_WIDTH = 760

USER_TITLE = "Ваши записи"
BASE_TITLE = "Встроенные записи (только просмотр)"


def titled(title: str, display_path: str) -> str:
    """Подпись над полем: что это и в каком файле лежит."""
    display_path = str(display_path or "").strip()
    return f"{title}: {display_path}" if display_path else title


class ProfileListFileTab(QWidget):
    """Два поля списка: слева то, что человек меняет, справа база программы."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._panes_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self._panes_layout.setContentsMargins(0, 0, 0, 0)
        self._panes_layout.setSpacing(12)
        layout.addLayout(self._panes_layout, 1)

        # ---- свои записи: ради них сюда приходят, поэтому они первые ----
        self.user_pane = QWidget(self)
        user_layout = QVBoxLayout(self.user_pane)
        user_layout.setContentsMargins(0, 0, 0, 0)
        user_layout.setSpacing(6)

        self.user_title = CaptionLabel(USER_TITLE, self.user_pane)
        self.user_title.setWordWrap(True)
        user_layout.addWidget(self.user_title)

        self.find_bar = FindReplaceBar(self.user_pane)
        self.find_bar.setVisible(False)
        user_layout.addWidget(self.find_bar)

        self.user_text = CodeEditor(
            highlighter_factory=lambda document: ListFileSyntaxHighlighter(document),
        )
        self.find_controller = FindController(self.user_text, self.find_bar, parent=self)
        self.user_text.setMinimumHeight(220)
        self.user_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        set_tooltip(
            self.user_text,
            "Ваша часть списка. Сохраняется сама в lists/user и добавляется к встроенным записям.",
        )
        set_control_accessibility(
            self.user_text,
            name="Ваши записи списка профиля",
            description=(
                "Ваша часть списка. Эти строки можно менять, они сохраняются сами. "
                "Ctrl+F — поиск, Ctrl+H — замена, Ctrl+G — переход к строке."
            ),
        )
        set_state_text(self.user_text, "Ваши записи списка профиля")
        user_layout.addWidget(self.user_text, 1)
        self._panes_layout.addWidget(self.user_pane, 3)

        # ---- встроенная база: обновляется программой, показана для справки ----
        self.base_pane = QWidget(self)
        base_layout = QVBoxLayout(self.base_pane)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.setSpacing(6)

        self.base_title = CaptionLabel(BASE_TITLE, self.base_pane)
        self.base_title.setWordWrap(True)
        base_layout.addWidget(self.base_title)

        self.base_text = CodeEditor(
            highlighter_factory=lambda document: ListFileSyntaxHighlighter(document),
        )
        self.base_text.setReadOnly(True)
        # Системная база бывает на сто тысяч строк: целиком за один вызов она
        # подвешивала окно на секунды, поэтому дописывается порциями.
        self.base_fill = ChunkedReadOnlyFill(self.base_text)
        self.base_text.setMinimumHeight(140)
        self.base_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        set_tooltip(
            self.base_text,
            "Встроенная часть списка. Она обновляется программой и показана только для просмотра.",
        )
        set_control_accessibility(
            self.base_text,
            name="Встроенные записи списка профиля",
            description="Встроенная часть списка. Она обновляется программой и доступна только для чтения.",
        )
        set_state_text(self.base_text, "Встроенные записи списка профиля")
        base_layout.addWidget(self.base_text, 1)
        self._panes_layout.addWidget(self.base_pane, 2)

        self.error_label = CaptionLabel("", self)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        status_row = QWidget(self)
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(12)
        # Кнопки «Сохранить» нет: правильный текст сохраняется сам, результат
        # показывает эта строка.
        self.status_label = CaptionLabel("Загрузка файла списка...", status_row)
        set_state_text(self.status_label, "Статус списка профиля: Загрузка файла списка...")
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label, 1)
        layout.addWidget(status_row)

    def side_by_side(self) -> bool:
        return self._panes_layout.direction() == QBoxLayout.Direction.LeftToRight

    def _sync_panes_direction(self) -> None:
        direction = (
            QBoxLayout.Direction.LeftToRight
            if self.width() >= SIDE_BY_SIDE_MIN_WIDTH
            else QBoxLayout.Direction.TopToBottom
        )
        if self._panes_layout.direction() != direction:
            self._panes_layout.setDirection(direction)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._sync_panes_direction()


__all__ = [
    "BASE_TITLE",
    "SIDE_BY_SIDE_MIN_WIDTH",
    "USER_TITLE",
    "ProfileListFileTab",
    "titled",
]
