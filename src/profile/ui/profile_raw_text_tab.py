"""Вкладка «Текст профиля»: строки профиля так, как они записаны в пресете.

Вкладка только расставляет редактор и кнопку. Проверку и запись текста в
пресет ведёт страница профиля (profile_setup_save_controllers).
"""

from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, PushButton

from ui.accessibility import set_control_accessibility, set_state_text
from ui.code_editor.editor import CodeEditor
from ui.code_editor.find_bar import FindReplaceBar
from ui.code_editor.find_controller import FindController
from ui.code_editor.syntax import PresetSyntaxHighlighter
from ui.fluent_widgets import set_tooltip


HINT_TEXT = (
    "Строки этого профиля в текущем пресете: условия, когда он применяется, и выбранная стратегия. "
    "Правка сохраняется только в этот пресет."
)


class ProfileRawTextTab(QWidget):
    """Редактор текста одного профиля на всю высоту вкладки и кнопка сохранения."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.hint_label = CaptionLabel(HINT_TEXT, self)
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.find_bar = FindReplaceBar(self)
        self.find_bar.setVisible(False)
        layout.addWidget(self.find_bar)

        self.text = CodeEditor(
            highlighter_factory=lambda document: PresetSyntaxHighlighter(document),
        )
        self.find_controller = FindController(self.text, self.find_bar, parent=self)
        self.text.setMinimumHeight(220)
        self.text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        set_tooltip(
            self.text,
            "Текст профиля как в файле пресета. Сохраняется только в текущий пресет и не меняет ваш шаблон профиля.",
        )
        set_control_accessibility(
            self.text,
            name="Текст профиля в текущем пресете",
            description=(
                "Текст профиля как в файле пресета. Сохраняется только в текущий пресет. "
                "Ctrl+F — поиск, Ctrl+H — замена, Ctrl+G — переход к строке, "
                "Ctrl с колесом мыши — масштаб."
            ),
        )
        set_state_text(self.text, "Текст профиля в текущем пресете")
        layout.addWidget(self.text, 1)

        actions = QWidget(self)
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(12)
        self.save_button = PushButton("Сохранить текст профиля", icon=FluentIcon.SAVE)
        set_tooltip(
            self.save_button,
            "Проверяет текст как один профиль и записывает его в текущий пресет.",
        )
        set_control_accessibility(
            self.save_button,
            name="Сохранить текст профиля",
            description="Проверяет текст как один профиль и записывает его в текущий пресет.",
        )
        set_state_text(self.save_button, "Сохранить текст профиля")
        actions_layout.addWidget(self.save_button)
        actions_layout.addStretch(1)
        layout.addWidget(actions)


__all__ = ["HINT_TEXT", "ProfileRawTextTab"]
